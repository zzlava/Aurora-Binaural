"""Engine selection fails closed; explicit stub mode remains available."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from aurora_binaural.api import create_app
from aurora_binaural.catalog import CATALOG, DEFAULT_PRESET_ID
from aurora_binaural.config import ServiceConfig
from aurora_binaural.engine import (
    RenderConfig,
    RustRenderer,
    StubRenderer,
    core_version,
    create_renderer,
    selected_engine,
)
from aurora_binaural.exit_codes import ProcessingError
from aurora_binaural.params import AuroraParams


@pytest.mark.parametrize("distribution", ["aurora-py", "aurora_core", None])
def test_core_without_version_uses_native_wheel_metadata(distribution: str | None) -> None:
    def version(name: str) -> str:
        if name == distribution:
            return "0.9.7"
        raise PackageNotFoundError(name)

    with patch("aurora_binaural.engine.importlib.metadata.version", side_effect=version):
        assert core_version(SimpleNamespace()) == ("0.9.7" if distribution else "unknown")


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


def test_route_aware_binding_receives_toml_route_before_prepare() -> None:
    calls: dict[str, object] = {}

    class RouteAwareCore(StubRenderer):
        def __init__(
            self, pack: str, source: str, preset_path: str, *, route: str, strength: float, seed: int, preset: str,
        ) -> None:
            super().__init__()
            calls.update(route=route, preset_path=preset_path, strength=strength, seed=seed, preset=preset)

        def set_controls(self, controls: dict[str, float]) -> None:
            assert self._prepared
            calls["controls"] = controls

        def effective_params(self) -> dict[str, object]:
            return {"controls": calls["controls"]}

    with patch("aurora_binaural.engine.importlib.import_module", return_value=SimpleNamespace(Renderer=RouteAwareCore)):
        renderer = RustRenderer()
    renderer.prepare(48000, 256, RenderConfig())
    renderer.set_params(AuroraParams(strength=0.7, seed=123))
    assert calls == {"route": "V", "preset_path": str(CATALOG[DEFAULT_PRESET_ID].preset_path),
                     "strength": 0.7, "seed": 123, "preset": DEFAULT_PRESET_ID,
                     "controls": AuroraParams(strength=0.7).resolved_controls}
    assert renderer.latency_samples() == 0
