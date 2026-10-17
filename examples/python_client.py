#!/usr/bin/env python3
"""Dependency-free Aurora API client. See docs/user/api.md for the protocol.

Key comes only from AURORA_API_KEY. A state file preserves the upload ID,
request and idempotency key across interruptions; it never contains the key.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


class ClientError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward an Authorization header to a redirect target."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Client:
    def __init__(self, server: str, key: str, *, retries: int, request_timeout: float, deadline: float):
        parsed = urllib.parse.urlsplit(server)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            raise ClientError("--server must be an HTTP(S) URL without credentials, query or fragment")
        self.server = server.rstrip("/")
        self.key = key
        self.retries = retries
        self.request_timeout = request_timeout
        self.deadline = deadline
        self.opener = urllib.request.build_opener(NoRedirect())

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("workflow timed out; rerun with the same --state to resume (job is not canceled)")
        return remaining

    def pause(self, seconds: float) -> None:
        time.sleep(min(seconds, self.remaining()))

    def request(self, method: str, path: str, *, body: dict[str, Any] | None = None,
                upload: Path | None = None, upload_sha: str | None = None,
                idem_key: str | None = None, destination: Path | None = None,
                expected_sha: str | None = None, expected_size: int | None = None) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            timeout = min(self.request_timeout, self.remaining())
            headers = {"Authorization": f"Bearer {self.key}"}
            if idem_key:
                headers["Idempotency-Key"] = idem_key
            data: Any = None
            handle = None
            temporary: Path | None = None
            retry_delay = min(0.5 * 2 ** attempt, 8.0)
            try:
                if upload:
                    # Reopen on every attempt: retries must send the full body.
                    handle = upload.open("rb")
                    data = handle
                    headers.update({"Content-Type": "application/octet-stream",
                                    "Content-Length": str(upload.stat().st_size),
                                    # ASCII transport name; Unicode local names remain supported.
                                    "X-Asset-Filename": "source" + upload.suffix.lower(),
                                    "X-Asset-Sha256": upload_sha or sha256_file(upload)})
                elif body is not None:
                    data = json.dumps(body, sort_keys=True, allow_nan=False).encode("utf-8")
                    headers["Content-Type"] = "application/json"
                request = urllib.request.Request(self.server + path, data=data, headers=headers, method=method)
                with self.opener.open(request, timeout=timeout) as response:
                    if destination is None:
                        payload = json.load(response)
                        if not isinstance(payload, dict):
                            raise ClientError("server returned a non-object JSON response")
                        return payload
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
                    temporary = Path(name)
                    digest, size = hashlib.sha256(), 0
                    with os.fdopen(fd, "wb") as target:
                        while chunk := response.read(65536):
                            self.remaining()
                            target.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                    if digest.hexdigest() != expected_sha or size != expected_size:
                        raise ClientError("download SHA-256 or byte count mismatch; destination was not replaced")
                    os.replace(temporary, destination)
                    return {"sha256": digest.hexdigest(), "size_bytes": size}
            except urllib.error.HTTPError as exc:
                raw = exc.read(65536)
                exc.close()
                try:
                    payload = json.loads(raw)
                    detail = payload.get("error", {}) if isinstance(payload, dict) else {}
                except ValueError:
                    detail = {}
                if not isinstance(detail, dict):
                    detail = {}
                retryable = exc.code in (429, 500, 502, 503, 504) and detail.get("retryable", True)
                if not retryable or attempt == self.retries:
                    raise ClientError(f"HTTP {exc.code}: {detail.get('code', 'HTTP_ERROR')} "
                                      f"{detail.get('message', exc.reason)}") from exc
                with contextlib.suppress(ValueError):
                    retry_delay = min(max(float(exc.headers.get("Retry-After", retry_delay)), 0), 10)
            except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                if attempt == self.retries:
                    raise ClientError(f"network or file operation failed after {attempt + 1} attempts: {exc}") from exc
            finally:
                if handle:
                    handle.close()
                if temporary:
                    temporary.unlink(missing_ok=True)
            print(f"retry {attempt + 1}/{self.retries}: {method} {path}", file=sys.stderr)
            self.pause(retry_delay)
        raise AssertionError("unreachable")


def run(args: argparse.Namespace) -> dict[str, Any]:
    key = os.environ.get("AURORA_API_KEY", "").strip()
    if not key:
        raise ClientError("set AURORA_API_KEY in the environment")
    source, output = args.input.resolve(), args.output.resolve()
    report_path = (args.report or Path(str(output) + ".report.json")).resolve()
    state_path = (args.state or Path(str(output) + ".state.json")).resolve()
    if len({source, output, report_path, state_path}) != 4:
        raise ClientError("input, output, report and state paths must be distinct")
    if not source.is_file():
        raise ClientError(f"input file does not exist: {source}")
    if output.suffix.lower() != ".wav":
        raise ClientError("this integration example produces WAV; --output must end in .wav")
    if not args.overwrite and (output.exists() or report_path.exists()):
        raise ClientError("output/report exists; use a new path or --overwrite")
    client = Client(args.server, key, retries=args.retries, request_timeout=args.request_timeout,
                    deadline=time.monotonic() + args.timeout)
    source_sha = sha256_file(source)
    render = {"api_version": "v1", "mode": "enhance",
              "preset": {"id": args.preset, "version": "1"}, "strength": args.strength, "seed": args.seed,
              "output": {"format": "wav", "sample_rate": 48000, "bit_depth": 24},
              "timing": {"mode": "preserve", "tail": "trim"}}
    identity = {"server": client.server, "input_sha256": source_sha, "render": render}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ClientError("state must be a JSON object")
        if state.get("identity") != identity:
            raise ClientError("state belongs to a different input/server/parameter set; choose a new --state")
        if args.idempotency_key and args.idempotency_key != state["idempotency_key"]:
            raise ClientError("--idempotency-key differs from the saved state")
    else:
        state = {"identity": identity, "idempotency_key": args.idempotency_key or str(uuid.uuid4())}
        save_json(state_path, state)
    saved_key = state.get("idempotency_key")
    if not isinstance(saved_key, str) or not saved_key.strip() or any(c in saved_key for c in "\r\n"):
        raise ClientError("state contains an invalid idempotency key")
    if "asset_id" not in state:
        uploaded = client.request("POST", "/v1/assets", upload=source, upload_sha=source_sha)
        if uploaded["sha256"] != source_sha or uploaded["size_bytes"] != source.stat().st_size:
            raise ClientError("upload checksum or byte count mismatch")
        state["asset_id"] = uploaded["asset_id"]
        state["request"] = {**render, "input": {"asset_id": uploaded["asset_id"]}}
        save_json(state_path, state)
    if "job_id" not in state:
        job = client.request("POST", "/v1/jobs", body=state["request"], idem_key=state["idempotency_key"])
        state["job_id"] = job["job_id"]
        save_json(state_path, state)
    job_id = state["job_id"]
    if not isinstance(job_id, str) or not job_id.startswith("job_") or not job_id.replace("_", "").isalnum():
        raise ClientError("invalid job_id in saved state")
    job_path = "/v1/jobs/" + job_id
    previous = None
    while True:
        job = client.request("GET", job_path)
        if job["status"] != previous:
            print(f"{job_id}: {job['status']}", file=sys.stderr)
            previous = job["status"]
        if job["status"] == "succeeded":
            break
        if job["status"] in ("failed", "canceled"):
            raise ClientError(f"{job_id}: {job['status']}: {json.dumps(job.get('error'), ensure_ascii=False)}")
        if job["status"] not in ("queued", "running"):
            raise ClientError(f"unknown job status: {job['status']}")
        client.pause(args.poll_interval)
    report = client.request("GET", job_path + "/report")
    if report["job_id"] != job_id or report["input"]["sha256"] != source_sha:
        raise ClientError("report identity mismatch")
    if args.require_rust and (report["engine"]["is_stub"] or report["engine"]["name"] != "aurora_core"):
        raise ClientError("--require-rust rejected a non-Rust report")
    metadata = job["result_metadata"]
    if any(metadata[field] != report["output"][field] for field in ("sha256", "size_bytes", "samples")):
        raise ClientError("report and job output metadata disagree")
    downloaded = client.request("GET", job_path + "/result", destination=output,
                                expected_sha=metadata["sha256"], expected_size=metadata["size_bytes"])
    save_json(report_path, report)
    return {"ok": True, "job_id": job_id, "asset_id": state["asset_id"], "output": str(output),
            "report": str(report_path), "state": str(state_path), **downloaded,
            "samples": metadata["samples"], "engine": report["engine"],
            "timeline": report["timeline"], "loudness_after": report["loudness_after"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--server", default=os.environ.get("AURORA_SERVER", "http://127.0.0.1:8000"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--state", type=Path, help="persistent resume state; default OUTPUT.state.json")
    parser.add_argument("--idempotency-key", help="optional logical operation key, persisted before upload")
    parser.add_argument("--preset", default="V2")
    parser.add_argument("--strength", type=float, help="0–1; omitted uses the preset default")
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--timeout", type=float, default=300, help="workflow deadline in seconds")
    parser.add_argument("--request-timeout", type=float, default=30)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--retries", type=int, default=3, help="extra attempts per request")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--require-rust", action="store_true")
    args = parser.parse_args()
    if args.idempotency_key is not None and (not args.idempotency_key.strip()
                                             or any(c in args.idempotency_key for c in "\r\n")):
        parser.error("--idempotency-key must be nonempty and contain no line breaks")
    if ((args.strength is not None and (not math.isfinite(args.strength) or not 0 <= args.strength <= 1))
            or not 0 <= args.seed <= 4294967295 or not 0 <= args.retries <= 10
            or any(not math.isfinite(v) or v <= 0 for v in
                   (args.timeout, args.request_timeout, args.poll_interval))):
        parser.error("strength/seed/retries out of range, or timeout/interval not finite and positive")
    try:
        print(json.dumps(run(args), ensure_ascii=False, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print("interrupted; remote job is retained; resume with the same --state", file=sys.stderr)
        return 130
    except (ClientError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
