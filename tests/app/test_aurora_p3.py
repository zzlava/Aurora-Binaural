"""Comprehensive P3 unit, integration, and fault-injection tests for Aurora Binaural HTTP API & Worker.

Covers docs/tasks/P3-antigravity-01.md Tasks 1–6 and docs/plan.md Sections 10, 11, and 16.4:
1. FastAPI endpoints (`/health`, `/ready`, `/v1/presets`, `/v1/assets`, `/v1/jobs`, `/v1/jobs/{id}`,
   `/v1/jobs/{id}/cancel`, `/v1/jobs/{id}/result`, `/v1/jobs/{id}/report`) & OpenAPI schema export.
2. Bearer API Key authentication and path-leak prevention on `/health` and `/ready`.
3. Streaming asset upload size limit (`413 ASSET_TOO_LARGE`) and duration limit (`422 DURATION_LIMIT_EXCEEDED`).
4. `Idempotency-Key` deduplication (202 same job) and conflict detection (`409 IDEMPOTENCY_KEY_CONFLICT`).
5. Idempotent cancellation (queued, running at block boundary, repeated cancel, and terminal state invariance).
6. Worker killed (`SIGKILL`) mid-flight -> lease expiration recovery -> retry succeeds; max_retries=2 cap.
7. API restart state persistence across process/app recreation.
8. Output write failure (simulated disk full `ENOSPC` / read-only permission error) -> job marked `failed`
   with zero partial files in `results_dir` or `tmp_dir`.
9. Periodic expiration cleanup (protects in-use assets for queued/running jobs) and `410 RESULT_EXPIRED`
   when downloading expired results/reports.
10. Fencing token (`lease_id`) guarantee: concurrent/stale retry attempt cannot produce two successful
    results or overwrite the winning attempt's files.
11. End-to-end CLI remote mode (`aurora convert --server`, `aurora job status`, `aurora job cancel`)
    against a real running FastAPI + Worker server.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import uvicorn
from fastapi.testclient import TestClient

from aurora_binaural.api import create_app, export_openapi_schema
from aurora_binaural.cli import run_cli
from aurora_binaural.cli.remote import RemoteAuroraClient
from aurora_binaural.config import ServiceConfig
from aurora_binaural.exit_codes import ExitCode, RemoteServiceError
from aurora_binaural.media import compute_sha256, probe_audio
from aurora_binaural.params import AuroraParams, InputSpec, OutputFormat, OutputSpec, PresetId, PresetSpec
from aurora_binaural.storage import TaskStore
from aurora_binaural.worker import WORKER_MAX_RETRIES, AuroraWorker


def _write_test_wav(
    path: Path,
    *,
    sample_rate: int = 48000,
    samples: int = 24000,
    channels: int = 2,
    amplitude: float = 0.25,
) -> bytes:
    """Generate a deterministic float32 WAV test file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(samples, dtype=np.float32)
    wave = (amplitude * np.sin(2.0 * math.pi * 440.0 * t / float(sample_rate))).astype(np.float32)
    arr = wave[:, np.newaxis] if channels == 1 else np.column_stack((wave, (-0.8 * wave).astype(np.float32)))
    if samples > 500:
        arr[500, 0] = 0.85
    pcm_bytes = np.ascontiguousarray(arr, dtype=np.float32).tobytes()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:0",
            "-c:a",
            "pcm_s24le",
            str(path),
        ],
        input=pcm_bytes,
        check=True,
    )
    return path.read_bytes()


def _make_test_config(
    tmp_path: Path,
    *,
    api_key: str = "test_secret_api_key_p3",
    max_upload_bytes: int = 50_000_000,
    max_duration_sec: float = 600.0,
    job_timeout_sec: float = 60.0,
    retention_sec: float = 3600.0,
    lease_duration_sec: float = 5.0,
    heartbeat_interval_sec: float = 0.2,
    cleanup_interval_sec: float = 10.0,
    worker_poll_interval_sec: float = 0.05,
    max_retries: int = 2,
    worker_block_delay_ms: float = 0.0,
) -> ServiceConfig:
    data_dir = tmp_path / "runtime"
    repo_hrtf = Path(__file__).resolve().parents[2] / "data" / "hrtf"
    return ServiceConfig(
        api_key=api_key,
        host="127.0.0.1",
        port=0,
        data_dir=data_dir,
        db_path=data_dir / "aurora.db",
        hrtf_dir=repo_hrtf,
        max_upload_bytes=max_upload_bytes,
        max_duration_sec=max_duration_sec,
        job_timeout_sec=job_timeout_sec,
        retention_sec=retention_sec,
        worker_concurrency=1,
        lease_duration_sec=lease_duration_sec,
        heartbeat_interval_sec=heartbeat_interval_sec,
        cleanup_interval_sec=cleanup_interval_sec,
        worker_poll_interval_sec=worker_poll_interval_sec,
        max_retries=max_retries,
        worker_block_delay_ms=worker_block_delay_ms,
    )


def _auth_headers(api_key: str = "test_secret_api_key_p3", extra: dict[str, str] | None = None) -> dict[str, str]:
    hdrs = {"Authorization": f"Bearer {api_key}"}
    if extra:
        hdrs.update(extra)
    return hdrs


@contextlib.contextmanager
def _live_uvicorn_server(config: ServiceConfig, *, start_worker: bool = True) -> Iterator[str]:
    """Run a real Uvicorn HTTP server (and optional background AuroraWorker) on an ephemeral port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    app = create_app(config)
    uv_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(uv_config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    worker: AuroraWorker | None = None
    if start_worker:
        worker = AuroraWorker(config, store=app.state.store)
        worker.start_background()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.02)

    try:
        yield base_url
    finally:
        if worker is not None:
            worker.stop(timeout=3.0)
        server.should_exit = True
        server_thread.join(timeout=3.0)


class TestApiContractAuthAndOpenApi:
    """Tests for Task 1 & Task 3: endpoints, OpenAPI artifact, Bearer auth, and upload limits."""

    def test_openapi_export_and_artifact_parity(self, tmp_path: Path) -> None:
        qa_openapi_path = Path(__file__).resolve().parents[2] / "qa" / "p3" / "antigravity" / "openapi.json"
        assert qa_openapi_path.is_file(), "qa/p3/antigravity/openapi.json must exist"

        generated = export_openapi_schema(tmp_path / "openapi_check.json")
        on_disk = json.loads(qa_openapi_path.read_text(encoding="utf-8"))
        assert generated == on_disk

        expected_paths = {
            "/health",
            "/ready",
            "/v1/presets",
            "/v1/assets",
            "/v1/jobs",
            "/v1/jobs/{id}",
            "/v1/jobs/{id}/cancel",
            "/v1/jobs/{id}/result",
            "/v1/jobs/{id}/report",
        }
        assert expected_paths.issubset(set(on_disk["paths"].keys()))

    def test_health_and_ready_no_internal_paths_and_auth_enforcement(self, tmp_path: Path) -> None:
        cfg = _make_test_config(tmp_path)
        app = create_app(cfg)
        client = TestClient(app)

        # 1. /health requires no auth and exposes zero internal paths
        r_health = client.get("/health")
        assert r_health.status_code == 200
        health_body = r_health.json()
        assert health_body == {
            "ok": True,
            "status": "ok",
            "service": "aurora-binaural-api",
            "api_version": "v1",
        }
        raw_health_str = r_health.text
        assert str(tmp_path) not in raw_health_str
        assert "aurora.db" not in raw_health_str
        assert "/data" not in raw_health_str

        # 2. /ready before worker starts -> 503 not_ready (worker=False, database=True, hrtf_data=True)
        r_ready_before = client.get("/ready")
        assert r_ready_before.status_code == 503
        ready_before_json = r_ready_before.json()
        assert ready_before_json["ok"] is False
        assert ready_before_json["checks"]["database"] is True
        assert ready_before_json["checks"]["hrtf_data"] is True
        assert ready_before_json["checks"]["worker"] is False
        assert str(tmp_path) not in r_ready_before.text

        # Register worker heartbeat -> /ready returns 200 ready
        worker = AuroraWorker(cfg, store=app.state.store)
        worker.register_heartbeat()
        r_ready_after = client.get("/ready")
        assert r_ready_after.status_code == 200
        assert r_ready_after.json()["ok"] is True
        assert r_ready_after.json()["status"] == "ready"

        # Invalid Bearer key on /ready returns 401
        r_ready_bad_key = client.get("/ready", headers={"Authorization": "Bearer wrong_key"})
        assert r_ready_bad_key.status_code == 401
        assert r_ready_bad_key.json()["error"]["code"] == "UNAUTHORIZED"

        # 3. Missing or invalid key on all /v1/* endpoints returns 401 UNAUTHORIZED
        for method, path in [
            ("GET", "/v1/presets"),
            ("POST", "/v1/assets"),
            ("POST", "/v1/jobs"),
            ("GET", "/v1/jobs/job_123"),
            ("POST", "/v1/jobs/job_123/cancel"),
            ("GET", "/v1/jobs/job_123/result"),
            ("GET", "/v1/jobs/job_123/report"),
        ]:
            resp_no_key = client.request(method, path)
            assert resp_no_key.status_code == 401
            assert resp_no_key.json()["error"]["code"] == "UNAUTHORIZED"

            resp_bad_key = client.request(method, path, headers={"Authorization": "Bearer invalid_token"})
            assert resp_bad_key.status_code == 401
            assert resp_bad_key.json()["error"]["code"] == "UNAUTHORIZED"

        # 4. Valid key on /v1/presets returns 200
        r_presets = client.get("/v1/presets", headers=_auth_headers())
        assert r_presets.status_code == 200
        presets_data = r_presets.json()
        assert presets_data["ok"] is True
        assert {p["id"] for p in presets_data["presets"]} == {"room", "surround", "orbit"}

    def test_asset_upload_size_limit_duration_limit_and_checksum(self, tmp_path: Path) -> None:
        in_wav = tmp_path / "sample.wav"
        wav_bytes = _write_test_wav(in_wav, sample_rate=48000, samples=24000)  # 0.5 sec, ~144 KB

        # 1. Enforce max_upload_bytes (set limit smaller than wav_bytes)
        cfg_small = _make_test_config(tmp_path / "small", max_upload_bytes=1024)
        client_small = TestClient(create_app(cfg_small))
        r_too_large = client_small.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream", "X-Asset-Filename": "sample.wav"}),
        )
        assert r_too_large.status_code == 413
        assert r_too_large.json()["error"]["code"] == "ASSET_TOO_LARGE"
        assert list(cfg_small.tmp_dir.iterdir()) == []

        # Also test multipart upload exceeding max_upload_bytes when Content-Length is not trusted
        r_mp_large = client_small.post(
            "/v1/assets",
            files={"file": ("sample.wav", wav_bytes, "audio/wav")},
            headers=_auth_headers(),
        )
        assert r_mp_large.status_code == 413
        assert r_mp_large.json()["error"]["code"] == "ASSET_TOO_LARGE"
        assert list(cfg_small.tmp_dir.iterdir()) == []

        # 2. Enforce max_duration_sec (0.5s audio against 0.2s limit)
        cfg_short_dur = _make_test_config(tmp_path / "short_dur", max_duration_sec=0.2)
        client_short = TestClient(create_app(cfg_short_dur))
        r_too_long = client_short.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream", "X-Asset-Filename": "sample.wav"}),
        )
        assert r_too_long.status_code == 422
        assert r_too_long.json()["error"]["code"] == "DURATION_LIMIT_EXCEEDED"
        assert list(cfg_short_dur.tmp_dir.iterdir()) == []
        assert list(cfg_short_dur.assets_dir.iterdir()) == []

        # 3. Checksum mismatch rejected with 400
        cfg_ok = _make_test_config(tmp_path / "ok")
        client_ok = TestClient(create_app(cfg_ok))
        r_bad_sha = client_ok.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(
                extra={
                    "Content-Type": "application/octet-stream",
                    "X-Asset-Filename": "sample.wav",
                    "X-Asset-Sha256": "0" * 64,
                }
            ),
        )
        assert r_bad_sha.status_code == 400
        assert r_bad_sha.json()["error"]["code"] == "ASSET_CHECKSUM_MISMATCH"

        # 4. Valid multipart and stream uploads succeed
        r_valid = client_ok.post(
            "/v1/assets",
            files={"file": ("sample.wav", wav_bytes, "audio/wav")},
            headers=_auth_headers(),
        )
        assert r_valid.status_code == 201
        asset_data = r_valid.json()
        assert asset_data["ok"] is True
        assert asset_data["asset_id"].startswith("asset_")
        assert asset_data["samples"] == 24000
        assert str(tmp_path) not in r_valid.text


class TestFaultsRecoveryAndInvariants:
    """Tests for Task 2 & Task 6 (docs/plan.md Section 16.4 fault & recovery scenarios)."""

    def test_idempotency_key_same_vs_different_params_409(self, tmp_path: Path) -> None:
        cfg = _make_test_config(tmp_path)
        client = TestClient(create_app(cfg))

        in_wav = tmp_path / "in.wav"
        wav_bytes = _write_test_wav(in_wav, samples=12000)
        r_asset = client.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream", "X-Asset-Filename": "in.wav"}),
        )
        asset_id = r_asset.json()["asset_id"]

        job_payload_1 = {
            "api_version": "v1",
            "mode": "enhance",
            "input": {"asset_id": asset_id},
            "preset": {"id": "room", "version": "1"},
            "strength": 0.4,
            "seed": 20261016,
            "output": {"format": "wav", "sample_rate": 48000, "bit_depth": 24},
            "timing": {"mode": "preserve", "tail": "trim"},
        }
        idem_headers = _auth_headers(extra={"Idempotency-Key": "idem-key-001"})

        # First call -> 202
        r1 = client.post("/v1/jobs", json=job_payload_1, headers=idem_headers)
        assert r1.status_code == 202
        job_id_1 = r1.json()["job_id"]

        # Second call with same Idempotency-Key and identical params -> 202 with same job_id
        r2 = client.post("/v1/jobs", json=job_payload_1, headers=idem_headers)
        assert r2.status_code == 202
        assert r2.json()["job_id"] == job_id_1

        # Third call with same Idempotency-Key but different strength -> 409 IDEMPOTENCY_KEY_CONFLICT
        job_payload_diff = {**job_payload_1, "strength": 0.75}
        r3 = client.post("/v1/jobs", json=job_payload_diff, headers=idem_headers)
        assert r3.status_code == 409
        err_body = r3.json()
        assert err_body["ok"] is False
        assert err_body["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert err_body["error"]["retryable"] is False

    def test_cancel_queued_running_duplicate_and_terminal_state_invariance(self, tmp_path: Path) -> None:
        # Use block delay so we can deterministically cancel a running job mid-stream
        cfg = _make_test_config(tmp_path, worker_block_delay_ms=25.0)
        app = create_app(cfg)
        client = TestClient(app)

        in_wav = tmp_path / "cancel_test.wav"
        wav_bytes = _write_test_wav(in_wav, sample_rate=48000, samples=48000)  # ~47 blocks of 1024
        asset_id = client.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream"}),
        ).json()["asset_id"]

        # 1. Queued job cancellation + duplicate cancellation
        r_job_q = client.post(
            "/v1/jobs",
            json={"input": {"asset_id": asset_id}, "preset": {"id": "room", "version": "1"}},
            headers=_auth_headers(),
        )
        job_q_id = r_job_q.json()["job_id"]

        r_cancel_1 = client.post(f"/v1/jobs/{job_q_id}/cancel", headers=_auth_headers())
        assert r_cancel_1.status_code == 200
        assert r_cancel_1.json()["status"] == "canceled"

        # Duplicate cancel on already-canceled job is idempotent
        r_cancel_2 = client.post(f"/v1/jobs/{job_q_id}/cancel", headers=_auth_headers())
        assert r_cancel_2.status_code == 200
        assert r_cancel_2.json()["status"] == "canceled"

        # 2. Running job cancellation at block boundary + temp directory cleanup
        r_job_run = client.post(
            "/v1/jobs",
            json={"input": {"asset_id": asset_id}, "preset": {"id": "surround", "version": "1"}},
            headers=_auth_headers(),
        )
        job_run_id = r_job_run.json()["job_id"]

        worker = AuroraWorker(cfg, store=app.state.store)
        worker.start_background()
        try:
            # Wait until job transitions to 'running'
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                st = client.get(f"/v1/jobs/{job_run_id}", headers=_auth_headers()).json()
                if st["status"] == "running":
                    break
                time.sleep(0.02)
            assert st["status"] == "running"

            # Request cancel while running: status is 'running' with canceling=True (or immediately 'canceled')
            r_cancel_run = client.post(f"/v1/jobs/{job_run_id}/cancel", headers=_auth_headers()).json()
            assert r_cancel_run["cancel_requested"] is True
            assert r_cancel_run["status"] in ("running", "canceled")
            if r_cancel_run["status"] == "running":
                assert r_cancel_run["canceling"] is True

            # Wait for worker to stop at the next block boundary and finalize status='canceled'
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                st_after = client.get(f"/v1/jobs/{job_run_id}", headers=_auth_headers()).json()
                if st_after["status"] == "canceled":
                    break
                time.sleep(0.02)
            assert st_after["status"] == "canceled"
            # Verify attempt temp directory was cleaned up
            assert list(cfg.tmp_dir.glob(f"attempt_{job_run_id}_*")) == []
        finally:
            worker.stop()

        # 3. Terminal succeeded job: calling cancel does NOT alter succeeded status or delete result
        cfg_fast = _make_test_config(tmp_path, worker_block_delay_ms=0.0)
        worker_fast = AuroraWorker(cfg_fast, store=app.state.store)
        r_job_ok = client.post(
            "/v1/jobs",
            json={"input": {"asset_id": asset_id}, "preset": {"id": "room", "version": "1"}},
            headers=_auth_headers(),
        )
        job_ok_id = r_job_ok.json()["job_id"]
        worker_fast.run_once()

        st_ok = client.get(f"/v1/jobs/{job_ok_id}", headers=_auth_headers()).json()
        assert st_ok["status"] == "succeeded"

        # Cancel after succeeded -> remains succeeded, result still downloadable!
        r_cancel_after_ok = client.post(f"/v1/jobs/{job_ok_id}/cancel", headers=_auth_headers()).json()
        assert r_cancel_after_ok["status"] == "succeeded"
        assert r_cancel_after_ok["cancel_requested"] is False

        r_dl = client.get(f"/v1/jobs/{job_ok_id}/result", headers=_auth_headers())
        assert r_dl.status_code == 200
        assert len(r_dl.content) > 1000

    def test_worker_sigkill_lease_expiry_recovery_and_retry_success(self, tmp_path: Path) -> None:
        """Spawn a real worker subprocess, SIGKILL it while running a job, wait for lease expiry, and recover."""
        cfg = _make_test_config(
            tmp_path,
            lease_duration_sec=0.5,
            heartbeat_interval_sec=0.15,
            worker_poll_interval_sec=0.05,
            worker_block_delay_ms=60.0,
            max_retries=2,
        )
        app = create_app(cfg)
        client = TestClient(app)

        in_wav = tmp_path / "kill_test.wav"
        wav_bytes = _write_test_wav(in_wav, sample_rate=48000, samples=48000)
        asset_id = client.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream"}),
        ).json()["asset_id"]

        job_id = client.post(
            "/v1/jobs",
            json={"input": {"asset_id": asset_id}, "preset": {"id": "room", "version": "1"}},
            headers=_auth_headers(),
        ).json()["job_id"]

        # Launch a real child process running `python -m aurora_binaural.worker` with slow block delay
        env = os.environ.copy()
        env.update(
            {
                "AURORA_DATA_DIR": str(cfg.data_dir),
                "AURORA_DB_PATH": str(cfg.db_path),
                "AURORA_HRTF_DIR": str(cfg.hrtf_dir),
                "AURORA_LEASE_DURATION_SEC": "0.5",
                "AURORA_HEARTBEAT_INTERVAL_SEC": "0.15",
                "AURORA_WORKER_POLL_INTERVAL_SEC": "0.05",
                "AURORA_WORKER_BLOCK_DELAY_MS": "60.0",
                "AURORA_MAX_RETRIES": "2",
            }
        )
        worker_proc = subprocess.Popen(
            [sys.executable, "-m", "aurora_binaural.worker"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Wait for worker_proc to claim the job (status == 'running', attempts == 1)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                st = client.get(f"/v1/jobs/{job_id}", headers=_auth_headers()).json()
                if st["status"] == "running" and st["attempts"] == 1:
                    break
                time.sleep(0.03)
            assert st["status"] == "running"
            assert st["attempts"] == 1

            # Abruptly SIGKILL the worker subprocess!
            worker_proc.send_signal(signal.SIGKILL)
            worker_proc.wait(timeout=3.0)
        finally:
            if worker_proc.poll() is None:
                worker_proc.kill()
                worker_proc.wait()

        # Wait for the 0.5s lease to expire
        time.sleep(0.65)

        # Start a healthy recovery worker (with 0ms block delay) -> reclaims expired lease and succeeds on attempt 2!
        cfg_recovery = _make_test_config(
            tmp_path,
            lease_duration_sec=5.0,
            worker_block_delay_ms=0.0,
            max_retries=2,
        )
        recovery_worker = AuroraWorker(cfg_recovery, store=app.state.store)
        finished_job = recovery_worker.run_once()
        assert finished_job is not None
        assert finished_job.status == "succeeded"
        assert finished_job.attempts == 2

        # Verify report via API records attempts == 2
        rep_resp = client.get(f"/v1/jobs/{job_id}/report", headers=_auth_headers())
        assert rep_resp.status_code == 200
        rep_json = rep_resp.json()
        assert rep_json["attempts"] == 2
        assert rep_json["job_id"] == job_id
        assert rep_json["timeline"]["sync_within_tolerance"] is True

    def test_max_retries_exhaustion_marks_job_failed(self, tmp_path: Path) -> None:
        """Verify that after initial attempt + 2 retries (3 total attempts) expire, job is marked failed."""
        cfg = _make_test_config(tmp_path, lease_duration_sec=1.0, max_retries=2)
        store = TaskStore(cfg.db_path)

        in_wav = tmp_path / "retry_exhaust.wav"
        _write_test_wav(in_wav, samples=4800)
        probe = probe_audio(in_wav)
        store.create_asset(
            asset_id="asset_exhaust",
            filename="retry_exhaust.wav",
            file_path=in_wav,
            probe=probe,
            retention_sec=3600.0,
            now=100.0,
        )
        params = AuroraParams(input=InputSpec(asset_id="asset_exhaust"))
        job, _ = store.create_or_get_job(params, retention_sec=3600.0, max_retries=2, now=100.0)
        assert WORKER_MAX_RETRIES == 2

        # Attempt 1 (initial) claims at t=100, expires at t=101 -> reclaimed to queued
        c1 = store.claim_next_job("w1", lease_duration_sec=1.0, now=100.0)
        assert c1 is not None and c1.attempts == 1
        store.recover_expired_leases(now=102.0)
        j_after_1 = store.get_job(job.job_id)
        assert j_after_1 is not None and j_after_1.status == "queued"

        # Attempt 2 (retry 1) claims at t=102, expires at t=103 -> reclaimed to queued
        c2 = store.claim_next_job("w2", lease_duration_sec=1.0, now=102.0)
        assert c2 is not None and c2.attempts == 2
        store.recover_expired_leases(now=104.0)
        j_after_2 = store.get_job(job.job_id)
        assert j_after_2 is not None and j_after_2.status == "queued"

        # Attempt 3 (retry 2) claims at t=104, expires at t=105 -> max_retries (2) exhausted -> failed!
        c3 = store.claim_next_job("w3", lease_duration_sec=1.0, now=104.0)
        assert c3 is not None and c3.attempts == 3
        store.recover_expired_leases(now=106.0)
        j_after_3 = store.get_job(job.job_id)
        assert j_after_3 is not None
        assert j_after_3.status == "failed"
        assert j_after_3.error is not None
        assert j_after_3.error["code"] == "LEASE_EXPIRED_MAX_RETRIES"
        assert j_after_3.error["retryable"] is False

    def test_api_restart_preserves_job_state_and_artifacts(self, tmp_path: Path) -> None:
        cfg = _make_test_config(tmp_path)
        app1 = create_app(cfg)
        client1 = TestClient(app1)

        in_wav = tmp_path / "restart.wav"
        wav_bytes = _write_test_wav(in_wav, samples=12000)
        asset_id = client1.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream"}),
        ).json()["asset_id"]
        job_id = client1.post(
            "/v1/jobs",
            json={"input": {"asset_id": asset_id}, "preset": {"id": "room", "version": "1"}},
            headers=_auth_headers(),
        ).json()["job_id"]

        worker = AuroraWorker(cfg)
        worker.run_once()

        status_before = client1.get(f"/v1/jobs/{job_id}", headers=_auth_headers()).json()
        assert status_before["status"] == "succeeded"
        del client1
        del app1

        # Create a brand new FastAPI app & TaskStore instance pointing to the same SQLite DB
        app2 = create_app(cfg)
        client2 = TestClient(app2)
        status_after = client2.get(f"/v1/jobs/{job_id}", headers=_auth_headers()).json()
        assert status_after == status_before

        res_resp = client2.get(f"/v1/jobs/{job_id}/result", headers=_auth_headers())
        assert res_resp.status_code == 200
        assert hashlib.sha256(res_resp.content).hexdigest() == status_after["result_metadata"]["sha256"]

    def test_output_write_failure_disk_full_marks_failed_and_leaves_no_partial_files(
        self, tmp_path: Path
    ) -> None:
        cfg = _make_test_config(tmp_path)
        app = create_app(cfg)
        client = TestClient(app)

        in_wav = tmp_path / "disk_full.wav"
        wav_bytes = _write_test_wav(in_wav, samples=12000)
        asset_id = client.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream"}),
        ).json()["asset_id"]
        job_id = client.post(
            "/v1/jobs",
            json={"input": {"asset_id": asset_id}, "preset": {"id": "room", "version": "1"}},
            headers=_auth_headers(),
        ).json()["job_id"]

        real_replace = os.replace

        def _failing_replace_on_results_dir(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            dst_path = Path(dst)
            if dst_path.parent.resolve() == cfg.results_dir.resolve() and dst_path.name.endswith(".report.json"):
                raise OSError(errno.ENOSPC, "No space left on device (simulated)")
            real_replace(src, dst)

        worker = AuroraWorker(cfg, store=app.state.store)
        with patch("aurora_binaural.worker.os.replace", side_effect=_failing_replace_on_results_dir):
            failed_job = worker.run_once()

        assert failed_job is not None
        assert failed_job.job_id == job_id
        assert failed_job.status == "failed"
        assert failed_job.error is not None
        assert failed_job.error["code"] == "OUTPUT_WRITE_FAILED"
        assert failed_job.error["retryable"] is False

        # Verify neither results_dir nor tmp_dir has any leftover partial files!
        assert list(cfg.results_dir.iterdir()) == []
        assert list(cfg.tmp_dir.iterdir()) == []

    def test_expiration_cleanup_protects_in_use_assets_and_returns_410_on_expired_download(
        self, tmp_path: Path
    ) -> None:
        # Configure a short retention period (10 seconds) and test with controlled timestamps
        cfg = _make_test_config(tmp_path, retention_sec=10.0)
        app = create_app(cfg)
        store: TaskStore = app.state.store
        client = TestClient(app)

        in_wav = tmp_path / "exp_in.wav"
        wav_bytes = _write_test_wav(in_wav, samples=12000)
        r_asset = client.post(
            "/v1/assets",
            content=wav_bytes,
            headers=_auth_headers(extra={"Content-Type": "application/octet-stream"}),
        )
        asset_id = r_asset.json()["asset_id"]
        asset_rec = store.get_asset(asset_id)
        assert asset_rec is not None

        # Create a queued job referencing asset_id
        r_job = client.post(
            "/v1/jobs",
            json={"input": {"asset_id": asset_id}, "preset": {"id": "room", "version": "1"}},
            headers=_auth_headers(),
        )
        job_id = r_job.json()["job_id"]

        # 1. Advance simulated time past asset's expiration while job is still 'queued':
        #    cleanup_expired_resources MUST NOT delete the in-use asset!
        future_ts = time.time() + 100.0
        cleaned_jobs, cleaned_assets = store.cleanup_expired_resources(now=future_ts)
        assert cleaned_jobs == []
        assert cleaned_assets == []
        assert asset_rec.file_path.exists(), "In-use asset for queued job must not be deleted!"

        # 2. Now run the worker so the job succeeds
        worker = AuroraWorker(cfg, store=store)
        succeeded_job = worker.run_once()
        assert succeeded_job is not None and succeeded_job.status == "succeeded"
        assert succeeded_job.result_path is not None and succeeded_job.result_path.exists()

        # 3. Advance time past both asset and job result expiration -> cleanup deletes both files
        far_future_ts = time.time() + 200.0
        cleaned_jobs_2, cleaned_assets_2 = store.cleanup_expired_resources(now=far_future_ts)
        assert job_id in cleaned_jobs_2
        assert asset_id in cleaned_assets_2
        assert not succeeded_job.result_path.exists()
        assert not asset_rec.file_path.exists()

        # 4. Downloading expired result or report returns HTTP 410 Gone with code="RESULT_EXPIRED"
        r_dl_exp = client.get(f"/v1/jobs/{job_id}/result", headers=_auth_headers())
        assert r_dl_exp.status_code == 410
        assert r_dl_exp.json()["error"]["code"] == "RESULT_EXPIRED"

        r_rep_exp = client.get(f"/v1/jobs/{job_id}/report", headers=_auth_headers())
        assert r_rep_exp.status_code == 410
        assert r_rep_exp.json()["error"]["code"] == "RESULT_EXPIRED"

    def test_retry_fencing_prevents_two_successful_results(self, tmp_path: Path) -> None:
        """Verify that if Worker A's lease expires and Worker B completes Attempt 2, Worker A cannot overwrite it."""
        cfg = _make_test_config(tmp_path, lease_duration_sec=1.0)
        store = TaskStore(cfg.db_path)

        in_wav = tmp_path / "fencing.wav"
        _write_test_wav(in_wav, samples=12000)
        asset_path = cfg.assets_dir / "asset_fence.wav"
        cfg.ensure_directories()
        asset_path.write_bytes(in_wav.read_bytes())
        store.create_asset(
            asset_id="asset_fence",
            filename="fencing.wav",
            file_path=asset_path,
            probe=probe_audio(asset_path),
            retention_sec=3600.0,
            now=100.0,
        )

        params = AuroraParams(
            input=InputSpec(asset_id="asset_fence"),
            preset=PresetSpec(id=PresetId.ROOM, version="1"),
            output=OutputSpec(format=OutputFormat.WAV, sample_rate=48000, bit_depth=24),
        )
        job, _ = store.create_or_get_job(params, retention_sec=3600.0, max_retries=2, now=100.0)

        # Worker A claims Attempt 1 at t=100 (lease_A expires at t=101)
        claim_a = store.claim_next_job("worker_A", lease_duration_sec=1.0, now=100.0)
        assert claim_a is not None
        assert claim_a.attempts == 1
        assert claim_a.lease_id is not None

        # Lease expires at t=102; Worker B recovers and claims Attempt 2
        store.recover_expired_leases(now=102.0)
        worker_b = AuroraWorker(cfg, store=store, worker_id="worker_B")
        claim_b = store.claim_next_job("worker_B", lease_duration_sec=30.0, now=102.0)
        assert claim_b is not None
        assert claim_b.attempts == 2

        # Worker B finishes Attempt 2 first and commits success
        res_b = worker_b.execute_claimed_job(claim_b)
        assert res_b is not None and res_b.status == "succeeded"
        assert res_b.attempts == 2

        final_report_file = cfg.results_dir / f"{job.job_id}.report.json"
        report_after_b = json.loads(final_report_file.read_text(encoding="utf-8"))
        assert report_after_b["attempts"] == 2

        # Now stale Worker A attempts to commit its output using its old `claim_a.lease_id`
        commit_called = False

        def _stale_commit() -> None:
            nonlocal commit_called
            commit_called = True

        _, committed_a = store.finalize_job_success(
            job.job_id,
            claim_a.lease_id,
            result_path=cfg.results_dir / f"{job.job_id}.wav",
            report_path=final_report_file,
            result_metadata={"samples": 12000},
            retention_sec=3600.0,
            commit_files_fn=_stale_commit,
            now=103.0,
        )
        assert committed_a is False
        assert commit_called is False
        # Report on disk is still strictly Attempt 2
        assert json.loads(final_report_file.read_text(encoding="utf-8"))["attempts"] == 2


class TestCliRemoteModeAgainstRealFastApiServer:
    """Tests for Task 5: `RemoteAuroraClient` and `aurora convert --server` against real FastAPI + Worker."""

    def test_cli_convert_server_and_job_commands_end_to_end(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = _make_test_config(tmp_path / "server_env")
        in_wav = tmp_path / "cli_remote_in.wav"
        _write_test_wav(in_wav, sample_rate=48000, samples=24000)

        out_wav = tmp_path / "cli_remote_out.wav"
        rep_json = tmp_path / "cli_remote_report.json"
        monkeypatch.setenv("AURORA_API_KEY", "test_secret_api_key_p3")

        with _live_uvicorn_server(cfg, start_worker=True) as server_url:
            # 1. `aurora convert --server` full upload -> queue -> worker render -> download -> report
            code = run_cli(
                [
                    "convert",
                    str(in_wav),
                    "--server",
                    server_url,
                    "--preset",
                    "room",
                    "--strength",
                    "0.4",
                    "--output",
                    str(out_wav),
                    "--report",
                    str(rep_json),
                ]
            )
            stdout_data = json.loads(capsys.readouterr().out)
            assert code == int(ExitCode.OK)
            assert stdout_data["ok"] is True
            assert stdout_data["status"] == "succeeded"
            assert stdout_data["output"]["samples"] == 24000
            assert stdout_data["timeline"]["sync_within_tolerance"] is True
            assert out_wav.exists()
            assert compute_sha256(out_wav) == stdout_data["output"]["sha256"]
            assert rep_json.exists()

            job_id = stdout_data["job_id"]
            assert isinstance(job_id, str) and job_id.startswith("job_")

            # 2. `aurora job status JOB_ID --server` against real server
            code_st = run_cli(["job", "status", job_id, "--server", server_url, "--json"])
            st_out = json.loads(capsys.readouterr().out)
            assert code_st == int(ExitCode.OK)
            assert st_out["job_id"] == job_id
            assert st_out["status"] == "succeeded"

            # 3. Invalid API Key via CLI returns ExitCode.REMOTE_ERROR (5) with code="UNAUTHORIZED"
            monkeypatch.setenv("AURORA_API_KEY", "wrong_api_key")
            code_unauth = run_cli(["job", "status", job_id, "--server", server_url, "--json"])
            unauth_out = json.loads(capsys.readouterr().out)
            assert code_unauth == int(ExitCode.REMOTE_ERROR)
            assert unauth_out["error"]["code"] == "UNAUTHORIZED"

            # 4. Expired result download via RemoteAuroraClient raises RemoteServiceError(error_code="RESULT_EXPIRED")
            client = RemoteAuroraClient(server_url, api_key="test_secret_api_key_p3")
            store: TaskStore = TaskStore(cfg.db_path)
            store.cleanup_expired_resources(now=time.time() + 7200.0)
            with pytest.raises(RemoteServiceError) as exc_info:
                client.download_result(job_id, tmp_path / "expired.wav", overwrite=True)
            assert exc_info.value.error_code == "RESULT_EXPIRED"
