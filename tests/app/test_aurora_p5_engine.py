"""Engine selection fails closed; explicit stub mode remains available."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from aurora_binaural.api import create_app
from aurora_binaural.config import ServiceConfig
from aurora_binaural.engine import StubRenderer, create_renderer, selected_engine
from aurora_binaural.exit_codes import ProcessingError


def test_default_engine_missing_core_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AURORA_ENGINE")
    assert selected_engine() == "rust"
    with patch("aurora_binaural.engine.importlib.import_module", side_effect=ImportError("missing")):
        with pytest.raises(ProcessingError, match="build-core.sh") as error:
            create_renderer()
        assert error.value.error_code == "CORE_NOT_INSTALLED"


def test_stub_is_explicit_and_invalid_selection_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(create_renderer(), StubRenderer)
    monkeypatch.setenv("AURORA_ENGINE", "typo")
    with pytest.raises(ProcessingError, match="rust or stub"):
        create_renderer()


def test_rust_cannot_simulate_delay() -> None:
    with pytest.raises(ProcessingError, match="AURORA_ENGINE=stub"):
        create_renderer(engine="rust", latency_samples=10)


def test_public_engine_status_is_explicit(tmp_path: Path) -> None:
    config = ServiceConfig(api_key="fixture", data_dir=tmp_path, db_path=tmp_path / "db", engine="rust")
    with (
        patch("aurora_binaural.engine.importlib.import_module", side_effect=ImportError("missing")),
        TestClient(create_app(config)) as client,
    ):
        response = client.get("/v1/presets", headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 200
    assert response.json()["engine"]["is_stub"] is False
    assert response.json()["engine"]["available"] is False

