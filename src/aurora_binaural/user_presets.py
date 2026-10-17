"""Named route V macro snapshots, versioned JSON exchange and SQLite migration."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aurora_binaural.catalog import CATALOG
from aurora_binaural.params import AuroraParams, PresetSpec, SpatialControls

if TYPE_CHECKING:
    from aurora_binaural.storage import TaskStore


def migrate_user_presets(conn: sqlite3.Connection) -> None:
    """Migration 1 is atomic with TaskStore startup; existing jobs stay untouched."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version < 1:
        conn.execute("""CREATE TABLE IF NOT EXISTS user_presets (
            user_preset_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            preset_json TEXT NOT NULL,
            controls_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )""")
        conn.execute("PRAGMA user_version=1")


class UserPresetName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class UserPresetSpec(UserPresetName):
    preset: PresetSpec
    controls: SpatialControls

    @model_validator(mode="after")
    def resolve_snapshot(self) -> UserPresetSpec:
        entry = CATALOG[self.preset.id]
        if entry.route != "V":
            raise ValueError("我的预设需要支持全部五个空间控件的路线 V 基础预设。")
        if self.preset.version != entry.version:
            raise ValueError("Unsupported base preset version.")
        params = AuroraParams(preset=self.preset, controls=self.controls)
        self.controls = params.controls
        return self


class UserPreset(UserPresetSpec):
    user_preset_id: str
    created_at: float
    updated_at: float


class UserPresetBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    presets: list[UserPresetSpec] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_names(self) -> UserPresetBundle:
        names = [preset.name.casefold() for preset in self.presets]
        if len(names) != len(set(names)):
            raise ValueError("导入文件含重复预设名称，请先重命名。")
        return self


class UserPresetStore:
    def __init__(self, tasks: TaskStore) -> None:
        self.tasks = tasks

    @staticmethod
    def _row(row: sqlite3.Row) -> UserPreset:
        return UserPreset.model_validate({
            "user_preset_id": row["user_preset_id"], "name": row["name"],
            "preset": json.loads(row["preset_json"]), "controls": json.loads(row["controls_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })

    def list_presets(self) -> list[UserPreset]:
        with self.tasks._immediate_tx() as conn:
            rows = conn.execute("SELECT * FROM user_presets ORDER BY created_at, user_preset_id").fetchall()
        return [self._row(row) for row in rows]

    def get(self, identity: str) -> UserPreset:
        from aurora_binaural.storage import ApiServiceError

        with self.tasks._immediate_tx() as conn:
            row = conn.execute("SELECT * FROM user_presets WHERE user_preset_id=? OR name=?", (identity, identity)).fetchone()
        if row is None:
            raise ApiServiceError("我的预设不存在，请刷新列表。", http_status=404, error_code="USER_PRESET_NOT_FOUND")
        return self._row(row)

    def create(self, spec: UserPresetSpec) -> UserPreset:
        return self.import_bundle(UserPresetBundle(presets=[spec]))[0]

    def import_bundle(self, bundle: UserPresetBundle) -> list[UserPreset]:
        from aurora_binaural.storage import ApiServiceError

        rows: list[UserPreset] = []
        try:
            with self.tasks._immediate_tx() as conn:
                for spec in bundle.presets:
                    now = time.time()
                    row = UserPreset(**spec.model_dump(), user_preset_id=f"userpreset_{uuid.uuid4().hex[:16]}",
                                     created_at=now, updated_at=now)
                    conn.execute("INSERT INTO user_presets VALUES (?, ?, ?, ?, ?, ?)", (
                        row.user_preset_id, row.name, row.preset.model_dump_json(),
                        row.controls.model_dump_json(exclude_none=True), now, now,
                    ))
                    rows.append(row)
        except sqlite3.IntegrityError as exc:
            raise ApiServiceError("预设名称已存在，请重命名后重试。", http_status=409, error_code="USER_PRESET_NAME_CONFLICT") from exc
        return rows

    def rename(self, identity: str, name: UserPresetName) -> UserPreset:
        from aurora_binaural.storage import ApiServiceError

        row = self.get(identity)
        try:
            with self.tasks._immediate_tx() as conn:
                result = conn.execute("UPDATE user_presets SET name=?, updated_at=? WHERE user_preset_id=?",
                                      (name.name, time.time(), row.user_preset_id))
                if not result.rowcount:
                    raise ApiServiceError("我的预设不存在。", http_status=404, error_code="USER_PRESET_NOT_FOUND")
        except sqlite3.IntegrityError as exc:
            raise ApiServiceError("预设名称已存在。", http_status=409, error_code="USER_PRESET_NAME_CONFLICT") from exc
        return self.get(row.user_preset_id)

    def delete(self, identity: str) -> None:
        row = self.get(identity)
        with self.tasks._immediate_tx() as conn:
            conn.execute("DELETE FROM user_presets WHERE user_preset_id=?", (row.user_preset_id,))

    def export_bundle(self) -> UserPresetBundle:
        return UserPresetBundle(presets=[UserPresetSpec(name=row.name, preset=row.preset, controls=row.controls)
                                        for row in self.list_presets()])
