"""Aurora Binaural FastAPI HTTP Service (docs/plan.md Sections 10, 11, and 16.4).

Implements the Section 10 HTTP API contract:
- `GET /health`: liveness check (no internal paths exposed)
- `GET /ready`: readiness check for SQLite task store, Worker heartbeat, and HRTF data
- `GET /v1/presets`: spatial presets, parameter bounds, and versions
- `POST /v1/assets`: streaming audio asset upload with size & duration limits
- `POST /v1/previews`: create a source-time BS.1770 matched original/effect excerpt job
- `GET /v1/jobs/{id}/original`: download the matched original excerpt
- `POST /v1/jobs`: create asynchronous job (HTTP 202) with `Idempotency-Key` support (HTTP 409 on conflict)
- `GET /v1/jobs/{id}`: query job status, progress, result metadata, and structured error
- `POST /v1/jobs/{id}/cancel`: idempotent job cancellation
- `GET /v1/jobs/{id}/result`: stream-download rendered audio file (HTTP 410 when expired)
- `GET /v1/jobs/{id}/report`: download structured JSON conversion report (HTTP 410 when expired)
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import urllib.parse
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from aurora_binaural.catalog import CATALOG, DEFAULT_PRESET_ID
from aurora_binaural.config import ServiceConfig
from aurora_binaural.engine import engine_status
from aurora_binaural.exit_codes import AuroraError, ExitCode
from aurora_binaural.media import probe_audio
from aurora_binaural.params import (
    PRESET_CATALOG,
    AuroraParams,
    InputSpec,
    OutputFormat,
    OutputSpec,
    PresetMetadata,
    PreviewRequest,
    PreviewSpec,
    TimingSpec,
)
from aurora_binaural.report import ConversionReport
from aurora_binaural.storage import (
    ApiServiceError,
    JobStatus,
    ResourceExpiredError,
    TaskStore,
    UnauthorizedError,
)
from aurora_binaural.user_presets import UserPreset, UserPresetBundle, UserPresetName, UserPresetSpec, UserPresetStore

API_VERSION = "v1"

_bearer_scheme = HTTPBearer(auto_error=False, description="Bearer API Key read from AURORA_API_KEY")


class ErrorDetailModel(BaseModel):
    """Structured error object returned by all HTTP error responses (Section 10)."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error description.")
    exit_code: int = Field(description="Corresponding CLI exit code (2, 3, 4, 5, 130).")
    retryable: bool = Field(description="Whether the client may retry the operation.")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional structured error context.")


class ErrorResponseModel(BaseModel):
    """Envelope for HTTP API error responses."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[False] = False
    error: ErrorDetailModel


class HealthResponseModel(BaseModel):
    """Response model for `GET /health` (strictly no internal paths)."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    status: Literal["ok"] = "ok"
    service: Literal["aurora-binaural-api"] = "aurora-binaural-api"
    api_version: Literal["v1"] = "v1"


class ReadyChecksModel(BaseModel):
    """Individual subsystem readiness flags for `GET /ready`."""

    model_config = ConfigDict(extra="forbid")

    database: bool = Field(description="True if SQLite task store is reachable and initialized.")
    worker: bool = Field(description="True if at least one Worker has an active heartbeat.")
    hrtf_data: bool = Field(description="True if fixed-version HRTF .sofa data is present.")


class ReadyResponseModel(BaseModel):
    """Response model for `GET /ready` (strictly no internal paths)."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: Literal["ready", "not_ready"]
    checks: ReadyChecksModel


class PresetsResponseModel(BaseModel):
    """Response model for `GET /v1/presets`."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    api_version: Literal["v1"] = "v1"
    engine: dict[str, Any]
    default_preset: str
    presets: list[PresetMetadata]
    parameter_ranges: dict[str, Any]


class AssetUploadResponseModel(BaseModel):
    """Response model for `POST /v1/assets`."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    asset_id: str
    filename: str
    sha256: str
    size_bytes: int
    format: str
    container: str
    codec: str
    sample_rate: int
    channels: int
    samples: int
    duration_seconds: float
    created_at: str | None
    expires_at: str | None


class JobResultMetadataModel(BaseModel):
    """Metadata summary embedded in `JobStatusResponseModel` when `status == 'succeeded'`."""

    model_config = ConfigDict(extra="forbid")

    format: str
    container: str
    codec: str
    sample_rate: int
    channels: int
    samples: int
    duration_seconds: float
    bit_depth: int | None = None
    size_bytes: int
    sha256: str
    expires_at: str | None = None


class JobErrorSummaryModel(BaseModel):
    """Error summary embedded in `JobStatusResponseModel` when `status == 'failed'`."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class JobStatusResponseModel(BaseModel):
    """Response model for `POST /v1/jobs`, `GET /v1/jobs/{id}`, and `POST /v1/jobs/{id}/cancel`."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    job_id: str
    asset_id: str
    preview: PreviewSpec | None = None
    status: JobStatus
    cancel_requested: bool = Field(
        description="True if cancellation was requested (transitional sub-field when status='running')."
    )
    canceling: bool = Field(
        description="True when status=='running' and cancel_requested==True (Section 10 transitional sub-field)."
    )
    progress: float = Field(ge=0.0, le=1.0)
    attempts: int = Field(ge=0)
    max_retries: int = Field(ge=0)
    created_at: str | None
    started_at: str | None = None
    finished_at: str | None = None
    expires_at: str | None = None
    result_metadata: JobResultMetadataModel | None = None
    error: JobErrorSummaryModel | None = None


def check_hrtf_ready(hrtf_dir: Path) -> bool:
    """Return True if `hrtf_dir` exists and contains at least one non-empty `.sofa` file."""
    if not hrtf_dir.exists() or not hrtf_dir.is_dir():
        return False
    try:
        for p in hrtf_dir.rglob("*.sofa"):
            if p.is_file() and p.stat().st_size > 0:
                return True
    except OSError:
        return False
    return False


def _sanitize_filename(raw_name: str | None) -> tuple[str, str]:
    """Sanitize an uploaded filename and extract a safe file extension."""
    if not raw_name:
        return "upload.wav", ".wav"
    unquoted = urllib.parse.unquote(raw_name).strip()
    base = Path(unquoted).name
    if not base or base in (".", ".."):
        return "upload.wav", ".wav"
    ext = Path(base).suffix.lower()
    if ext not in (".wav", ".flac", ".m4a", ".alac", ".aiff", ".aif", ".mp3", ".aac"):
        ext = ".wav"
    return base, ext


def _media_type_for_format(fmt: OutputFormat) -> str:
    return OutputSpec(format=fmt).media_type


def create_app(
    config: ServiceConfig | None = None,
    *,
    store: TaskStore | None = None,
    web_dir: Path | None = None,
    local_gui: bool = False,
) -> FastAPI:
    """Create and configure the Aurora Binaural FastAPI application."""
    cfg = config or ServiceConfig.from_env()
    cfg.ensure_directories()
    task_store = store or TaskStore(cfg.db_path)
    user_presets = UserPresetStore(task_store)

    app = FastAPI(
        title="Aurora Binaural API",
        version="0.4.0",
        description=(
            "Aurora Binaural spatial audio processing HTTP API (docs/plan.md Section 10). "
            "Provides streaming asset uploads, persistent SQLite asynchronous job queue with "
            "Idempotency-Key protection, block-boundary cancellation, and timeline-verified outputs."
        ),
    )
    app.state.config = cfg
    app.state.store = task_store
    if cfg.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware, allow_origins=list(cfg.cors_origins),
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Asset-Filename"],
            expose_headers=["Content-Disposition"],
        )

    def _verify_token_string(token: str | None) -> None:
        expected = cfg.api_key
        if not expected or not token:
            raise UnauthorizedError()
        if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            raise UnauthorizedError()

    def require_api_key(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    ) -> str:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise UnauthorizedError()
        _verify_token_string(credentials.credentials)
        return credentials.credentials

    def optional_api_key(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    ) -> None:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr is not None:
            if credentials is None or credentials.scheme.lower() != "bearer":
                raise UnauthorizedError()
            _verify_token_string(credentials.credentials)

    @app.exception_handler(ApiServiceError)
    async def _handle_api_service_error(_req: Request, exc: ApiServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(AuroraError)
    async def _handle_aurora_error(_req: Request, exc: AuroraError) -> JSONResponse:
        status_map = {
            ExitCode.PARAM_ERROR: 422,
            ExitCode.INPUT_ERROR: 422,
            ExitCode.PROCESSING_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
            ExitCode.REMOTE_ERROR: status.HTTP_502_BAD_GATEWAY,
            ExitCode.CANCELED: status.HTTP_409_CONFLICT,
        }
        http_code = status_map.get(exc.exit_code, status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=http_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        err = ApiServiceError(
            "Request parameter validation failed.",
            http_status=422,
            error_code="INVALID_PARAMETERS",
            exit_code=ExitCode.PARAM_ERROR,
            retryable=False,
            details={"errors": json.loads(json.dumps(exc.errors(), default=str))},
        )
        return JSONResponse(status_code=err.http_status, content=err.to_dict())

    if local_gui:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])

        @app.get("/gui/session", include_in_schema=False)
        async def gui_session(request: Request) -> JSONResponse:
            origin = request.headers.get("origin")
            expected_origin = f"http://{request.headers.get('host', '')}"
            if (request.headers.get("x-aurora-gui") != "1"
                    or (origin is not None and origin != expected_origin)
                    or request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none")
                    or request.client is None or request.client.host not in ("127.0.0.1", "::1")):
                raise UnauthorizedError()
            return JSONResponse({"api_key": cfg.api_key}, headers={"Cache-Control": "no-store"})

    @app.get(
        "/health",
        response_model=HealthResponseModel,
        summary="Process liveness check",
        tags=["system"],
    )
    async def get_health() -> HealthResponseModel:
        """Liveness check endpoint. Never exposes internal filesystem paths."""
        return HealthResponseModel()

    @app.get(
        "/ready",
        response_model=ReadyResponseModel,
        responses={503: {"model": ReadyResponseModel}, 401: {"model": ErrorResponseModel}},
        dependencies=[Depends(optional_api_key)],
        summary="Check task storage, worker heartbeat, and HRTF dataset readiness",
        tags=["system"],
    )
    async def get_ready() -> JSONResponse:
        """Readiness check verifying SQLite database, active Worker heartbeat, and HRTF data."""
        db_ok = task_store.check_health()
        worker_ok = task_store.has_active_worker()
        hrtf_ok = check_hrtf_ready(cfg.hrtf_dir)
        all_ok = db_ok and worker_ok and hrtf_ok
        payload = ReadyResponseModel(
            ok=all_ok,
            status="ready" if all_ok else "not_ready",
            checks=ReadyChecksModel(
                database=db_ok,
                worker=worker_ok,
                hrtf_data=hrtf_ok,
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload.model_dump(mode="json"),
        )

    @app.get(
        "/v1/presets",
        response_model=PresetsResponseModel,
        responses={401: {"model": ErrorResponseModel}},
        dependencies=[Depends(require_api_key)],
        summary="List spatial presets, parameter ranges, and versions",
        tags=["v1"],
    )
    async def get_presets() -> PresetsResponseModel:
        """Return built-in spatial presets, parameter ranges, and version metadata."""
        return PresetsResponseModel(
            engine=engine_status(cfg.engine),
            default_preset=DEFAULT_PRESET_ID,
            presets=list(PRESET_CATALOG.values()),
            parameter_ranges={
                "strength": {"min": 0.0, "max": 1.0, "dry_wet_mixing_allowed": False},
                "seed": {"min": 0, "max": 4294967295, "default": 20261016},
                "sample_rate": ["source", 44100, 48000, 96000],
                "wav_bit_depth": [16, 24, "32f"],
                "lossless_bit_depth": [16, 24],
                "output_format": [fmt.value for fmt in OutputFormat],
                "lossy_bitrate_kbps": {"min": 64, "max": 512, "default": 256},
            },
        )

    @app.get("/v1/user-presets", response_model=list[UserPreset], dependencies=[Depends(require_api_key)], tags=["v1"])
    async def list_user_presets() -> list[UserPreset]:
        return user_presets.list_presets()

    @app.post("/v1/user-presets", status_code=201, response_model=UserPreset,
              dependencies=[Depends(require_api_key)], tags=["v1"])
    async def save_user_preset(spec: UserPresetSpec) -> UserPreset:
        return user_presets.create(spec)

    @app.get("/v1/user-presets/export", response_model=UserPresetBundle,
             dependencies=[Depends(require_api_key)], tags=["v1"])
    async def export_user_presets() -> UserPresetBundle:
        return user_presets.export_bundle()

    @app.post("/v1/user-presets/import", status_code=201, response_model=list[UserPreset],
              dependencies=[Depends(require_api_key)], tags=["v1"])
    async def import_user_presets(bundle: UserPresetBundle) -> list[UserPreset]:
        return user_presets.import_bundle(bundle)

    @app.patch("/v1/user-presets/{identity}", response_model=UserPreset,
               dependencies=[Depends(require_api_key)], tags=["v1"])
    async def rename_user_preset(identity: str, name: UserPresetName) -> UserPreset:
        return user_presets.rename(identity, name)

    @app.delete("/v1/user-presets/{identity}", dependencies=[Depends(require_api_key)], tags=["v1"])
    async def delete_user_preset(identity: str) -> dict[str, bool]:
        user_presets.delete(identity)
        return {"deleted": True}

    @app.post(
        "/v1/assets",
        status_code=status.HTTP_201_CREATED,
        response_model=AssetUploadResponseModel,
        responses={
            400: {"model": ErrorResponseModel},
            401: {"model": ErrorResponseModel},
            413: {"model": ErrorResponseModel},
            422: {"model": ErrorResponseModel},
        },
        dependencies=[Depends(require_api_key)],
        summary="Stream-upload an audio asset and return asset ID and probe metadata",
        tags=["v1"],
    )
    async def upload_asset(
        request: Request,
        x_asset_filename: Annotated[str | None, Header()] = None,
        x_asset_sha256: Annotated[str | None, Header()] = None,
    ) -> AssetUploadResponseModel:
        """Stream-upload an audio asset, enforcing upload size and duration limits."""
        content_len_hdr = request.headers.get("content-length")
        if content_len_hdr is not None:
            with contextlib.suppress(ValueError):
                if int(content_len_hdr) > cfg.max_upload_bytes:
                    raise ApiServiceError(
                        f"Upload size ({int(content_len_hdr)} bytes) exceeds limit of {cfg.max_upload_bytes} bytes.",
                        http_status=status.HTTP_413_CONTENT_TOO_LARGE,
                        error_code="ASSET_TOO_LARGE",
                        exit_code=ExitCode.INPUT_ERROR,
                        retryable=False,
                        details={"max_upload_bytes": cfg.max_upload_bytes},
                    )

        content_type = request.headers.get("content-type", "").lower()
        cfg.ensure_directories()
        tmp_upload_path = cfg.tmp_dir / f".upload_tmp_{uuid.uuid4().hex}"
        hasher = hashlib.sha256()
        total_bytes = 0
        raw_filename = x_asset_filename

        try:
            if content_type.startswith("multipart/form-data"):
                form = await request.form()
                upload_obj = form.get("file")
                if upload_obj is None or isinstance(upload_obj, str):
                    raise ApiServiceError(
                        "Multipart upload requires a 'file' field.",
                        http_status=status.HTTP_400_BAD_REQUEST,
                        error_code="MISSING_UPLOAD_FILE",
                        exit_code=ExitCode.INPUT_ERROR,
                    )
                if not raw_filename and getattr(upload_obj, "filename", None):
                    raw_filename = str(upload_obj.filename)
                with tmp_upload_path.open("wb") as out_f:
                    while True:
                        chunk = await upload_obj.read(65536)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > cfg.max_upload_bytes:
                            raise ApiServiceError(
                                f"Uploaded asset exceeds maximum allowed size of {cfg.max_upload_bytes} bytes.",
                                http_status=status.HTTP_413_CONTENT_TOO_LARGE,
                                error_code="ASSET_TOO_LARGE",
                                exit_code=ExitCode.INPUT_ERROR,
                                retryable=False,
                                details={"max_upload_bytes": cfg.max_upload_bytes},
                            )
                        hasher.update(chunk)
                        out_f.write(chunk)
                    out_f.flush()
                    os.fsync(out_f.fileno())
            else:
                with tmp_upload_path.open("wb") as out_f:
                    async for chunk in request.stream():
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > cfg.max_upload_bytes:
                            raise ApiServiceError(
                                f"Uploaded asset exceeds maximum allowed size of {cfg.max_upload_bytes} bytes.",
                                http_status=status.HTTP_413_CONTENT_TOO_LARGE,
                                error_code="ASSET_TOO_LARGE",
                                exit_code=ExitCode.INPUT_ERROR,
                                retryable=False,
                                details={"max_upload_bytes": cfg.max_upload_bytes},
                            )
                        hasher.update(chunk)
                        out_f.write(chunk)
                    out_f.flush()
                    os.fsync(out_f.fileno())

            if total_bytes == 0:
                raise ApiServiceError(
                    "Uploaded asset body is empty.",
                    http_status=status.HTTP_400_BAD_REQUEST,
                    error_code="EMPTY_ASSET_UPLOAD",
                    exit_code=ExitCode.INPUT_ERROR,
                    retryable=False,
                )

            actual_sha256 = hasher.hexdigest()
            if x_asset_sha256 and x_asset_sha256.strip().lower() != actual_sha256.lower():
                raise ApiServiceError(
                    "Uploaded asset SHA-256 digest does not match X-Asset-Sha256 header.",
                    http_status=status.HTTP_400_BAD_REQUEST,
                    error_code="ASSET_CHECKSUM_MISMATCH",
                    exit_code=ExitCode.INPUT_ERROR,
                    retryable=False,
                    details={"expected": x_asset_sha256.strip().lower(), "actual": actual_sha256},
                )

            clean_name, ext = _sanitize_filename(raw_filename)
            staged_with_ext = cfg.tmp_dir / f".upload_probe_{uuid.uuid4().hex}{ext}"
            os.replace(tmp_upload_path, staged_with_ext)
            tmp_upload_path = staged_with_ext

            probe_info = probe_audio(tmp_upload_path)
            if probe_info.duration_seconds > cfg.max_duration_sec:
                raise ApiServiceError(
                    f"Audio duration ({probe_info.duration_seconds:.2f}s) exceeds maximum limit ({cfg.max_duration_sec:.2f}s).",
                    http_status=422,
                    error_code="DURATION_LIMIT_EXCEEDED",
                    exit_code=ExitCode.INPUT_ERROR,
                    retryable=False,
                    details={
                        "duration_seconds": probe_info.duration_seconds,
                        "max_duration_sec": cfg.max_duration_sec,
                    },
                )

            asset_id = f"asset_{uuid.uuid4().hex[:16]}"
            final_asset_path = cfg.assets_dir / f"{asset_id}{ext}"
            os.replace(tmp_upload_path, final_asset_path)

            record = task_store.create_asset(
                asset_id=asset_id,
                filename=clean_name,
                file_path=final_asset_path,
                probe=probe_info,
                retention_sec=cfg.retention_sec,
            )
            return AssetUploadResponseModel.model_validate(record.to_api_dict())
        finally:
            if tmp_upload_path.exists():
                with contextlib.suppress(OSError):
                    tmp_upload_path.unlink(missing_ok=True)

    def enqueue_job(params: AuroraParams, idempotency_key: str | None) -> JobStatusResponseModel:
        if params.input is None or not params.input.asset_id:
            raise ApiServiceError(
                "HTTP job creation requires 'input.asset_id'.",
                http_status=422,
                error_code="INVALID_PARAMETERS",
                exit_code=ExitCode.PARAM_ERROR,
                retryable=False,
            )
        if params.input.path is not None:
            raise ApiServiceError(
                "Local filesystem 'input.path' is not permitted over HTTP API; upload via POST /v1/assets first.",
                http_status=422,
                error_code="INVALID_PARAMETERS",
                exit_code=ExitCode.PARAM_ERROR,
                retryable=False,
            )

        catalog_meta = CATALOG.get(params.preset.id)
        if catalog_meta is None or params.preset.version != catalog_meta.version:
            raise ApiServiceError(
                f"Unsupported preset version '{params.preset.version}' for preset '{params.preset.id.value}'.",
                http_status=422,
                error_code="UNSUPPORTED_PRESET_VERSION",
                exit_code=ExitCode.PARAM_ERROR,
                retryable=False,
            )

        job_record, _created = task_store.create_or_get_job(
            params,
            idempotency_key=idempotency_key,
            retention_sec=cfg.retention_sec,
            max_retries=cfg.max_retries,
        )
        return JobStatusResponseModel.model_validate(job_record.to_api_dict())

    @app.post(
        "/v1/jobs",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobStatusResponseModel,
        responses={
            401: {"model": ErrorResponseModel},
            404: {"model": ErrorResponseModel},
            409: {"model": ErrorResponseModel},
            410: {"model": ErrorResponseModel},
            422: {"model": ErrorResponseModel},
        },
        dependencies=[Depends(require_api_key)],
        summary="Create an asynchronous spatial rendering job (HTTP 202)",
        tags=["v1"],
    )
    async def create_job(
        params: AuroraParams,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JobStatusResponseModel:
        """Create an asynchronous job with `Idempotency-Key` deduplication and conflict protection."""
        if params.preview is not None:
            raise ApiServiceError("试听任务请通过 POST /v1/previews 创建。", http_status=422,
                                  error_code="INVALID_PARAMETERS", exit_code=ExitCode.PARAM_ERROR)
        return enqueue_job(params, idempotency_key)

    @app.post(
        "/v1/previews", status_code=202, response_model=JobStatusResponseModel,
        dependencies=[Depends(require_api_key)], tags=["v1"],
        responses={
            401: {"model": ErrorResponseModel}, 404: {"model": ErrorResponseModel},
            409: {"model": ErrorResponseModel}, 410: {"model": ErrorResponseModel},
            422: {"model": ErrorResponseModel},
        },
        summary="Queue a source-timeline excerpt with a BS.1770 matched original/effect pair",
    )
    async def create_preview(
        request: PreviewRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JobStatusResponseModel:
        params = AuroraParams(
            input=InputSpec(asset_id=request.asset_id), preset=request.preset,
            strength=request.strength, controls=request.controls, control_clipping=request.control_clipping,
            seed=request.seed, output=OutputSpec(bit_depth=32),
            timing=TimingSpec(fade_out_ms=0),
            preview=PreviewSpec(start_seconds=request.start_seconds, duration_seconds=request.duration_seconds),
        )
        asset = task_store.get_asset(request.asset_id)
        if asset is not None and request.start_seconds >= asset.duration_seconds:
            raise ApiServiceError("试听起点必须小于音频时长。", http_status=422,
                                  error_code="PREVIEW_OUT_OF_RANGE", exit_code=ExitCode.PARAM_ERROR)
        return enqueue_job(params, idempotency_key)

    @app.get(
        "/v1/jobs/{id}",
        response_model=JobStatusResponseModel,
        responses={401: {"model": ErrorResponseModel}, 404: {"model": ErrorResponseModel}},
        dependencies=[Depends(require_api_key)],
        summary="Query job status, progress, result metadata, and error",
        tags=["v1"],
    )
    async def get_job_status(id: str) -> JobStatusResponseModel:
        """Return current job status (`queued`, `running`, `succeeded`, `failed`, `canceled`)."""
        job = task_store.get_job(id)
        if job is None:
            raise ApiServiceError(
                f"Job '{id}' was not found.",
                http_status=status.HTTP_404_NOT_FOUND,
                error_code="JOB_NOT_FOUND",
                exit_code=ExitCode.REMOTE_ERROR,
                retryable=False,
                details={"job_id": id},
            )
        return JobStatusResponseModel.model_validate(job.to_api_dict())

    @app.post(
        "/v1/jobs/{id}/cancel",
        response_model=JobStatusResponseModel,
        responses={401: {"model": ErrorResponseModel}, 404: {"model": ErrorResponseModel}},
        dependencies=[Depends(require_api_key)],
        summary="Cancel a queued or running job (idempotent)",
        tags=["v1"],
    )
    async def cancel_job(id: str) -> JobStatusResponseModel:
        """Idempotently cancel a job. Terminal jobs (`succeeded`, `failed`, `canceled`) remain unchanged."""
        job = task_store.request_cancel_job(id)
        return JobStatusResponseModel.model_validate(job.to_api_dict())

    @app.get(
        "/v1/jobs/{id}/result",
        responses={
            200: {
                "content": {
                    "audio/wav": {"schema": {"type": "string", "format": "binary"}},
                    "audio/mp4": {"schema": {"type": "string", "format": "binary"}},
                    "audio/mpeg": {"schema": {"type": "string", "format": "binary"}},
                    "audio/flac": {"schema": {"type": "string", "format": "binary"}},
                    "audio/aiff": {"schema": {"type": "string", "format": "binary"}},
                },
                "description": "Rendered binaural audio stream.",
            },
            401: {"model": ErrorResponseModel},
            404: {"model": ErrorResponseModel},
            409: {"model": ErrorResponseModel},
            410: {"model": ErrorResponseModel},
        },
        dependencies=[Depends(require_api_key)],
        summary="Download rendered output audio for a succeeded job",
        tags=["v1"],
    )
    async def download_job_result(id: str) -> StreamingResponse:
        """Stream-download the rendered audio file, returning HTTP 410 if the result has expired."""
        ctx = task_store.acquire_result_access(id)
        job = ctx.__enter__()
        try:
            if job.result_path is None or not job.result_path.exists():
                ctx.__exit__(None, None, None)
                raise ResourceExpiredError("result", id, job.expires_at)

            result_file = job.result_path
            file_size = result_file.stat().st_size
            fmt = job.params.output.format
            media_type = _media_type_for_format(fmt)
            filename = f"{job.job_id}.{job.params.output.extension}"

            def _file_iterator() -> Iterator[bytes]:
                try:
                    with result_file.open("rb") as f:
                        while chunk := f.read(65536):
                            yield chunk
                finally:
                    ctx.__exit__(None, None, None)

            headers = {
                "Content-Length": str(file_size),
                "Content-Disposition": f'attachment; filename="{filename}"',
            }
            return StreamingResponse(_file_iterator(), media_type=media_type, headers=headers)
        except Exception:
            ctx.__exit__(None, None, None)
            raise

    @app.get(
        "/v1/jobs/{id}/original", dependencies=[Depends(require_api_key)], tags=["v1"],
        responses={
            200: {"content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
                  "description": "BS.1770 loudness-matched original excerpt (float32 stereo WAV)."},
            401: {"model": ErrorResponseModel}, 404: {"model": ErrorResponseModel},
            409: {"model": ErrorResponseModel}, 410: {"model": ErrorResponseModel},
        },
        response_class=StreamingResponse,
        summary="Download the loudness-matched original side of a completed preview",
    )
    async def download_preview_original(id: str) -> StreamingResponse:
        ctx = task_store.acquire_result_access(id)
        job = ctx.__enter__()
        try:
            if job.params.preview is None:
                raise ApiServiceError("该任务不是试听任务。", http_status=409,
                                      error_code="NOT_A_PREVIEW", exit_code=ExitCode.PARAM_ERROR)
            original = job.result_path.with_suffix(".original.wav") if job.result_path else None
            if original is None or not original.is_file():
                raise ResourceExpiredError("result", id, job.expires_at)
            file_handle = original.open("rb")

            def stream() -> Iterator[bytes]:
                try:
                    with file_handle:
                        while chunk := file_handle.read(65536):
                            yield chunk
                finally:
                    ctx.__exit__(None, None, None)

            return StreamingResponse(stream(), media_type="audio/wav", headers={
                "Content-Length": str(original.stat().st_size),
                "Content-Disposition": f'attachment; filename="{id}.original.wav"',
            })
        except Exception:
            ctx.__exit__(None, None, None)
            raise

    @app.get(
        "/v1/jobs/{id}/report",
        response_model=ConversionReport,
        responses={
            401: {"model": ErrorResponseModel},
            404: {"model": ErrorResponseModel},
            409: {"model": ErrorResponseModel},
            410: {"model": ErrorResponseModel},
        },
        dependencies=[Depends(require_api_key)],
        summary="Download JSON conversion report for a succeeded job",
        tags=["v1"],
    )
    async def download_job_report(id: str) -> ConversionReport:
        """Return the structured JSON `ConversionReport`, or HTTP 410 if expired."""
        with task_store.acquire_result_access(id) as job:
            if job.report_path is None or not job.report_path.exists():
                raise ResourceExpiredError("result", id, job.expires_at)
            raw_json = job.report_path.read_text(encoding="utf-8")
            return ConversionReport.model_validate_json(raw_json)

    static_dir = web_dir or Path(os.environ.get(
        "AURORA_WEB_DIR", str(Path(__file__).resolve().parents[3] / "web" / "dist")
    ))
    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def export_openapi_schema(target_path: Path | None = None) -> dict[str, Any]:
    """Generate the OpenAPI 3.1 schema for the Aurora Binaural FastAPI service and optionally write it to disk."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="aurora_openapi_") as tmp_dir:
        cfg = ServiceConfig(
            api_key="openapi_schema_placeholder",
            data_dir=Path(tmp_dir),
            db_path=Path(tmp_dir) / "openapi.db",
        )
        app = create_app(cfg)
        schema = app.openapi()

    if target_path is not None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return schema


__all__ = [
    "API_VERSION",
    "AssetUploadResponseModel",
    "ErrorDetailModel",
    "ErrorResponseModel",
    "HealthResponseModel",
    "JobStatusResponseModel",
    "PresetsResponseModel",
    "ReadyResponseModel",
    "create_app",
    "export_openapi_schema",
]
