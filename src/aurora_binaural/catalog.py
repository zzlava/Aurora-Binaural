"""Read route and preset metadata from the immutable presets/*.toml directory.

The current V TOML schema has shared [layout] and named preset tables; the
legacy B schema has named rows containing width endpoints. Neither contains
explicit route/default metadata yet, so schema detection and the product's
default V2 policy are centralized here. No duplicate preset list is maintained.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from aurora_binaural.resources import asset_directory

DEFAULT_PRESET_ID = "V2"


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    route: str
    version: str
    default_strength: float
    description: str
    preset_path: Path
    sha256: str
    public: bool


def load_catalog(directory: Path | None = None) -> dict[str, CatalogEntry]:
    directory = directory or asset_directory("presets")
    entries: dict[str, CatalogEntry] = {}
    for path in sorted(directory.glob("*.toml")):
        content = path.read_bytes()
        document = tomllib.loads(content.decode("utf-8"))
        route = str(document.get("route", "V" if "layout" in document else "B"))
        for key, row in document.items():
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                continue
            if key in entries:
                raise ValueError(f"Duplicate preset {key!r} in {directory}")
            public = route == "V" or (route == "B" and key == "room")
            name = f"经典 · {row['name']}" if route == "B" and key == "room" else row["name"]
            entries[key] = CatalogEntry(
                id=key, name=name, route=route, version=str(document.get("version", 1)),
                default_strength=float(row.get("default_strength", 0.5)),
                description=str(row.get("description", f"路线 {route} · {name}")),
                preset_path=path.resolve(), sha256=hashlib.sha256(content).hexdigest(), public=public,
            )
    if not entries:
        raise ValueError(f"No presets found in {directory}; set AURORA_PRESETS_DIR to presets/*.toml.")
    return entries


CATALOG = load_catalog()
