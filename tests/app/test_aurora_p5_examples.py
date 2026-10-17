"""Exercise the runnable client over real HTTP, including ambiguous POST retries."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

CLIENT = Path(__file__).resolve().parents[2] / "examples/python_client.py"


@pytest.fixture
def example_server() -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {"uploads": 0, "creates": [], "polls": 0, "corrupt": False,
                             "redirect": False, "report_calls": 0, "result": b"verified audio bytes"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            pass

        def send(self, status: int, body: bytes, **headers: str) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.headers.get("Authorization") != "Bearer test-example-key":
                self.send(401, b'{"error":{"code":"UNAUTHORIZED"}}')
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v1/assets":
                state["uploads"] += 1
                if state["uploads"] == 1:
                    self.send(503, b'{"error":{"retryable":true}}', **{"Retry-After": "0"})
                    return
                state["input_sha"] = hashlib.sha256(body).hexdigest()
                assert state["input_sha"] == self.headers["X-Asset-Sha256"]
                self.send(201, json.dumps({"asset_id": "asset_fixture", "sha256": state["input_sha"],
                                            "size_bytes": len(body)}).encode())
                return
            if self.path == "/v1/jobs":
                state["creates"].append((self.headers["Idempotency-Key"], json.loads(body)))
                if state["redirect"]:
                    self.send(302, b"", Location="/must-not-follow")
                    return
                if len(state["creates"]) == 1:
                    # The server accepted the task but its reply was lost. Same key/body must replay.
                    self.close_connection = True
                    return
                self.send(202, b'{"job_id":"job_fixture"}')
                return
            self.send(404, b"{}")

        def do_GET(self) -> None:
            result = state["result"]
            metadata = {"sha256": hashlib.sha256(result).hexdigest(), "size_bytes": len(result), "samples": 48}
            if self.path == "/v1/jobs/job_fixture":
                state["polls"] += 1
                self.send(200, json.dumps({"status": "queued" if state["polls"] == 1 else "succeeded",
                                           "result_metadata": metadata}).encode())
            elif self.path == "/v1/jobs/job_fixture/report":
                state["report_calls"] += 1
                self.send(200, json.dumps({"job_id": "job_fixture", "input": {"sha256": state["input_sha"]},
                                           "output": metadata, "engine": {"is_stub": False, "name": "aurora_core"},
                                           "timeline": {"sample_diff": 0}, "loudness_after": {}}).encode())
            elif self.path == "/v1/jobs/job_fixture/result":
                self.send(200, b"corrupt audio" if state["corrupt"] else result)
            else:
                self.send(500, b'{"error":{"code":"REDIRECT_FOLLOWED"}}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def invoke(tmp_path: Path, server: str, *extra: str) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "AI 配音.wav"
    if not source.exists():
        source.write_bytes(b"source audio fixture")
    return subprocess.run([sys.executable, str(CLIENT), str(source), "--server", server,
                           "--output", str(tmp_path / "result.wav"), "--poll-interval", "0.01",
                           "--timeout", "10", "--require-rust", *extra],
                          env={**os.environ, "AURORA_API_KEY": "test-example-key"},
                          capture_output=True, text=True, timeout=20, check=False)


def test_example_retries_and_resumes_same_job(tmp_path: Path, example_server: tuple[str, dict[str, Any]]) -> None:
    server, state = example_server
    first = invoke(tmp_path, server)
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert (tmp_path / "result.wav").read_bytes() == state["result"]
    assert result["sha256"] == hashlib.sha256(state["result"]).hexdigest()
    assert state["uploads"] == 2
    assert len(state["creates"]) == 2
    assert state["creates"][0] == state["creates"][1]
    assert "preset" not in state["creates"][0][1]  # Use the API/TOML default without copying it.
    saved = (tmp_path / "result.wav.state.json").read_text()
    assert "test-example-key" not in saved
    resumed = invoke(tmp_path, server, "--overwrite")
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["job_id"] == result["job_id"]
    assert len(state["creates"]) == 2 and state["uploads"] == 2
    incompatible = invoke(tmp_path, server, "--overwrite", "--strength", "0.5")
    assert incompatible.returncode == 1
    assert "different input/server/parameter" in incompatible.stderr


def test_example_hash_failure_preserves_destination(tmp_path: Path,
                                                    example_server: tuple[str, dict[str, Any]]) -> None:
    server, state = example_server
    output = tmp_path / "result.wav"
    output.write_bytes(b"keep existing file")
    state["corrupt"] = True
    result = invoke(tmp_path, server, "--overwrite")
    assert result.returncode == 1
    assert "SHA-256 or byte count mismatch" in result.stderr
    assert output.read_bytes() == b"keep existing file"
    assert not (tmp_path / "result.wav.report.json").exists()
    assert list(tmp_path.glob(".result.wav.*")) == []


def test_example_rejects_redirect(tmp_path: Path, example_server: tuple[str, dict[str, Any]]) -> None:
    server, state = example_server
    state["redirect"] = True
    result = invoke(tmp_path, server)
    assert result.returncode == 1
    assert "HTTP 302" in result.stderr
    assert state["polls"] == 0 and len(state["creates"]) == 1


@pytest.mark.parametrize("bad_args", [("--strength", "nan"), ("--timeout", "0"), ("--retries", "-1")])
def test_example_rejects_invalid_limits(tmp_path: Path, bad_args: tuple[str, str]) -> None:
    result = invoke(tmp_path, "http://127.0.0.1:1", *bad_args)
    assert result.returncode == 2
