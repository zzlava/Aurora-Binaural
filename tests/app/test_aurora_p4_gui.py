"""GUI bootstrap isolation, static serving, CLI lifecycle and explicit CORS."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aurora_binaural.api import create_app
from aurora_binaural.cli import run_cli
from test_aurora_p3 import _make_test_config


def test_static_gui_and_local_session_are_separate_from_public_api(tmp_path: Path) -> None:
    cfg = _make_test_config(tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<html lang="zh-CN">Aurora</html>')
    app = create_app(cfg, web_dir=web, local_gui=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50001)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/v1/presets").status_code == 401
        assert client.get("/gui/session").status_code == 401
        headers = {"X-Aurora-GUI": "1"}
        response = client.get("/gui/session", headers=headers)
        assert response.status_code == 200
        assert response.json()["api_key"] == cfg.api_key
        assert response.headers["cache-control"] == "no-store"
        for extra in (
            {"Origin": "https://attacker.example"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Host": "attacker.example"},
        ):
            assert client.get("/gui/session", headers=headers | extra).status_code in (400, 401)
    with TestClient(app, base_url="http://127.0.0.1", client=("198.51.100.9", 50001)) as external:
        assert external.get("/gui/session", headers={"X-Aurora-GUI": "1"}).status_code == 401
    public = TestClient(create_app(cfg, web_dir=web))
    assert public.get("/gui/session", headers={"X-Aurora-GUI": "1"}).status_code == 404
    assert public.get("/health").json()["status"] == "ok"


def test_remote_cors_is_explicit_and_still_requires_key(tmp_path: Path) -> None:
    cfg = replace(_make_test_config(tmp_path), cors_origins=("http://127.0.0.1:8765",))
    client = TestClient(create_app(cfg))
    headers = {
        "Origin": "http://127.0.0.1:8765",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key,x-asset-filename",
    }
    response = client.options("/v1/previews", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    response = client.get("/v1/presets", headers={"Origin": headers["Origin"]})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    assert client.options("/v1/previews", headers=headers | {"Origin": "https://attacker.example"}).status_code == 400


def test_gui_cli_delegates_loopback_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, bool]] = []

    def launch(*, port: int, open_browser: bool) -> int:
        seen.append((port, open_browser))
        return 0

    monkeypatch.setattr("aurora_binaural.gui.run_gui", launch)
    assert run_cli(["gui", "--port", "8765", "--no-browser"]) == 0
    assert seen == [(8765, False)]
    assert run_cli(["gui"]) == 0
    assert seen[-1] == (0, True)


def test_gui_real_lifecycle_and_ephemeral_bootstrap(tmp_path: Path) -> None:
    import json
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request

    from aurora_binaural.storage import TaskStore

    web = tmp_path / "static"
    web.mkdir()
    (web / "index.html").write_text("<html>Aurora GUI fixture</html>")
    runtime = tmp_path / "runtime"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ | {
        "AURORA_DATA_DIR": str(runtime),
        "AURORA_DB_PATH": str(runtime / "aurora.db"),
        "AURORA_WEB_DIR": str(web),
        "AURORA_HOST": "0.0.0.0",
        "AURORA_API_KEY": "unused-environment-fixture",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from aurora_binaural.cli import main; main()",
            "gui",
            "--no-browser",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    key = ""
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(base + "/health", timeout=1) as response:
                    assert response.status == 200
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.03)
        else:
            pytest.fail("GUI did not start")
        request = urllib.request.Request(base + "/gui/session", headers={"X-Aurora-GUI": "1"})
        with urllib.request.urlopen(request, timeout=2) as response:
            key = json.load(response)["api_key"]
        assert key and key != "unused-environment-fixture"
        request = urllib.request.Request(base + "/v1/presets", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(request, timeout=2) as response:
            assert len(json.load(response)["presets"]) == 4
        assert TaskStore(runtime / "aurora.db").has_active_worker()
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode in (0, 130)
        assert key.encode() not in stdout + stderr
        assert b"127.0.0.1" in stderr
        assert not TaskStore(runtime / "aurora.db").has_active_worker()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
