"""Locate read-only preset/HRTF assets in deployments, wheels and source checkouts."""

import os
from pathlib import Path


def asset_directory(name: str) -> Path:
    override = os.environ.get(f"AURORA_{name.upper()}_DIR")
    if override:
        return Path(override).resolve()
    packaged = Path(__file__).parent / "_data" / name
    if packaged.is_dir():
        return packaged
    root = Path(__file__).resolve().parents[2]
    return root / ("data/hrtf" if name == "hrtf" else name)
