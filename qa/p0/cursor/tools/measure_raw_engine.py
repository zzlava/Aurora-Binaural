#!/usr/bin/env python3
"""Raw engine vs dry, the same way as qa/tools/raw_engine_vs_dry.py.

Calls ``Processor.process_file`` directly. The only analysis after that is
the metric itself (IACC, 1/3-octave levels). No timbre guard, no loudness match.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa" / "tools"))
sys.path.insert(0, str(ROOT / "qa" / "p1" / "cursor"))

import p0_metrics as metrics  # noqa: E402
from compare_route_b import read_wav  # noqa: E402
from prototypes.aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from prototypes.aurora_p0.strength import clear_preset_cache  # noqa: E402

SR = 48_000
HRTF = {
    "thk_ku100": ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa",
    "sadie2_d1": ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa",
}
STRENGTHS = (0.2, 0.4, 0.6, 0.8, 1.0)


def render(audio: np.ndarray, sofa: Path, name: str, strength: float) -> np.ndarray:
    clear_preset_cache()
    cfg = RenderConfig(
        route="B",
        strength=strength,
        preset="room",
        hrtf_path=sofa,
        hrtf_name=name,
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
    out = proc.process_file(audio)
    return np.asarray(out, dtype=np.float64)


def _time_major(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x)
    if x.ndim == 1:
        x = np.stack([x, x], axis=0)
    if x.shape[0] == 2 and (x.shape[1] != 2):
        return np.ascontiguousarray(x.T)
    return np.ascontiguousarray(x)


def evaluate(wet: np.ndarray, dry: np.ndarray) -> dict:
    """IACC and median-removed 1/3-octave deviation, 40 Hz–16 kHz."""
    aw = _time_major(wet)
    ad = _time_major(dry)
    mw = metrics.metrics(aw, SR)
    md = metrics.metrics(ad, SR)
    delta = {k: mw["_levels"][k] - md["_levels"][k] for k in mw["_levels"] if 40 <= k <= 16000}
    med = float(np.median(list(delta.values())))
    shifted = {float(k): float(v - med) for k, v in delta.items()}
    hot = {int(k): round(v, 2) for k, v in shifted.items() if abs(v) > 3.0}
    return {
        "iacc": float(mw["iacc"]),
        "dry_iacc": float(md["iacc"]),
        "delta_iacc": float(md["iacc"] - mw["iacc"]),
        "max_abs_dev_db": float(max(abs(v) for v in shifted.values())),
        "offset_db": med,
        "bands_db": {str(int(k)): round(v, 2) for k, v in shifted.items()},
        "hot_db": hot,
    }


def run(categories: list[str], hrtfs: list[str], strengths: list[float]) -> dict:
    report: dict = {
        "method": "Processor.process_file, preset fixed EQ, no timbre guard, no loudness match",
        "rows": [],
    }
    for cat in categories:
        dry = read_wav(ROOT / f"data/assets/real_{cat}.wav")
        for name in hrtfs:
            sofa = HRTF[name]
            for strength in strengths:
                wet = render(dry, sofa, name, strength)
                row = evaluate(wet, dry)
                row.update({"category": cat, "hrtf": name, "strength": strength})
                report["rows"].append(row)
                flag = "OK" if _passes(cat, strength, row) else "FAIL"
                print(
                    f"{flag} {cat:9} {name:11} s={strength:.1f}  "
                    f"IACC {row['iacc']:.3f} dry {row['dry_iacc']:.3f} "
                    f"drop {row['delta_iacc']:+.3f}  max {row['max_abs_dev_db']:.2f} dB  "
                    f"hot {row['hot_db']}",
                    flush=True,
                )
    return report


def _passes(cat: str, strength: float, row: dict) -> bool:
    tone = row["max_abs_dev_db"] <= 3.0 + 1e-9
    if cat == "music" and abs(strength - 0.4) < 1e-9:
        return tone and row["delta_iacc"] >= 0.08 - 1e-9
    if cat == "music" and abs(strength - 0.8) < 1e-9:
        return tone and row["delta_iacc"] >= 0.15 - 1e-9
    if cat == "voice" and abs(strength - 0.4) < 1e-9:
        return tone and row["iacc"] >= 0.85 - 1e-9
    return tone


def main() -> None:
    quick = "--quick" in sys.argv
    categories = ["music", "voice"] if quick else ["music", "voice", "ambience"]
    hrtfs = ["thk_ku100"] if quick else ["thk_ku100", "sadie2_d1"]
    strengths = [0.4, 0.8] if quick else list(STRENGTHS)
    report = run(categories, hrtfs, strengths)
    if "--json" in sys.argv:
        dest = ROOT / "qa" / "p0" / "cursor" / "metrics_v2.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {dest}")


if __name__ == "__main__":
    main()
