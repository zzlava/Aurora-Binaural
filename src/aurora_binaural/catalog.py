"""Read route and preset metadata from the immutable presets/*.toml directory.

The route V root ``default_preset`` field selects the product default.
Older catalogs fall back to their first named V preset with an explicit warning.
"""

import hashlib
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path

from aurora_binaural.resources import asset_directory


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


def load_default_preset(directory: Path | None = None) -> str:
    """Read Cursor's root default_preset field; never hard-code a product preset."""
    directory = directory or asset_directory("presets")
    document = tomllib.loads((directory / "route_v.toml").read_text(encoding="utf-8"))
    preset_ids = [key for key, row in document.items() if isinstance(row, dict) and isinstance(row.get("name"), str)]
    if not preset_ids:
        raise ValueError(f"No route V presets found in {directory / 'route_v.toml'}")
    selected = document.get("default_preset")
    if selected is None:
        warnings.warn(
            f"route_v.toml has no default_preset; falling back to its first preset {preset_ids[0]!r}.",
            RuntimeWarning, stacklevel=2,
        )
        return preset_ids[0]
    if selected not in preset_ids:
        raise ValueError(f"route_v.toml default_preset {selected!r} does not name a preset table.")
    return str(selected)


CATALOG = load_catalog()
DEFAULT_PRESET_ID = load_default_preset()
