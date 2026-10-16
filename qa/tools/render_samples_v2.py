#!/usr/bin/env python3
"""Listening samples for P0-cursor-04.

The engine output is level-matched with one broadband gain (and a second
scalar only if the true peak would pass -1 dBTP). No timbre guard.

    prototypes/.venv/bin/python qa/tools/render_samples_v2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from prototypes.aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from prototypes.aurora_p0.loudness import broadband_loudness_match  # noqa: E402
from prototypes.aurora_p0.strength import clear_preset_cache  # noqa: E402

SR = 48_000
TARGET_LUFS = -20.0
STRENGTHS = (0.2, 0.4, 0.6, 0.8, 1.0)
HRTFS = {
    "thk_ku100": ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa",
    "sadie2_d1": ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa",
}
OUT = ROOT / "qa" / "p0" / "cursor" / "samples_v2"


def read_wav(path: Path) -> np.ndarray:
    audio, rate = sf.read(str(path), always_2d=True, dtype="float64")
    if rate != SR:
        raise SystemExit(f"{path} is {rate} Hz")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    return np.ascontiguousarray(audio[:, :2].T)


def render(audio: np.ndarray, sofa: Path, hrtf: str, strength: float) -> np.ndarray:
    clear_preset_cache()
    cfg = RenderConfig(
        route="B",
        strength=strength,
        preset="room",
        hrtf_path=sofa,
        hrtf_name=hrtf,
        eq_mode="frontal",
        directional_power=True,
        timbre_guard=False,
        apply_fixed_eq=True,
        seed=20261016,
        block_size=256,
        sample_rate=SR,
    )
    proc = Processor()
    proc.prepare(SR, 256, cfg)
    wet = proc.process_file(audio)
    matched, _before, after, gain_db = broadband_loudness_match(wet, SR, TARGET_LUFS)
    print(f"  {hrtf} s={strength:.1f}  gain {gain_db:+.2f} dB  lufs {after:.2f}", flush=True)
    return matched


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for cat in ("music", "voice", "ambience"):
        dry = read_wav(ROOT / f"data/assets/real_{cat}.wav")
        matched, _b, after, gain_db = broadband_loudness_match(dry, SR, TARGET_LUFS)
        name = f"{cat}_dry_lufs20.wav"
        sf.write(str(OUT / name), matched.T, SR, subtype="PCM_24")
        print(f"{name}  gain {gain_db:+.2f} dB  lufs {after:.2f}", flush=True)
        for hrtf, sofa in HRTFS.items():
            for strength in STRENGTHS:
                wet = render(dry, sofa, hrtf, strength)
                tag = f"{cat}_routeB_str{strength:.1f}_hrtf-{hrtf}_eq-frontal.wav"
                sf.write(str(OUT / tag), wet.T, SR, subtype="PCM_24")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
