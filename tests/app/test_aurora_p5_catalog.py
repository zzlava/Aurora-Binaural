"""The shared catalog follows TOML rows and names without UI/CLI preset copies."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aurora_binaural.api import create_app
from aurora_binaural.catalog import CATALOG, DEFAULT_PRESET_ID, load_catalog
from aurora_binaural.cli import run_cli
from aurora_binaural.config import ServiceConfig
from aurora_binaural.params import PRESET_CATALOG, AuroraParams, PresetId, get_params_json_schema


def test_api_cli_models_share_toml_catalog(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert DEFAULT_PRESET_ID in CATALOG
    assert AuroraParams().preset.id == DEFAULT_PRESET_ID
    assert set(PRESET_CATALOG) == {"V1", "V2", "V3", "room"}
    assert PRESET_CATALOG[PresetId("room")].name.startswith("经典")
    assert run_cli(["presets"]) == 0
    cli = json.loads(capsys.readouterr().out)
    config = ServiceConfig(api_key="fixture", data_dir=tmp_path, db_path=tmp_path / "db")
    with TestClient(create_app(config)) as client:
        api = client.get("/v1/presets", headers={"Authorization": "Bearer fixture"}).json()
    assert api["presets"] == cli["presets"]
    assert api["default_preset"] == cli["default_preset"] == DEFAULT_PRESET_ID
    for row in api["presets"]:
        assert row["route"] == CATALOG[row["id"]].route
        assert row["version"] == CATALOG[row["id"]].version
    assert set(get_params_json_schema()["$defs"]["PresetId"]["enum"]) == set(CATALOG)


def test_custom_toml_rows_and_names_are_discovered(tmp_path: Path) -> None:
    (tmp_path / "future.toml").write_text(
        'version = 7\n[layout]\nfront_az = 45.0\n[V7]\nname = "未来的中文预设"\ndefault_strength = 0.7\n',
        encoding="utf-8",
    )
    catalog = load_catalog(tmp_path)
    assert list(catalog) == ["V7"]
    assert catalog["V7"].route == "V"
    assert catalog["V7"].name == "未来的中文预设"
    assert catalog["V7"].version == "7"
    assert catalog["V7"].default_strength == 0.7
    assert catalog["V7"].public


def test_empty_and_duplicate_catalogs_fail_explicitly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No presets"):
        load_catalog(tmp_path)
    for name in ("a", "b"):
        (tmp_path / f"{name}.toml").write_text('[room]\nname="重复"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate preset"):
        load_catalog(tmp_path)


def test_default_uses_root_field_and_first_table_fallback(tmp_path: Path) -> None:
    from aurora_binaural.catalog import load_default_preset

    path = tmp_path / "route_v.toml"
    path.write_text('default_preset = "V3"\n[V2]\nname="two"\n[V3]\nname="three"\n')
    assert load_default_preset(tmp_path) == "V3"
    path.write_text('[V7]\nname="first"\n[V3]\nname="second"\n')
    with pytest.warns(RuntimeWarning, match="first preset 'V7'"):
        assert load_default_preset(tmp_path) == "V7"
    path.write_text('default_preset = "missing"\n[V7]\nname="first"\n')
    with pytest.raises(ValueError, match="does not name a preset"):
        load_default_preset(tmp_path)
