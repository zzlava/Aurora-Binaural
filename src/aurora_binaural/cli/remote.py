"""HTTP API client for `aurora convert --server` and `aurora job` (docs/plan.md Sections 9 & 10).

Implements the client side of the Section 10 HTTP API contract:
- `POST /v1/assets`: upload audio asset, receive `asset_id`
- `POST /v1/jobs`: create rendering job with `Idempotency-Key`, receive `job_id` (HTTP 202)
- `GET /v1/jobs/{id}`: poll status (`queued`, `running`, `succeeded`, `failed`, `canceled`)
- `POST /v1/jobs/{id}/cancel`: cancel job on SIGINT / user cancellation
- `GET /v1/jobs/{id}/result`: download rendered audio to temporary file and atomically commit
- `GET /v1/jobs/{id}/report`: fetch structured JSON conversion report
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, NoReturn

from aurora_binaural.exit_codes import (
    InputAudioError,
    OperationCanceledError,
    ParameterValidationError,
    RemoteServiceError,
)
from aurora_binaural.media import atomic_write_bytes, compute_sha256, log_stderr
from aurora_binaural.params import AuroraParams, InputSpec


def validate_server_url(server_url: str) -> str:
    """Validate and normalize `--server` base URL."""
    clean = server_url.strip()
    if not clean.startswith(("http://", "https://")):
        raise ParameterValidationError(
            f"Invalid --server URL '{server_url}': must start with http:// or https://",
            error_code="INVALID_SERVER_URL",
        )
    return clean.rstrip("/")


class RemoteAuroraClient:
    """HTTP client implementing docs/plan.md Section 10 contract using stdlib `urllib.request`."""

    def __init__(
        self,
        server_url: str,
        *,
        api_key: str | None = None,
        timeout_sec: float = 30.0,
        poll_interval_sec: float = 0.2,
    ) -> None:
        self.base_url = validate_server_url(server_url)
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self.poll_interval_sec = poll_interval_sec

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        hdrs: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            hdrs["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            hdrs.update(extra)
        return hdrs

    def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        req = urllib.request.Request(url, data=body, headers=self._headers(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw:
                    return {"ok": True}
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                return {"ok": True, "data": parsed}
        except urllib.error.HTTPError as exc:
            self._raise_from_http_error(exc, method=method, endpoint=endpoint)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RemoteServiceError(
                f"Failed to communicate with remote Aurora server '{self.base_url}': {exc}",
                error_code="REMOTE_UNREACHABLE",
                retryable=True,
                details={"server": self.base_url, "endpoint": endpoint},
            ) from exc

    def _raise_from_http_error(
        self,
        exc: urllib.error.HTTPError,
        *,
        method: str,
        endpoint: str,
        default_prefix: str = "REMOTE_HTTP",
    ) -> NoReturn:
        err_body = ""
        with contextlib.suppress(Exception):
            err_body = exc.read().decode("utf-8", errors="replace")
        details: dict[str, object] = {
            "server": self.base_url,
            "endpoint": endpoint,
            "http_status": exc.code,
        }
        error_code = f"{default_prefix}_{exc.code}"
        retryable = exc.code >= 500 or exc.code == 429
        msg = f"Remote server returned HTTP {exc.code} for {method} {endpoint}"
        if err_body:
            try:
                parsed_err = json.loads(err_body)
                if isinstance(parsed_err, dict):
                    err_obj = parsed_err.get("error", parsed_err)
                    if isinstance(err_obj, dict):
                        if isinstance(err_obj.get("code"), str):
                            error_code = str(err_obj["code"])
                        if isinstance(err_obj.get("message"), str):
                            msg = str(err_obj["message"])
                        if isinstance(err_obj.get("retryable"), bool):
                            retryable = bool(err_obj["retryable"])
                        details["remote_error"] = err_obj
            except json.JSONDecodeError:
                details["response_body"] = err_body[:500]
        raise RemoteServiceError(
            msg,
            error_code=error_code,
            retryable=retryable,
            details=details,
        ) from exc

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Query `GET /v1/jobs/{id}` (Section 10)."""
        safe_id = urllib.parse.quote(job_id, safe="")
        return self._request_json("GET", f"/v1/jobs/{safe_id}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Request `POST /v1/jobs/{id}/cancel` (Section 10)."""
        safe_id = urllib.parse.quote(job_id, safe="")
        return self._request_json("POST", f"/v1/jobs/{safe_id}/cancel", body=b"{}", headers={"Content-Type": "application/json"})

    def upload_asset(self, input_path: Path) -> str:
        """Stream-upload `input_path` to `POST /v1/assets` and return `asset_id`."""
        file_size = input_path.stat().st_size
        file_sha256 = compute_sha256(input_path)
        headers = self._headers(
            {
                "Content-Type": "application/octet-stream",
                "Content-Length": str(file_size),
                "X-Asset-Filename": urllib.parse.quote(input_path.name),
                "X-Asset-Sha256": file_sha256,
            }
        )
        url = f"{self.base_url}/v1/assets"
        log_stderr(f"Uploading asset '{input_path.name}' ({file_size} bytes) to {url}")
        try:
            with input_path.open("rb") as f:
                req = urllib.request.Request(url, data=f, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    payload = json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            self._raise_from_http_error(
                exc,
                method="POST",
                endpoint="/v1/assets",
                default_prefix="REMOTE_UPLOAD_HTTP",
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RemoteServiceError(
                f"Failed to upload asset to '{self.base_url}': {exc}",
                error_code="REMOTE_UPLOAD_FAILED",
                details={"server": self.base_url, "path": str(input_path)},
            ) from exc

        asset_id = payload.get("asset_id") or payload.get("id")
        if not isinstance(asset_id, str) or not asset_id:
            raise RemoteServiceError(
                "Remote server did not return a valid 'asset_id' from POST /v1/assets.",
                error_code="INVALID_REMOTE_RESPONSE",
                details={"response": payload},
            )
        return asset_id

    def create_job(self, asset_id: str, params: AuroraParams, *, idempotency_key: str | None = None) -> str:
        """Create an asynchronous job via `POST /v1/jobs` (Section 10)."""
        job_params = params.model_copy(deep=True)
        job_params.input = InputSpec(asset_id=asset_id)
        payload_dict = job_params.model_dump(mode="json", exclude_none=True)
        # Keep Section 10 canonical payload format
        body_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

        idem_key = idempotency_key or str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": idem_key,
        }
        log_stderr(f"Creating remote job on {self.base_url}/v1/jobs (asset_id={asset_id})")
        resp = self._request_json("POST", "/v1/jobs", body=body_bytes, headers=headers)
        job_id = resp.get("job_id") or resp.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise RemoteServiceError(
                "Remote server did not return a valid 'job_id' from POST /v1/jobs.",
                error_code="INVALID_REMOTE_RESPONSE",
                details={"response": resp},
            )
        return job_id

    def download_result(self, job_id: str, output_path: Path, *, overwrite: bool = False) -> None:
        """Stream-download `GET /v1/jobs/{id}/result` to a temp file and atomically replace `output_path`."""
        if output_path.exists() and not overwrite:
            raise InputAudioError(
                f"Output file already exists (use --overwrite to replace): {output_path}",
                error_code="OUTPUT_ALREADY_EXISTS",
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.parent / f".aurora_tmp_{uuid.uuid4().hex}_{output_path.name}"
        safe_id = urllib.parse.quote(job_id, safe="")
        endpoint = f"/v1/jobs/{safe_id}/result"
        url = f"{self.base_url}{endpoint}"
        req = urllib.request.Request(url, headers=self._headers({"Accept": "*/*"}), method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp, tmp_path.open("wb") as out_f:
                while chunk := resp.read(65536):
                    out_f.write(chunk)
                out_f.flush()
                os.fsync(out_f.fileno())

            if output_path.exists() and not overwrite:
                raise InputAudioError(
                    f"Output file already exists (use --overwrite to replace): {output_path}",
                    error_code="OUTPUT_ALREADY_EXISTS",
                )
            os.replace(tmp_path, output_path)
        except urllib.error.HTTPError as exc:
            self._raise_from_http_error(
                exc,
                method="GET",
                endpoint=endpoint,
                default_prefix="REMOTE_DOWNLOAD_HTTP",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RemoteServiceError(
                f"Failed to download job result for '{job_id}': {exc}",
                error_code="REMOTE_DOWNLOAD_FAILED",
                details={"job_id": job_id, "server": self.base_url},
            ) from exc
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def get_job_report(self, job_id: str) -> dict[str, Any]:
        """Fetch `GET /v1/jobs/{id}/report` (Section 10)."""
        safe_id = urllib.parse.quote(job_id, safe="")
        return self._request_json("GET", f"/v1/jobs/{safe_id}/report")

    def run_convert_workflow(
        self,
        input_path: Path,
        output_path: Path,
        params: AuroraParams,
        *,
        overwrite: bool = False,
        report_path: Path | None = None,
    ) -> dict[str, Any]:
        """Execute full remote conversion workflow: upload -> create job -> poll -> download result & report."""
        if not input_path.exists():
            raise InputAudioError(f"Input file does not exist: {input_path}", error_code="INPUT_NOT_FOUND")
        if not input_path.is_file():
            raise InputAudioError(f"Input path is not a file: {input_path}", error_code="INPUT_NOT_A_FILE")
        if output_path.exists() and not overwrite:
            raise InputAudioError(
                f"Output file already exists (use --overwrite to replace): {output_path}",
                error_code="OUTPUT_ALREADY_EXISTS",
            )
        if report_path is not None and report_path.exists() and not overwrite:
            raise InputAudioError(
                f"Report file already exists (use --overwrite to replace): {report_path}",
                error_code="REPORT_ALREADY_EXISTS",
            )

        asset_id = self.upload_asset(input_path)
        job_id = self.create_job(asset_id, params)
        log_stderr(f"Remote job '{job_id}' submitted; polling status...")

        try:
            while True:
                status_payload = self.get_job_status(job_id)
                status = str(status_payload.get("status", "")).lower()
                progress = status_payload.get("progress")
                if progress is not None:
                    log_stderr(f"Remote job '{job_id}' status={status} progress={progress}")
                else:
                    log_stderr(f"Remote job '{job_id}' status={status}")

                if status == "succeeded":
                    break
                if status == "failed":
                    err_info = status_payload.get("error")
                    err_msg = f"Remote job '{job_id}' failed."
                    err_code = "REMOTE_JOB_FAILED"
                    retryable = False
                    if isinstance(err_info, dict):
                        if isinstance(err_info.get("message"), str):
                            err_msg = str(err_info["message"])
                        if isinstance(err_info.get("code"), str):
                            err_code = str(err_info["code"])
                        if isinstance(err_info.get("retryable"), bool):
                            retryable = bool(err_info["retryable"])
                    raise RemoteServiceError(
                        err_msg,
                        error_code=err_code,
                        retryable=retryable,
                        details={"job_id": job_id, "job_status": status_payload},
                    )
                if status == "canceled":
                    raise OperationCanceledError(
                        f"Remote job '{job_id}' was canceled.",
                        details={"job_id": job_id},
                    )
                if status not in ("queued", "running"):
                    raise RemoteServiceError(
                        f"Unexpected remote job status '{status}' for job '{job_id}'.",
                        error_code="INVALID_REMOTE_JOB_STATUS",
                        details={"job_id": job_id, "status_payload": status_payload},
                    )

                time.sleep(self.poll_interval_sec)

            log_stderr(f"Downloading rendered result for job '{job_id}' -> {output_path}")
            self.download_result(job_id, output_path, overwrite=overwrite)

            log_stderr(f"Fetching conversion report for job '{job_id}'")
            report_data = self.get_job_report(job_id)
            if report_path is not None:
                report_bytes = (json.dumps(report_data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                atomic_write_bytes(report_path, report_bytes, overwrite=overwrite)
            return report_data
        except KeyboardInterrupt:
            log_stderr(f"Interrupted! Requesting cancellation of remote job '{job_id}'...")
            with contextlib.suppress(Exception):
                self.cancel_job(job_id)
            raise
