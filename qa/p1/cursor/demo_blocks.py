#!/usr/bin/env python3
"""Call the route B core from Python, one block at a time.

Requires ``maturin develop`` from ``core/`` so ``import aurora_core`` works,
and the HRIR packs from ``core/tools/export_hrir_pack.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from aurora_core import Renderer  # noqa: E402


def main() -> None:
    pack = ROOT / "data/hrtf/ku100_frontal.ahrp"
    source = ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa"
    preset = ROOT / "presets/p0_draft.toml"
    renderer = Renderer(str(pack), str(source), str(preset), strength=0.4, seed=20261016, preset="room")

    class Config:
        input_channels = 2
        output_channels = 2

    block = 256
    renderer.prepare(48_000, block, Config())

    class Params:
        strength = 0.4
        preset = "room"
        seed = 20261016

        def effective_strength(self) -> float:
            return self.strength

    renderer.set_params(Params())
    sr = 48_000
    n = sr // 2
    t = np.arange(n, dtype=np.float32) / sr
    left = 0.2 * np.sin(2 * np.pi * 440 * t)
    right = 0.2 * np.sin(2 * np.pi * 440 * t + 0.4)
    stereo = np.stack([left, right], axis=1).astype(np.float32)
    pieces = []
    for start in range(0, n, block):
        pieces.append(renderer.process(stereo[start : start + block]))
    out = np.concatenate(pieces, axis=0)
    print(
        f"frames {out.shape[0]} channels {out.shape[1]} "
        f"latency {renderer.latency_samples()} tail {renderer.tail_samples()} "
        f"peak {float(np.max(np.abs(out))):.4f}"
    )


if __name__ == "__main__":
    main()
