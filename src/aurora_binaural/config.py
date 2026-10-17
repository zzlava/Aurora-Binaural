"""Environment-driven configuration for Aurora Binaural HTTP API and Worker (docs/plan.md Sections 10 & 11).

All operational parameters are read from environment variables with safe local defaults:
- `AURORA_API_KEY`: Bearer authentication key for API endpoints (required for `/v1/*`).
- `AURORA_HOST`: Bind address for the HTTP server (default: `127.0.0.1`).
- `AURORA_PORT`: Bind port for the HTTP server (default: `8000`).
- `AURORA_DATA_DIR`: Runtime directory for SQLite DB, uploaded assets, results, and temp files.
- `AURORA_DB_PATH`: Explicit SQLite database path (defaults to `{AURORA_DATA_DIR}/aurora.db`).
- `AURORA_HRTF_DIR`: Directory containing fixed-version `.sofa` HRTF datasets.
- `AURORA_MAX_UPLOAD_BYTES`: Maximum uploaded asset size in bytes (default: 524288000 = 500 MiB).
- `AURORA_MAX_DURATION_SEC`: Maximum input audio duration in seconds (default: 7200.0 = 2 hours).
- `AURORA_JOB_TIMEOUT_SEC`: Maximum wall-clock execution time per job attempt in seconds (default: 1800.0).
- `AURORA_RETENTION_SEC`: Retention period (TTL) for uploaded assets and job results in seconds (default: 86400.0).
- `AURORA_WORKER_CONCURRENCY`: Number of concurrent job execution threads in a Worker process (default: 1).
- `AURORA_LEASE_DURATION_SEC`: Worker task lease duration in seconds (default: 30.0).
- `AURORA_HEARTBEAT_INTERVAL_SEC`: Worker lease heartbeat renewal interval in seconds (default: 5.0).
- `AURORA_CLEANUP_INTERVAL_SEC`: Periodic expired-file cleanup interval in seconds (default: 60.0).
- `AURORA_WORKER_POLL_INTERVAL_SEC`: Worker queue polling interval in seconds (default: 0.15).
- `AURORA_MAX_RETRIES`: Maximum retry count after an initial attempt fails/expires (default: 2).
- `AURORA_WORKER_BLOCK_DELAY_MS`: Optional per-block delay in milliseconds for testing cancellation/recovery (default: 0.0).
- `AURORA_STRICT_FFMPEG`: Reject M4A/AAC when FFmpeg < 7.0 if set to 1/true (default: false).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from aurora_binaural.resources import asset_directory


def _env_str(env: Mapping[str, str], key: str, default: str) -> str:
    val = env.get(key)
    if val is None or not val.strip():
        return default
    return val.strip()


def _env_int(env: Mapping[str, str], key: str, default: int, *, minimum: int = 1) -> int:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Environment variable {key} must be an integer, got '{raw}'.") from exc
    if parsed < minimum:
        raise ValueError(f"Environment variable {key} must be >= {minimum}, got {parsed}.")
    return parsed


def _env_float(env: Mapping[str, str], key: str, default: float, *, minimum: float = 0.0) -> float:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Environment variable {key} must be a number, got '{raw}'.") from exc
    if parsed < minimum:
        raise ValueError(f"Environment variable {key} must be >= {minimum}, got {parsed}.")
    return parsed


def _env_bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ServiceConfig:
    """Immutable runtime configuration for the Aurora Binaural API and Worker."""

    engine: str = field(default_factory=lambda: os.environ.get("AURORA_ENGINE", "rust"))
    api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("./var/aurora")
    db_path: Path = Path("./var/aurora/aurora.db")
    hrtf_dir: Path = field(default_factory=lambda: asset_directory("hrtf"))
    max_upload_bytes: int = 524_288_000
    max_duration_sec: float = 7200.0
    job_timeout_sec: float = 1800.0
    retention_sec: float = 86400.0
    worker_concurrency: int = 1
    lease_duration_sec: float = 30.0
    heartbeat_interval_sec: float = 5.0
    cleanup_interval_sec: float = 60.0
    worker_poll_interval_sec: float = 0.15
    max_retries: int = 2
    worker_block_delay_ms: float = 0.0
    strict_ffmpeg: bool = False
    cors_origins: tuple[str, ...] = ()

    @property
    def assets_dir(self) -> Path:
        """Directory where uploaded source assets are stored."""
        return self.data_dir / "assets"

    @property
    def results_dir(self) -> Path:
        """Directory where finalized job outputs and reports are stored."""
        return self.data_dir / "results"

    @property
    def tmp_dir(self) -> Path:
        """Directory for streaming upload staging and isolated per-attempt worker directories."""
        return self.data_dir / "tmp"

    def ensure_directories(self) -> None:
        """Create required runtime directories if they do not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_local_gui(cls) -> ServiceConfig:
        """Share the local GUI database location with CLI saved-preset selection."""
        env = dict(os.environ)
        env.setdefault("AURORA_DATA_DIR", str(Path.home() / "Library" / "Caches" / "AuroraBinaural"))
        return cls.from_env(env)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ServiceConfig:
        """Load `ServiceConfig` from `os.environ` (or an explicit mapping for tests)."""
        env = environ if environ is not None else os.environ
        raw_key = env.get("AURORA_API_KEY")
        api_key = raw_key.strip() if raw_key and raw_key.strip() else None

        data_dir = Path(_env_str(env, "AURORA_DATA_DIR", "./var/aurora")).resolve()
        raw_db = env.get("AURORA_DB_PATH")
        db_path = Path(raw_db.strip()).resolve() if raw_db and raw_db.strip() else (data_dir / "aurora.db")
        hrtf_dir = Path(_env_str(env, "AURORA_HRTF_DIR", str(asset_directory("hrtf")))).resolve()

        engine = _env_str(env, "AURORA_ENGINE", "rust").lower()
        if engine not in ("rust", "stub"):
            raise ValueError("AURORA_ENGINE must be rust or stub.")
        return cls(
            engine=engine,
            api_key=api_key,
            host=_env_str(env, "AURORA_HOST", "127.0.0.1"),
            port=_env_int(env, "AURORA_PORT", 8000, minimum=1),
            data_dir=data_dir,
            db_path=db_path,
            hrtf_dir=hrtf_dir,
            max_upload_bytes=_env_int(env, "AURORA_MAX_UPLOAD_BYTES", 524_288_000, minimum=1),
            max_duration_sec=_env_float(env, "AURORA_MAX_DURATION_SEC", 7200.0, minimum=0.001),
            job_timeout_sec=_env_float(env, "AURORA_JOB_TIMEOUT_SEC", 1800.0, minimum=0.01),
            retention_sec=_env_float(env, "AURORA_RETENTION_SEC", 86400.0, minimum=0.01),
            worker_concurrency=_env_int(env, "AURORA_WORKER_CONCURRENCY", 1, minimum=1),
            lease_duration_sec=_env_float(env, "AURORA_LEASE_DURATION_SEC", 30.0, minimum=0.05),
            heartbeat_interval_sec=_env_float(env, "AURORA_HEARTBEAT_INTERVAL_SEC", 5.0, minimum=0.01),
            cleanup_interval_sec=_env_float(env, "AURORA_CLEANUP_INTERVAL_SEC", 60.0, minimum=0.01),
            worker_poll_interval_sec=_env_float(env, "AURORA_WORKER_POLL_INTERVAL_SEC", 0.15, minimum=0.005),
            max_retries=_env_int(env, "AURORA_MAX_RETRIES", 2, minimum=0),
            worker_block_delay_ms=_env_float(env, "AURORA_WORKER_BLOCK_DELAY_MS", 0.0, minimum=0.0),
            strict_ffmpeg=_env_bool(env, "AURORA_STRICT_FFMPEG", False),
            cors_origins=tuple(value.strip() for value in env.get("AURORA_CORS_ORIGINS", "").split(",") if value.strip()),
        )
