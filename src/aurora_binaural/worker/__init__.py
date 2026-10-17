"""Aurora Binaural persistent background Worker (docs/plan.md Sections 10, 11, and 16.4).

Responsibilities:
- Startup and periodic recovery of expired worker leases (`WORKER_MAX_RETRIES = 2`).
- Short transaction job claiming (`claim_next_job`) with unique fencing token (`lease_id`).
- Background lease heartbeat renewal while a job is running.
- Isolated per-attempt temporary directory (`{tmp_dir}/attempt_{job_id}_{attempt}_{lease_id}`).
- Block-boundary cancellation check (`cancel_check`) and timeout enforcement.
- Atomic delivery of verified output audio and JSON report (`os.replace`) inside the finalization
  transaction so retries can never overwrite each other or produce two successful results, and
  write failures leave zero partial files behind.
- Periodic cleanup of expired assets and results without deleting files in active use.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from aurora_binaural.config import ServiceConfig
from aurora_binaural.engine import Renderer, create_renderer
from aurora_binaural.exit_codes import AuroraError, OperationCanceledError, ProcessingError
from aurora_binaural.media import atomic_write_bytes, log_stderr
from aurora_binaural.pipeline import convert_audio
from aurora_binaural.previews import render_preview
from aurora_binaural.storage import JobRecord, TaskStore, format_utc_iso

WORKER_MAX_RETRIES = 2


class AuroraWorker:
    """Background worker that claims and executes rendering jobs from `TaskStore`."""

    def __init__(
        self,
        config: ServiceConfig,
        *,
        store: TaskStore | None = None,
        worker_id: str | None = None,
        renderer_factory: type[Renderer] | None = None,
    ) -> None:
        self.config = config
        self.config.ensure_directories()
        self.store = store or TaskStore(config.db_path)
        self.worker_id = worker_id or f"worker_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self.renderer_factory = renderer_factory
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def recover_and_cleanup(self, *, now: float | None = None) -> tuple[list[str], list[str], list[str]]:
        """Recover expired leases and clean up expired assets/results."""
        recovered = self.store.recover_expired_leases(now=now)
        if recovered:
            log_stderr(f"[{self.worker_id}] Reclaimed {len(recovered)} expired job lease(s): {recovered}")
        self.store.cleanup_attempt_directories(self.config.tmp_dir)
        cleaned_jobs, cleaned_assets = self.store.cleanup_expired_resources(now=now)
        return recovered, cleaned_jobs, cleaned_assets

    def register_heartbeat(self, *, now: float | None = None) -> None:
        """Write or update this worker's heartbeat in SQLite."""
        self.store.upsert_worker_heartbeat(
            self.worker_id,
            pid=os.getpid(),
            concurrency=self.config.worker_concurrency,
            lease_duration_sec=self.config.lease_duration_sec,
            now=now,
        )

    def execute_claimed_job(self, job: JobRecord) -> JobRecord | None:
        """Execute a single claimed job in an isolated temporary directory."""
        assert job.lease_id is not None
        lease_id = job.lease_id
        attempt_dir = (
            self.config.tmp_dir / f"attempt_{job.job_id}_{job.attempts}_{lease_id}"
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)

        progress_value = 0.0
        hb_stop = threading.Event()
        lease_lost_flag = threading.Event()
        cancel_seen_flag = threading.Event()

        def _lease_heartbeat_loop() -> None:
            interval = min(self.config.heartbeat_interval_sec, max(0.02, self.config.lease_duration_sec / 3.0))
            while not hb_stop.wait(timeout=interval):
                try:
                    owned, cancel_req = self.store.renew_lease(
                        job.job_id,
                        lease_id,
                        lease_duration_sec=self.config.lease_duration_sec,
                        progress=progress_value,
                    )
                    self.register_heartbeat()
                    if not owned:
                        lease_lost_flag.set()
                        return
                    if cancel_req:
                        cancel_seen_flag.set()
                except Exception:
                    pass

        hb_thread = threading.Thread(
            target=_lease_heartbeat_loop,
            name=f"lease-hb-{job.job_id}",
            daemon=True,
        )
        hb_thread.start()

        def _cleanup_attempt() -> None:
            hb_stop.set()
            hb_thread.join(timeout=1.0)
            shutil.rmtree(attempt_dir, ignore_errors=True)

        def _finish_canceled() -> JobRecord | None:
            # A visible canceled state promises that this attempt's files are gone.
            # Publishing it before the finally block allowed API observers to race cleanup.
            _cleanup_attempt()
            return self.store.finalize_job_canceled(job.job_id, lease_id)

        t_start = time.monotonic()

        def _cancel_check() -> bool:
            if self.config.worker_block_delay_ms > 0.0:
                time.sleep(self.config.worker_block_delay_ms / 1000.0)
            if (time.monotonic() - t_start) > self.config.job_timeout_sec:
                raise ProcessingError(
                    f"Job '{job.job_id}' exceeded timeout of {self.config.job_timeout_sec}s.",
                    error_code="JOB_TIMEOUT_EXCEEDED",
                    retryable=False,
                )
            if self._stop_event.is_set():
                raise OperationCanceledError("Worker shutting down.")
            if lease_lost_flag.is_set() or cancel_seen_flag.is_set():
                return True
            cancel_req, lost = self.store.check_attempt_aborted(job.job_id, lease_id)
            if lost:
                lease_lost_flag.set()
                return True
            if cancel_req:
                cancel_seen_flag.set()
                return True
            return False

        try:
            asset = self.store.get_asset(job.asset_id)
            if asset is None or not asset.file_path.exists():
                _cleanup_attempt()
                return self.store.finalize_job_failed(
                    job.job_id,
                    lease_id,
                    error_code="ASSET_NOT_FOUND",
                    message=f"Input asset '{job.asset_id}' is missing on storage.",
                    retryable=False,
                )

            fmt_ext = job.params.output.extension
            staged_out_path = attempt_dir / f"staged_out.{fmt_ext}"
            staged_report_path = attempt_dir / "staged_report.json"

            renderer_instance = (
                self.renderer_factory() if self.renderer_factory is not None
                else create_renderer(engine=self.config.engine, hrtf_dir=self.config.hrtf_dir)
            )
            staged_original_path = staged_out_path.with_suffix(".original.wav")

            def _progress(value: float) -> None:
                nonlocal progress_value
                progress_value = value

            if job.params.preview is not None:
                report = render_preview(
                    asset.file_path, staged_out_path, staged_original_path, job.params,
                    renderer=renderer_instance, cancel_check=_cancel_check, progress=_progress,
                )
            else:
                report = convert_audio(
                    input_path=asset.file_path,
                    output_path=staged_out_path,
                    params=job.params,
                    overwrite=True,
                    report_path=None,
                    renderer=renderer_instance,
                    strict_ffmpeg=self.config.strict_ffmpeg,
                    cancel_check=_cancel_check,
                    progress=_progress,
                )

            # Re-verify cancellation / lease before committing
            if _cancel_check():
                raise OperationCanceledError("Job canceled at completion boundary.")

            commit_now = time.time()
            expires_epoch = commit_now + self.config.retention_sec
            expires_iso = format_utc_iso(expires_epoch)

            sanitized_in = (asset.probe if job.params.preview is not None else report.input).model_copy(
                update={"path": f"asset://{asset.asset_id}/{asset.filename}"}
            )
            sanitized_out = report.output.model_copy(
                update={"path": f"result://{job.job_id}.{fmt_ext}"}
            )
            preview = report.preview
            if preview is not None:
                preview = preview.model_copy(update={
                    "original": preview.original.model_copy(update={"path": f"result://{job.job_id}.original.wav"})
                })
            final_report_model = report.model_copy(
                update={
                    "preview": preview,
                    "job_id": job.job_id,
                    "asset_id": job.asset_id,
                    "input": sanitized_in,
                    "output": sanitized_out,
                    "attempts": job.attempts,
                    "created_at": datetime.fromtimestamp(commit_now, tz=UTC).isoformat(),
                    "expires_at": expires_iso,
                }
            )
            report_bytes = (final_report_model.model_dump_json(indent=2) + "\n").encode("utf-8")
            atomic_write_bytes(staged_report_path, report_bytes, overwrite=True)

            final_result_path = self.config.results_dir / f"{job.job_id}.{fmt_ext}"
            final_report_path = self.config.results_dir / f"{job.job_id}.report.json"
            final_original_path = final_result_path.with_suffix(".original.wav")

            result_meta: dict[str, Any] = {
                "format": sanitized_out.format,
                "container": sanitized_out.container,
                "codec": sanitized_out.codec,
                "sample_rate": sanitized_out.sample_rate,
                "channels": sanitized_out.channels,
                "samples": sanitized_out.samples,
                "duration_seconds": sanitized_out.duration_seconds,
                "bit_depth": sanitized_out.bit_depth,
                "size_bytes": sanitized_out.size_bytes,
                "sha256": sanitized_out.sha256,
            }

            def _commit_files() -> None:
                try:
                    self.config.results_dir.mkdir(parents=True, exist_ok=True)
                    os.replace(staged_out_path, final_result_path)
                    os.replace(staged_report_path, final_report_path)
                    if job.params.preview is not None:
                        os.replace(staged_original_path, final_original_path)
                except OSError as exc:
                    with contextlib.suppress(OSError):
                        final_original_path.unlink(missing_ok=True)
                    with contextlib.suppress(OSError):
                        final_result_path.unlink(missing_ok=True)
                    with contextlib.suppress(OSError):
                        final_report_path.unlink(missing_ok=True)
                    raise ProcessingError(
                        f"Failed to write output files for job '{job.job_id}': {exc}",
                        error_code="OUTPUT_WRITE_FAILED",
                        retryable=False,
                    ) from exc

            updated_job, committed = self.store.finalize_job_success(
                job.job_id,
                lease_id,
                result_path=final_result_path,
                report_path=final_report_path,
                result_metadata=result_meta,
                retention_sec=self.config.retention_sec,
                commit_files_fn=_commit_files,
                now=commit_now,
            )
            if not committed:
                if cancel_seen_flag.is_set() or (updated_job is not None and updated_job.cancel_requested):
                    return _finish_canceled()
                return updated_job
            return updated_job

        except OperationCanceledError:
            if lease_lost_flag.is_set() and not cancel_seen_flag.is_set():
                # Lease was reclaimed by another worker; do not overwrite the new worker's job status
                return self.store.get_job(job.job_id)
            return _finish_canceled()
        except AuroraError as exc:
            # finalize_job_failed may itself transition a cancel-requested job to canceled.
            _cleanup_attempt()
            return self.store.finalize_job_failed(
                job.job_id,
                lease_id,
                error_code=exc.error_code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
        except OSError as exc:
            _cleanup_attempt()
            return self.store.finalize_job_failed(
                job.job_id,
                lease_id,
                error_code="OUTPUT_WRITE_FAILED",
                message=f"I/O failure while processing job '{job.job_id}': {exc}",
                retryable=False,
            )
        except Exception as exc:
            _cleanup_attempt()
            return self.store.finalize_job_failed(
                job.job_id,
                lease_id,
                error_code="WORKER_INTERNAL_ERROR",
                message=f"Unexpected worker error: {exc}",
                retryable=False,
            )
        finally:
            _cleanup_attempt()

    def run_once(self) -> JobRecord | None:
        """Perform one cycle of recovery, cleanup, heartbeat, and claiming/running at most one job."""
        self.register_heartbeat()
        self.recover_and_cleanup()
        job = self.store.claim_next_job(
            self.worker_id,
            lease_duration_sec=self.config.lease_duration_sec,
        )
        if job is None:
            return None
        log_stderr(f"[{self.worker_id}] Claimed job '{job.job_id}' (attempt {job.attempts}/{1 + job.max_retries})")
        return self.execute_claimed_job(job)

    def serve_forever(self) -> None:
        """Run the worker loop with configurable concurrency until `stop()` is called."""
        self._stop_event.clear()
        self.register_heartbeat()
        self.recover_and_cleanup()
        log_stderr(
            f"[{self.worker_id}] Worker started (concurrency={self.config.worker_concurrency}, "
            f"lease={self.config.lease_duration_sec}s, max_retries={self.config.max_retries})"
        )

        last_cleanup = time.monotonic()
        last_hb = time.monotonic()
        active_futures: set[Future[JobRecord | None]] = set()

        with ThreadPoolExecutor(
            max_workers=self.config.worker_concurrency,
            thread_name_prefix=f"{self.worker_id}-slot",
        ) as pool:
            try:
                while not self._stop_event.is_set():
                    now_mono = time.monotonic()
                    if now_mono - last_hb >= min(self.config.heartbeat_interval_sec, 1.0):
                        self.register_heartbeat()
                        self.store.recover_expired_leases()
                        last_hb = now_mono

                    if now_mono - last_cleanup >= self.config.cleanup_interval_sec:
                        self.recover_and_cleanup()
                        last_cleanup = now_mono

                    done = {f for f in active_futures if f.done()}
                    for f in done:
                        active_futures.discard(f)
                        with contextlib.suppress(Exception):
                            f.result()

                    while (
                        not self._stop_event.is_set()
                        and len(active_futures) < self.config.worker_concurrency
                    ):
                        claimed = self.store.claim_next_job(
                            self.worker_id,
                            lease_duration_sec=self.config.lease_duration_sec,
                        )
                        if claimed is None:
                            break
                        log_stderr(
                            f"[{self.worker_id}] Claimed job '{claimed.job_id}' "
                            f"(attempt {claimed.attempts}/{1 + claimed.max_retries})"
                        )
                        fut = pool.submit(self.execute_claimed_job, claimed)
                        active_futures.add(fut)

                    self._stop_event.wait(timeout=self.config.worker_poll_interval_sec)
            finally:
                for fut in list(active_futures):
                    with contextlib.suppress(Exception):
                        fut.result(timeout=5.0)
                with contextlib.suppress(Exception):
                    self.store.remove_worker_heartbeat(self.worker_id)
                log_stderr(f"[{self.worker_id}] Worker stopped.")

    def start_background(self) -> None:
        """Start `serve_forever` in a daemon background thread (useful for tests and single-process mode)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.register_heartbeat()
        self._thread = threading.Thread(target=self.serve_forever, name=self.worker_id, daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal the worker loop to stop and wait for background thread termination."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None


__all__ = ["WORKER_MAX_RETRIES", "AuroraWorker"]
