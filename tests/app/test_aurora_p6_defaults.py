"""P6 defaults must render through native V2 in both CLI and queued API jobs."""

import json
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aurora_binaural.api import create_app
from aurora_binaural.catalog import DEFAULT_PRESET_ID
from aurora_binaural.cli import run_cli
from aurora_binaural.config import ServiceConfig
from aurora_binaural.worker import AuroraWorker

pytest.importorskip("aurora_core", reason="Run bash scripts/build-core.sh for native P6 checks")


def source_audio(path: Path) -> None:
    t = np.arange(48000) / 48000
    stereo = np.column_stack((np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 660 * t)))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes((stereo * 4000).astype("<i2").tobytes())


def assert_native_default(report: dict[str, Any]) -> None:
    # Validate the public report, not a mock of constructor arguments.
    engine = report["engine"]
    params = report["params"]
    assert isinstance(engine, dict) and isinstance(params, dict)
    assert engine["name"] == "aurora_core" and engine["is_stub"] is False
    assert engine["route"] == "V"
    assert engine["hrtf"]["name"] == "SADIE II D1"
    assert params["preset"]["id"] == DEFAULT_PRESET_ID
    assert report["timeline"]["sample_diff"] == 0


def test_cli_omitted_engine_and_preset_render_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AURORA_ENGINE")
    source = tmp_path / "source.wav"
    source_audio(source)
    assert run_cli(["convert", str(source), "--output", str(tmp_path / "result.wav")]) == 0
    assert_native_default(json.loads(capsys.readouterr().out))


@pytest.mark.parametrize("preview", [False, True])
def test_default_api_and_worker_render_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preview: bool,
) -> None:
    monkeypatch.delenv("AURORA_ENGINE")
    config = ServiceConfig(api_key="p6-fixture", data_dir=tmp_path / "runtime", db_path=tmp_path / "db")
    assert config.engine == "rust"
    source = tmp_path / "source.wav"
    source_audio(source)
    app = create_app(config)
    headers = {"Authorization": "Bearer p6-fixture"}
    with TestClient(app) as client:
        status = client.get("/v1/presets", headers=headers).json()["engine"]
        assert status["is_stub"] is False and status["available"] is True
        assert status["version"] != "unknown"
        assert status["hrtf"]["name"] == "SADIE II D1"
        assert "V" in status["supported_routes"]
        uploaded = client.post("/v1/assets", headers=headers, files={"file": (source.name, source.read_bytes())})
        assert uploaded.status_code == 201
        asset_id = uploaded.json()["asset_id"]
        payload = {"asset_id": asset_id, "duration_seconds": 1} if preview else {"input": {"asset_id": asset_id}}
        created = client.post("/v1/previews" if preview else "/v1/jobs", headers=headers, json=payload)
        assert created.status_code == 202
        job = AuroraWorker(config, store=app.state.store).run_once()
        assert job is not None and job.status == "succeeded", job
        report = client.get(f"/v1/jobs/{job.job_id}/report", headers=headers).json()
        assert_native_default(report)
        assert client.get(f"/v1/jobs/{job.job_id}/result", headers=headers).status_code == 200
