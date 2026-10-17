"""SQLite migration, named snapshots, transactional exchange and CLI selection."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aurora_binaural.api import create_app
from aurora_binaural.catalog import DEFAULT_PRESET_ID
from aurora_binaural.cli import run_cli
from aurora_binaural.config import ServiceConfig
from aurora_binaural.params import AuroraParams, InputSpec
from aurora_binaural.storage import ApiServiceError, TaskStore
from aurora_binaural.user_presets import UserPresetBundle, UserPresetName, UserPresetSpec, UserPresetStore


def spec(name: str = "我的空间") -> UserPresetSpec:
    return UserPresetSpec.model_validate({"name": name, "preset": {"id": DEFAULT_PRESET_ID},
                                          "controls": dict(strength=0.8, width=0.9, height=0.7, room=0.1, voice_distance=0.2)})


def test_migration_preserves_existing_assets_and_jobs(tmp_path: Path) -> None:
    from aurora_binaural.media import probe_audio
    from test_aurora_p6_defaults import source_audio

    source = tmp_path / "source.wav"
    source_audio(source)
    tasks = TaskStore(tmp_path / "db")
    tasks.create_asset(asset_id="asset_old", filename=source.name, file_path=source,
                       probe=probe_audio(source), retention_sec=3600)
    job, _ = tasks.create_or_get_job(AuroraParams(input=InputSpec(asset_id="asset_old")),
                                      idempotency_key="old-job", retention_sec=3600, max_retries=2)
    # Simulate the pre-ADR-0005 schema, preserving real queued records.
    with sqlite3.connect(tasks.db_path) as conn:
        conn.execute("DROP TABLE user_presets")
        conn.execute("PRAGMA user_version=0")
    migrated = TaskStore(tasks.db_path)
    with sqlite3.connect(tasks.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert migrated.get_asset("asset_old") is not None
    restored = migrated.get_job(job.job_id)
    assert restored is not None and restored.status == "queued"
    saved = UserPresetStore(migrated).create(spec())
    # Repeat startup/migration and prove persistence.
    assert UserPresetStore(TaskStore(tasks.db_path)).get(saved.user_preset_id).controls == saved.controls


def test_crud_and_atomic_json_exchange(tmp_path: Path) -> None:
    store = UserPresetStore(TaskStore(tmp_path / "db"))
    saved = store.create(spec("  新空间  "))
    assert saved.name == "新空间"
    assert saved.controls.model_dump(exclude_none=True) == spec().controls.model_dump(exclude_none=True)
    assert len(store.list_presets()) == 1
    renamed = store.rename(saved.user_preset_id, UserPresetName(name="新名字"))
    assert renamed.user_preset_id == saved.user_preset_id
    assert renamed.name == "新名字"
    exported = store.export_bundle().model_dump_json()
    with pytest.raises(ApiServiceError, match="名称已存在"):
        store.import_bundle(UserPresetBundle(presets=[spec("先插入"), spec("新名字")]))
    assert [row.name for row in store.list_presets()] == ["新名字"]
    store.delete(saved.user_preset_id)
    assert not store.list_presets()
    imported = store.import_bundle(UserPresetBundle.model_validate_json(exported))
    assert imported[0].name == "新名字" and imported[0].controls == saved.controls
    assert imported[0].user_preset_id != saved.user_preset_id
    with pytest.raises(ApiServiceError) as failure:
        store.get(saved.user_preset_id)
    assert failure.value.http_status == 404


@pytest.mark.parametrize("payload", [
    {"schema_version": 2, "presets": []},
    {"presets": [{"name": " ", "preset": {"id": "V3"}, "controls": {}}]},
    {"presets": [{"name": "bad", "preset": {"id": "V3"}, "controls": {"widht": 0.5}}]},
    {"presets": [{"name": "bad", "preset": {"id": "room"}, "controls": {}}]},
])
def test_invalid_imports_fail(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UserPresetBundle.model_validate(payload)


def test_api_presets_crud_and_restart(tmp_path: Path) -> None:
    config = ServiceConfig(api_key="preset-test", data_dir=tmp_path / "data", db_path=tmp_path / "db")
    headers = {"Authorization": "Bearer preset-test"}
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/user-presets").status_code == 401
        created = client.post("/v1/user-presets", headers=headers, json=spec().model_dump(mode="json"))
        assert created.status_code == 201, created.text
        identity = created.json()["user_preset_id"]
        assert client.post("/v1/user-presets", headers=headers, json=spec().model_dump(mode="json")).status_code == 409
        assert client.patch(f"/v1/user-presets/{identity}", headers=headers, json={"name": "改名后"}).status_code == 200
        bundle = client.get("/v1/user-presets/export", headers=headers).json()
        assert bundle["presets"][0]["name"] == "改名后"
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/user-presets", headers=headers).json()[0]["name"] == "改名后"
        assert client.delete(f"/v1/user-presets/{identity}", headers=headers).status_code == 200
        assert client.post("/v1/user-presets/import", headers=headers, json=bundle).status_code == 201
        assert len(client.get("/v1/user-presets", headers=headers).json()) == 1


@pytest.mark.parametrize("command", ["convert", "preview", "batch"])
def test_cli_selects_saved_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture[str], command: str) -> None:
    from test_aurora_p6_defaults import source_audio

    db = tmp_path / "db"
    saved = UserPresetStore(TaskStore(db)).create(spec())
    monkeypatch.setenv("AURORA_DB_PATH", str(db))
    source = tmp_path / "input" / "source.wav"
    source.parent.mkdir()
    source_audio(source)
    if command == "convert":
        args = [command, str(source), "--output", str(tmp_path / "out.wav")]
    else:
        args = [command, str(source if command == "preview" else source.parent), "--output-dir", str(tmp_path / "out")]
    assert run_cli([*args, "--user-preset", saved.name]) == 0
    result = json.loads(capsys.readouterr().out)
    if command == "batch":
        assert result["failed"] == 0
        assert result["results"][0]["report"]["params"]["controls"] == saved.controls.model_dump(exclude_none=True)
    else:
        assert result["params"]["controls"] == saved.controls.model_dump(exclude_none=True)
