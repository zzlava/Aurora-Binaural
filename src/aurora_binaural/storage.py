"""SQLite persistent task & asset store with short transactions, leases, heartbeats, and idempotency.

Implements docs/plan.md Sections 10, 11, and 16.4:
- Short `BEGIN IMMEDIATE` transactions for job claiming, lease renewal, cancellation, and completion.
- Fencing token (`lease_id`) per attempt so stale workers can never overwrite results or produce two successes.
- Startup and periodic recovery of expired worker leases (`max_retries = 2`).
- `Idempotency-Key` deduplication with parameter hash conflict detection (HTTP 409).
- Periodic cleanup of expired assets and results without deleting files in use by queued/running jobs or active downloads.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from aurora_binaural.exit_codes import AuroraError, ExitCode
from aurora_binaural.params import AuroraParams
from aurora_binaural.report import AudioProbeInfo

JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]
TERMINAL_JOB_STATUSES: tuple[str, ...] = ("succeeded", "failed", "canceled")


def format_utc_iso(epoch_sec: float | None) -> str | None:
    """Convert a POSIX timestamp in seconds to an ISO-8601 UTC string."""
    if epoch_sec is None:
        return None
    return datetime.fromtimestamp(epoch_sec, tz=UTC).isoformat()


class ApiServiceError(AuroraError):
    """Structured HTTP API exception carrying HTTP status code, stable error code, and retryability."""

    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        http_status: int = 400,
        error_code: str = "BAD_REQUEST",
        exit_code: ExitCode = ExitCode.REMOTE_ERROR,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            message,
            error_code=error_code,
            exit_code=exit_code,
            retryable=retryable,
            details=details,
        )
        self.http_status = http_status


class UnauthorizedError(ApiServiceError):
    """Raised when Bearer API Key is missing or invalid (HTTP 401)."""

    def __init__(self, message: str = "Missing or invalid Bearer API Key.") -> None:
        super().__init__(
            message,
            http_status=401,
            error_code="UNAUTHORIZED",
            exit_code=ExitCode.REMOTE_ERROR,
            retryable=False,
        )


class AssetNotFoundError(ApiServiceError):
    """Raised when an asset ID does not exist (HTTP 404)."""

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"Asset '{asset_id}' was not found.",
            http_status=404,
            error_code="ASSET_NOT_FOUND",
            exit_code=ExitCode.INPUT_ERROR,
            retryable=False,
            details={"asset_id": asset_id},
        )


class JobNotFoundError(ApiServiceError):
    """Raised when a job ID does not exist (HTTP 404)."""

    def __init__(self, job_id: str) -> None:
        super().__init__(
            f"Job '{job_id}' was not found.",
            http_status=404,
            error_code="JOB_NOT_FOUND",
            exit_code=ExitCode.REMOTE_ERROR,
            retryable=False,
            details={"job_id": job_id},
        )


class IdempotencyConflictError(ApiServiceError):
    """Raised when the same Idempotency-Key is reused with different job parameters (HTTP 409)."""

    def __init__(self, idempotency_key: str, existing_job_id: str) -> None:
        super().__init__(
            f"Idempotency-Key '{idempotency_key}' was already used with different request parameters.",
            http_status=409,
            error_code="IDEMPOTENCY_KEY_CONFLICT",
            exit_code=ExitCode.PARAM_ERROR,
            retryable=False,
            details={"idempotency_key": idempotency_key, "existing_job_id": existing_job_id},
        )


class JobStateConflictError(ApiServiceError):
    """Raised when requesting result/report for a job that has not succeeded (HTTP 409)."""

    def __init__(self, job_id: str, status: str) -> None:
        super().__init__(
            f"Job '{job_id}' is in status '{status}' and does not have a completed result.",
            http_status=409,
            error_code="JOB_NOT_SUCCEEDED",
            exit_code=ExitCode.REMOTE_ERROR,
            retryable=status in ("queued", "running"),
            details={"job_id": job_id, "status": status},
        )


class ResourceExpiredError(ApiServiceError):
    """Raised when an asset or job result/report has expired (HTTP 410 Gone)."""

    def __init__(self, resource_type: Literal["asset", "result"], resource_id: str, expires_at: float | None = None) -> None:
        code = "ASSET_EXPIRED" if resource_type == "asset" else "RESULT_EXPIRED"
        label = "Asset" if resource_type == "asset" else "Job result"
        super().__init__(
            f"{label} '{resource_id}' has expired and is no longer available.",
            http_status=410,
            error_code=code,
            exit_code=ExitCode.REMOTE_ERROR,
            retryable=False,
            details={
                f"{resource_type}_id": resource_id,
                "expires_at": format_utc_iso(expires_at),
            },
        )


@dataclass(frozen=True)
class AssetRecord:
    """Stored metadata for an uploaded audio asset."""

    asset_id: str
    filename: str
    file_path: Path
    sha256: str
    size_bytes: int
    format: str
    container: str
    codec: str
    sample_rate: int
    channels: int
    samples: int
    duration_seconds: float
    probe: AudioProbeInfo
    created_at: float
    expires_at: float
    expired: bool

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize asset metadata for `POST /v1/assets` without exposing internal server paths."""
        return {
            "ok": True,
            "asset_id": self.asset_id,
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "container": self.container,
            "codec": self.codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "samples": self.samples,
            "duration_seconds": self.duration_seconds,
            "created_at": format_utc_iso(self.created_at),
            "expires_at": format_utc_iso(self.expires_at),
        }


@dataclass(frozen=True)
class JobRecord:
    """Stored state and metadata for an asynchronous rendering job."""

    job_id: str
    asset_id: str
    status: JobStatus
    cancel_requested: bool
    progress: float
    params: AuroraParams
    request_hash: str
    idempotency_key: str | None
    attempts: int
    max_retries: int
    worker_id: str | None
    lease_id: str | None
    lease_expires_at: float | None
    heartbeat_at: float | None
    result_path: Path | None
    report_path: Path | None
    result_metadata: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    updated_at: float
    expires_at: float
    result_expired: bool

    @property
    def is_canceling(self) -> bool:
        """True when job is currently `running` and cancellation has been requested (Section 10)."""
        return self.status == "running" and self.cancel_requested

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize job status for `POST /v1/jobs`, `GET /v1/jobs/{id}`, and `POST /v1/jobs/{id}/cancel`."""
        return {
            "ok": True,
            "job_id": self.job_id,
            "preview": self.params.preview.model_dump() if self.params.preview else None,
            "asset_id": self.asset_id,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
            "canceling": self.is_canceling,
            "progress": round(self.progress, 4),
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "created_at": format_utc_iso(self.created_at),
            "started_at": format_utc_iso(self.started_at),
            "finished_at": format_utc_iso(self.finished_at),
            "expires_at": format_utc_iso(self.expires_at),
            "result_metadata": self.result_metadata if self.status == "succeeded" else None,
            "error": self.error if self.status == "failed" else None,
        }


def compute_canonical_job_hash(params: AuroraParams) -> str:
    """Compute a deterministic SHA-256 hex digest over the normalized `AuroraParams` payload."""
    normalized = {
        "api_version": params.api_version,
        **({"preview": params.preview.model_dump()} if params.preview else {}),
        "mode": params.mode.value,
        "asset_id": params.input.asset_id if params.input else None,
        "preset": {
            "id": params.preset.id.value,
            "version": params.preset.version,
        },
        "strength": round(params.effective_strength, 6),
        "seed": params.seed,
        "output": {
            "format": params.output.format.value,
            "container": params.output.container.value if params.output.container else params.output.format.value,
            "sample_rate": params.output.sample_rate,
            "bit_depth": params.output.bit_depth,
            "bitrate_kbps": params.output.bitrate_kbps,
        },
        "timing": {
            "mode": params.timing.mode.value,
            "tail": params.timing.tail.value,
            "fade_out_ms": round(params.timing.fade_out_ms, 4),
        },
    }
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TaskStore:
    """SQLite-backed persistent store for assets, jobs, leases, and worker heartbeats."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._download_lock = threading.Lock()
        self._active_downloads: dict[str, int] = {}
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    @contextlib.contextmanager
    def _immediate_tx(self) -> Iterator[sqlite3.Connection]:
        """Execute a short write transaction under `BEGIN IMMEDIATE`."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                conn.execute("COMMIT;")
            except Exception:
                with contextlib.suppress( sqlite3.Error ):
                    conn.execute("ROLLBACK;")
                raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create schema tables and indexes if they do not exist."""
        with self._immediate_tx() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    format TEXT NOT NULL,
                    container TEXT NOT NULL,
                    codec TEXT NOT NULL,
                    sample_rate INTEGER NOT NULL,
                    channels INTEGER NOT NULL,
                    samples INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL,
                    probe_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    expired INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0.0,
                    params_json TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 2,
                    worker_id TEXT,
                    lease_id TEXT,
                    lease_expires_at REAL,
                    heartbeat_at REAL,
                    result_path TEXT,
                    report_path TEXT,
                    result_metadata_json TEXT,
                    error_json TEXT,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    result_expired INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_asset_status ON jobs(asset_id, status);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    pid INTEGER NOT NULL,
                    concurrency INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )

    def check_health(self) -> bool:
        """Verify database read/write readiness."""
        try:
            conn = self._connect()
            try:
                row = conn.execute("SELECT 1 AS ok;").fetchone()
                return bool(row and row["ok"] == 1)
            finally:
                conn.close()
        except Exception:
            return False

    def upsert_worker_heartbeat(
        self,
        worker_id: str,
        *,
        pid: int,
        concurrency: int,
        lease_duration_sec: float,
        now: float | None = None,
    ) -> None:
        """Record or refresh a Worker process heartbeat for `/ready` monitoring."""
        ts = now if now is not None else time.time()
        expires = ts + max(lease_duration_sec, 5.0)
        with self._immediate_tx() as conn:
            conn.execute(
                """
                INSERT INTO worker_heartbeats (worker_id, pid, concurrency, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    pid = excluded.pid,
                    concurrency = excluded.concurrency,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at;
                """,
                (worker_id, pid, concurrency, ts, expires),
            )

    def remove_worker_heartbeat(self, worker_id: str) -> None:
        """Remove a Worker heartbeat entry on clean shutdown."""
        with self._immediate_tx() as conn:
            conn.execute("DELETE FROM worker_heartbeats WHERE worker_id = ?;", (worker_id,))

    def has_active_worker(self, *, now: float | None = None) -> bool:
        """Return True if at least one Worker has an active (non-expired) heartbeat."""
        ts = now if now is not None else time.time()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM worker_heartbeats WHERE expires_at > ?;",
                (ts,),
            ).fetchone()
            return bool(row and int(row["cnt"]) > 0)
        except Exception:
            return False
        finally:
            conn.close()

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> AssetRecord:
        probe = AudioProbeInfo.model_validate_json(str(row["probe_json"]))
        return AssetRecord(
            asset_id=str(row["asset_id"]),
            filename=str(row["filename"]),
            file_path=Path(str(row["file_path"])),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            format=str(row["format"]),
            container=str(row["container"]),
            codec=str(row["codec"]),
            sample_rate=int(row["sample_rate"]),
            channels=int(row["channels"]),
            samples=int(row["samples"]),
            duration_seconds=float(row["duration_seconds"]),
            probe=probe,
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            expired=bool(row["expired"]),
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        params = AuroraParams.model_validate_json(str(row["params_json"]))
        res_meta_raw = row["result_metadata_json"]
        err_raw = row["error_json"]
        res_path_raw = row["result_path"]
        rep_path_raw = row["report_path"]
        return JobRecord(
            job_id=str(row["job_id"]),
            asset_id=str(row["asset_id"]),
            status=row["status"],
            cancel_requested=bool(row["cancel_requested"]),
            progress=float(row["progress"]),
            params=params,
            request_hash=str(row["request_hash"]),
            idempotency_key=str(row["idempotency_key"]) if row["idempotency_key"] is not None else None,
            attempts=int(row["attempts"]),
            max_retries=int(row["max_retries"]),
            worker_id=str(row["worker_id"]) if row["worker_id"] is not None else None,
            lease_id=str(row["lease_id"]) if row["lease_id"] is not None else None,
            lease_expires_at=float(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None,
            heartbeat_at=float(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None,
            result_path=Path(str(res_path_raw)) if res_path_raw is not None else None,
            report_path=Path(str(rep_path_raw)) if rep_path_raw is not None else None,
            result_metadata=json.loads(str(res_meta_raw)) if res_meta_raw is not None else None,
            error=json.loads(str(err_raw)) if err_raw is not None else None,
            created_at=float(row["created_at"]),
            started_at=float(row["started_at"]) if row["started_at"] is not None else None,
            finished_at=float(row["finished_at"]) if row["finished_at"] is not None else None,
            updated_at=float(row["updated_at"]),
            expires_at=float(row["expires_at"]),
            result_expired=bool(row["result_expired"]),
        )

    def create_asset(
        self,
        *,
        asset_id: str,
        filename: str,
        file_path: Path,
        probe: AudioProbeInfo,
        retention_sec: float,
        now: float | None = None,
    ) -> AssetRecord:
        """Persist an uploaded asset record in SQLite."""
        ts = now if now is not None else time.time()
        expires = ts + retention_sec
        sanitized_probe = probe.model_copy(update={"path": f"asset://{asset_id}/{filename}"})
        with self._immediate_tx() as conn:
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, filename, file_path, sha256, size_bytes, format, container,
                    codec, sample_rate, channels, samples, duration_seconds, probe_json,
                    created_at, expires_at, expired
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);
                """,
                (
                    asset_id,
                    filename,
                    str(file_path),
                    sanitized_probe.sha256,
                    sanitized_probe.size_bytes,
                    sanitized_probe.format,
                    sanitized_probe.container,
                    sanitized_probe.codec,
                    sanitized_probe.sample_rate,
                    sanitized_probe.channels,
                    sanitized_probe.samples,
                    sanitized_probe.duration_seconds,
                    sanitized_probe.model_dump_json(),
                    ts,
                    expires,
                ),
            )
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?;", (asset_id,)).fetchone()
            assert row is not None
            return self._row_to_asset(row)

    def get_asset(self, asset_id: str) -> AssetRecord | None:
        """Fetch an asset record by ID."""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM assets WHERE asset_id = ?;", (asset_id,)).fetchone()
            return self._row_to_asset(row) if row is not None else None
        finally:
            conn.close()

    def create_or_get_job(
        self,
        params: AuroraParams,
        *,
        idempotency_key: str | None = None,
        retention_sec: float,
        max_retries: int = 2,
        now: float | None = None,
    ) -> tuple[JobRecord, bool]:
        """Create a new queued job or return existing job for `Idempotency-Key`.

        Raises:
            IdempotencyConflictError: If `idempotency_key` already exists with different parameters (HTTP 409).
            AssetNotFoundError: If `params.input.asset_id` does not exist (HTTP 404).
            ResourceExpiredError: If `params.input.asset_id` has expired (HTTP 410).
        """
        ts = now if now is not None else time.time()
        asset_id = params.input.asset_id if params.input else None
        if not asset_id:
            raise ApiServiceError(
                "Job creation requires 'input.asset_id'.",
                http_status=422,
                error_code="INVALID_PARAMETERS",
                exit_code=ExitCode.PARAM_ERROR,
            )

        clean_idem = idempotency_key.strip() if idempotency_key and idempotency_key.strip() else None
        req_hash = compute_canonical_job_hash(params)

        with self._immediate_tx() as conn:
            if clean_idem is not None:
                existing_row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?;",
                    (clean_idem,),
                ).fetchone()
                if existing_row is not None:
                    existing_job = self._row_to_job(existing_row)
                    if existing_job.request_hash != req_hash:
                        raise IdempotencyConflictError(clean_idem, existing_job.job_id)
                    return existing_job, False

            asset_row = conn.execute(
                "SELECT * FROM assets WHERE asset_id = ?;",
                (asset_id,),
            ).fetchone()
            if asset_row is None:
                raise AssetNotFoundError(asset_id)

            asset = self._row_to_asset(asset_row)
            if asset.expired or ts >= asset.expires_at or not asset.file_path.exists():
                raise ResourceExpiredError("asset", asset_id, asset.expires_at)

            job_id = f"job_{uuid.uuid4().hex[:16]}"
            expires = ts + retention_sec
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, asset_id, status, cancel_requested, progress, params_json,
                    request_hash, idempotency_key, attempts, max_retries,
                    created_at, updated_at, expires_at, result_expired
                ) VALUES (?, ?, 'queued', 0, 0.0, ?, ?, ?, 0, ?, ?, ?, ?, 0);
                """,
                (
                    job_id,
                    asset_id,
                    params.model_dump_json(),
                    req_hash,
                    clean_idem,
                    max_retries,
                    ts,
                    ts,
                    expires,
                ),
            )
            new_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            assert new_row is not None
            return self._row_to_job(new_row), True

    def get_job(self, job_id: str) -> JobRecord | None:
        """Fetch a job record by ID."""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            return self._row_to_job(row) if row is not None else None
        finally:
            conn.close()

    def request_cancel_job(self, job_id: str, *, now: float | None = None) -> JobRecord:
        """Idempotently request job cancellation (docs/plan.md Sections 10 & 16.4).

        - If `queued`: immediately transitions to `canceled`.
        - If `running`: sets `cancel_requested = 1` (status stays `running` with `canceling = True`
          until the Worker stops at the next block boundary and cleans up temp files).
        - If already terminal (`succeeded`, `failed`, `canceled`): leaves status and results untouched.
        """
        ts = now if now is not None else time.time()
        with self._immediate_tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)

            job = self._row_to_job(row)
            if job.status in TERMINAL_JOB_STATUSES:
                return job

            if job.status == "queued":
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'canceled',
                        cancel_requested = 1,
                        worker_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        finished_at = ?,
                        updated_at = ?
                    WHERE job_id = ?;
                    """,
                    (ts, ts, job_id),
                )
            elif job.status == "running":
                conn.execute(
                    """
                    UPDATE jobs
                    SET cancel_requested = 1,
                        updated_at = ?
                    WHERE job_id = ?;
                    """,
                    (ts, job_id),
                )

            updated_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            assert updated_row is not None
            return self._row_to_job(updated_row)

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_duration_sec: float,
        now: float | None = None,
    ) -> JobRecord | None:
        """Atomically claim the oldest `queued` job with a fresh `lease_id` and lease expiration."""
        ts = now if now is not None else time.time()
        lease_expires = ts + lease_duration_sec
        lease_id = f"lease_{uuid.uuid4().hex}"

        with self._immediate_tx() as conn:
            row = conn.execute(
                """
                SELECT job_id FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1;
                """
            ).fetchone()
            if row is None:
                return None

            job_id = str(row["job_id"])
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    worker_id = ?,
                    lease_id = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?,
                    progress = 0.0
                WHERE job_id = ? AND status = 'queued';
                """,
                (worker_id, lease_id, lease_expires, ts, ts, ts, job_id),
            )
            claimed_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            assert claimed_row is not None
            return self._row_to_job(claimed_row)

    def renew_lease(
        self,
        job_id: str,
        lease_id: str,
        *,
        lease_duration_sec: float,
        progress: float | None = None,
        now: float | None = None,
    ) -> tuple[bool, bool]:
        """Renew the lease for `(job_id, lease_id)` and return `(still_owned, cancel_requested)`."""
        ts = now if now is not None else time.time()
        lease_expires = ts + lease_duration_sec
        with self._immediate_tx() as conn:
            row = conn.execute(
                "SELECT status, lease_id, cancel_requested FROM jobs WHERE job_id = ?;",
                (job_id,),
            ).fetchone()
            if row is None or str(row["status"]) != "running" or str(row["lease_id"] or "") != lease_id:
                return False, True

            cancel_req = bool(row["cancel_requested"])
            if progress is not None:
                conn.execute(
                    """
                    UPDATE jobs
                    SET lease_expires_at = ?,
                        heartbeat_at = ?,
                        progress = ?,
                        updated_at = ?
                    WHERE job_id = ? AND status = 'running' AND lease_id = ?;
                    """,
                    (lease_expires, ts, max(0.0, min(0.99, progress)), ts, job_id, lease_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET lease_expires_at = ?,
                        heartbeat_at = ?,
                        updated_at = ?
                    WHERE job_id = ? AND status = 'running' AND lease_id = ?;
                    """,
                    (lease_expires, ts, ts, job_id, lease_id),
                )
            return True, cancel_req

    def check_attempt_aborted(self, job_id: str, lease_id: str) -> tuple[bool, bool]:
        """Fast read check returning `(cancel_requested, lease_lost)` at block boundaries."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status, lease_id, cancel_requested FROM jobs WHERE job_id = ?;",
                (job_id,),
            ).fetchone()
            if row is None:
                return True, True
            status = str(row["status"])
            current_lease = str(row["lease_id"] or "")
            cancel_req = bool(row["cancel_requested"])
            if status != "running" or current_lease != lease_id:
                return cancel_req, True
            return cancel_req, False
        finally:
            conn.close()

    def finalize_job_success(
        self,
        job_id: str,
        lease_id: str,
        *,
        result_path: Path,
        report_path: Path,
        result_metadata: dict[str, Any],
        retention_sec: float,
        commit_files_fn: Callable[[], None],
        now: float | None = None,
    ) -> tuple[JobRecord | None, bool]:
        """Atomically move verified files and mark the job `succeeded` if and only if `lease_id` is still active.

        Guarantees:
        1. If the lease was lost (e.g. reclaimed by another worker) or cancellation was requested,
           `commit_files_fn` is NOT called and `(current_job, False)` is returned.
        2. If `commit_files_fn()` raises an exception (e.g. disk full or permission error), the DB
           transaction rolls back and the job is NOT marked succeeded.
        """
        ts = now if now is not None else time.time()
        expires = ts + retention_sec
        with self._immediate_tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            if row is None:
                return None, False

            job = self._row_to_job(row)
            if job.status != "running" or job.lease_id != lease_id or job.cancel_requested:
                return job, False

            # Perform atomic file moves into final results directory while holding the IMMEDIATE lock
            commit_files_fn()

            meta_with_expiry = dict(result_metadata)
            meta_with_expiry["expires_at"] = format_utc_iso(expires)

            conn.execute(
                """
                UPDATE jobs
                SET status = 'succeeded',
                    progress = 1.0,
                    result_path = ?,
                    report_path = ?,
                    result_metadata_json = ?,
                    error_json = NULL,
                    worker_id = NULL,
                    lease_id = NULL,
                    lease_expires_at = NULL,
                    finished_at = ?,
                    updated_at = ?,
                    expires_at = ?,
                    result_expired = 0
                WHERE job_id = ? AND status = 'running' AND lease_id = ?;
                """,
                (
                    str(result_path),
                    str(report_path),
                    json.dumps(meta_with_expiry, ensure_ascii=False),
                    ts,
                    ts,
                    expires,
                    job_id,
                    lease_id,
                ),
            )
            updated_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            assert updated_row is not None
            return self._row_to_job(updated_row), True

    def finalize_job_canceled(
        self,
        job_id: str,
        lease_id: str,
        *,
        now: float | None = None,
    ) -> JobRecord | None:
        """Mark a running job as `canceled` after the Worker aborts at a block boundary and cleans up."""
        ts = now if now is not None else time.time()
        with self._immediate_tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            if row is None:
                return None
            job = self._row_to_job(row)
            if job.status in TERMINAL_JOB_STATUSES:
                return job
            if job.status == "running" and (job.lease_id == lease_id or job.cancel_requested):
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'canceled',
                        cancel_requested = 1,
                        worker_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        finished_at = ?,
                        updated_at = ?
                    WHERE job_id = ?;
                    """,
                    (ts, ts, job_id),
                )
            updated_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            return self._row_to_job(updated_row) if updated_row is not None else None

    def finalize_job_failed(
        self,
        job_id: str,
        lease_id: str,
        *,
        error_code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> JobRecord | None:
        """Record an attempt failure (or re-queue if `retryable=True` and retries remain)."""
        ts = now if now is not None else time.time()
        err_payload: dict[str, Any] = {
            "code": error_code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }
        with self._immediate_tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            if row is None:
                return None
            job = self._row_to_job(row)
            if job.status != "running" or job.lease_id != lease_id:
                return job

            if job.cancel_requested:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'canceled',
                        cancel_requested = 1,
                        worker_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        finished_at = ?,
                        updated_at = ?
                    WHERE job_id = ?;
                    """,
                    (ts, ts, job_id),
                )
            elif retryable and job.attempts <= job.max_retries:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued',
                        progress = 0.0,
                        error_json = ?,
                        worker_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE job_id = ?;
                    """,
                    (json.dumps(err_payload, ensure_ascii=False), ts, job_id),
                )
            else:
                err_payload["retryable"] = False
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        error_json = ?,
                        worker_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        finished_at = ?,
                        updated_at = ?
                    WHERE job_id = ?;
                    """,
                    (json.dumps(err_payload, ensure_ascii=False), ts, ts, job_id),
                )
            updated_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,)).fetchone()
            return self._row_to_job(updated_row) if updated_row is not None else None

    def recover_expired_leases(self, *, now: float | None = None) -> list[str]:
        """Reclaim `running` jobs whose worker lease has expired (docs/plan.md Section 11 & Task 2).

        - If `cancel_requested` is set: marks the job `canceled`.
        - Else if `attempts <= max_retries`: resets the job to `queued` so it will be retried.
        - Else (`attempts > max_retries`): marks the job `failed` with `LEASE_EXPIRED_MAX_RETRIES`.
        """
        ts = now if now is not None else time.time()
        recovered_ids: list[str] = []
        with self._immediate_tx() as conn:
            expired_rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY created_at ASC;
                """,
                (ts,),
            ).fetchall()

            for row in expired_rows:
                job = self._row_to_job(row)
                recovered_ids.append(job.job_id)
                if job.cancel_requested:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'canceled',
                            worker_id = NULL,
                            lease_id = NULL,
                            lease_expires_at = NULL,
                            finished_at = ?,
                            updated_at = ?
                        WHERE job_id = ?;
                        """,
                        (ts, ts, job.job_id),
                    )
                elif job.attempts <= job.max_retries:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'queued',
                            progress = 0.0,
                            worker_id = NULL,
                            lease_id = NULL,
                            lease_expires_at = NULL,
                            updated_at = ?
                        WHERE job_id = ?;
                        """,
                        (ts, job.job_id),
                    )
                else:
                    err_payload = {
                        "code": "LEASE_EXPIRED_MAX_RETRIES",
                        "message": (
                            f"Worker lease expired on attempt {job.attempts} and maximum retry limit "
                            f"({job.max_retries}) has been exhausted."
                        ),
                        "retryable": False,
                        "details": {"attempts": job.attempts, "max_retries": job.max_retries},
                    }
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'failed',
                            error_json = ?,
                            worker_id = NULL,
                            lease_id = NULL,
                            lease_expires_at = NULL,
                            finished_at = ?,
                            updated_at = ?
                        WHERE job_id = ?;
                        """,
                        (json.dumps(err_payload, ensure_ascii=False), ts, ts, job.job_id),
                    )
        return recovered_ids

    @contextlib.contextmanager
    def acquire_result_access(self, job_id: str, *, now: float | None = None) -> Iterator[JobRecord]:
        """Validate a job's result/report is available and not expired, and guard against cleanup during access."""
        ts = now if now is not None else time.time()
        with self._download_lock:
            job = self.get_job(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            if job.result_expired or ts >= job.expires_at:
                raise ResourceExpiredError("result", job_id, job.expires_at)
            if job.status != "succeeded":
                raise JobStateConflictError(job_id, job.status)
            self._active_downloads[job_id] = self._active_downloads.get(job_id, 0) + 1

        try:
            yield job
        finally:
            with self._download_lock:
                cnt = self._active_downloads.get(job_id, 1) - 1
                if cnt <= 0:
                    self._active_downloads.pop(job_id, None)
                else:
                    self._active_downloads[job_id] = cnt

    def cleanup_expired_resources(self, *, now: float | None = None) -> tuple[list[str], list[str]]:
        """Delete expired job results and expired unused assets (docs/plan.md Section 11 & Task 2).

        Invariants:
        - Never deletes an asset referenced by any `queued` or `running` job.
        - Never deletes a job result for a `queued` or `running` job, or a result currently being downloaded.
        - Marks `result_expired = 1` / `expired = 1` in SQLite so subsequent download requests get a
          deterministic `410 Gone` (`RESULT_EXPIRED` / `ASSET_EXPIRED`) rather than 404.
        """
        ts = now if now is not None else time.time()
        cleaned_jobs: list[str] = []
        cleaned_assets: list[str] = []

        with self._immediate_tx() as conn:
            expired_job_rows = conn.execute(
                """
                SELECT job_id, result_path, report_path, params_json
                FROM jobs
                WHERE status IN ('succeeded', 'failed', 'canceled')
                  AND result_expired = 0
                  AND expires_at <= ?;
                """,
                (ts,),
            ).fetchall()

            for row in expired_job_rows:
                jid = str(row["job_id"])
                with self._download_lock:
                    if self._active_downloads.get(jid, 0) > 0:
                        continue
                params = AuroraParams.model_validate_json(str(row["params_json"]))
                if params.preview is not None and row["result_path"]:
                    with contextlib.suppress(OSError):
                        Path(str(row["result_path"])).with_suffix(".original.wav").unlink(missing_ok=True)
                for col in ("result_path", "report_path"):
                    raw_p = row[col]
                    if raw_p:
                        with contextlib.suppress(OSError):
                            Path(str(raw_p)).unlink(missing_ok=True)
                conn.execute(
                    "UPDATE jobs SET result_expired = 1, updated_at = ? WHERE job_id = ?;",
                    (ts, jid),
                )
                cleaned_jobs.append(jid)

            expired_asset_rows = conn.execute(
                """
                SELECT asset_id, file_path
                FROM assets
                WHERE expired = 0
                  AND expires_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM jobs
                      WHERE jobs.asset_id = assets.asset_id
                        AND jobs.status IN ('queued', 'running')
                  );
                """,
                (ts,),
            ).fetchall()

            for row in expired_asset_rows:
                aid = str(row["asset_id"])
                raw_p = row["file_path"]
                if raw_p:
                    with contextlib.suppress(OSError):
                        Path(str(raw_p)).unlink(missing_ok=True)
                conn.execute("UPDATE assets SET expired = 1 WHERE asset_id = ?;", (aid,))
                cleaned_assets.append(aid)

        return cleaned_jobs, cleaned_assets


    def cleanup_attempt_directories(self, tmp_dir: Path) -> list[str]:
        """Reclaim fenced/terminal attempts, including empty SIGKILL leftovers.

        Recover leases first. A directory for the current running attempt is always
        protected, even at the expiration boundary; recovery must fence it before
        deletion. The write transaction serializes this check with claim/renewal.
        Only our exact directory namespace is considered; never follow symlinks.
        """
        cleaned: list[str] = []
        pattern = re.compile(r"attempt_(job_[0-9a-f]{16})_([1-9][0-9]*)_(lease_[0-9a-f]{32})")
        for path in tmp_dir.glob("attempt_*"):
            match = pattern.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_dir():
                continue
            job_id, attempt, lease_id = match.groups()
            with self._immediate_tx() as conn:
                row = conn.execute(
                    "SELECT status, attempts, lease_id FROM jobs WHERE job_id = ?;", (job_id,)
                ).fetchone()
                if row is None:
                    continue
                if row["status"] == "running" and row["attempts"] == int(attempt) and row["lease_id"] == lease_id:
                    continue
                try:
                    shutil.rmtree(path)
                except OSError:
                    continue  # Retry on the next cleanup cycle.
                cleaned.append(path.name)
        return cleaned
