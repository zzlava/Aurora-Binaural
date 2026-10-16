#!/usr/bin/env python3
"""Split route B into buses and compare each to the dry file.

Buses: direct objects, wide pair, rear ambient, side decorrelator,
early reflections, FDN, low-frequency bypass. Levels use the same
mono 1/3-octave method as qa/tools/p0_metrics.py (the review script).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa" / "tools"))
sys.path.insert(0, str(ROOT / "qa" / "p1" / "cursor"))

import p0_metrics as m  # noqa: E402
from compare_route_b import read_wav  # noqa: E402
from prototypes.aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from prototypes.aurora_p0.filters import delay_buffer  # noqa: E402
from prototypes.aurora_p0.metrics import iacc  # noqa: E402
from prototypes.aurora_p0.upmix import decorrelate  # noqa: E402

SR = 48_000
SOFA = ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa"


def _as_time_major(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x)
    if x.ndim == 1:
        x = np.stack([x, x], axis=0)
    if x.shape[0] == 2 and x.shape[1] != 2:
        return x.T
    return x


def band_delta(wet: np.ndarray, dry: np.ndarray) -> dict[int, float]:
    a = m.metrics(_as_time_major(wet), SR)
    b = m.metrics(_as_time_major(dry), SR)
    d = {k: a["_levels"][k] - b["_levels"][k] for k in a["_levels"] if 40 <= k <= 16000}
    med = float(np.median(list(d.values())))
    return {int(k): round(v - med, 2) for k, v in d.items()}


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x) ** 2) + 1e-20))


def split(stereo: np.ndarray, strength: float) -> dict[str, np.ndarray]:
    cfg = RenderConfig(
        route="B",
        strength=strength,
        preset="room",
        hrtf_path=SOFA,
        hrtf_name="thk_ku100",
        eq_mode="frontal",
        directional_power=True,
        timbre_guard=False,
        seed=20261016,
        block_size=256,
        sample_rate=SR,
    )
    proc = Processor()
    proc.prepare(SR, 256, cfg)
    assert proc.params is not None and proc.cfg is not None
    low, high = proc._crossover_file(stereo)
    low_mono = 0.5 * (low[0] + low[1])
    low_out = np.stack([low_mono, low_mono], axis=0)
    up = proc._run_upmix(high)
    latency = int(up["latency"])
    sectors = np.asarray(up["sectors"])
    azs = np.asarray(up["sector_azimuths"], dtype=np.float64)
    direct = np.zeros((2, high.shape[1]), dtype=np.float64)
    for s, az in enumerate(azs):
        direct += proc._conv_mono(sectors[s], float(az), 0.0)
    wide_l = decorrelate(np.asarray(up["wide_l"]), SR, cfg.seed + 3)
    wide_r = decorrelate(np.asarray(up["wide_r"]), SR, cfg.seed + 4)
    amb_l = decorrelate(np.asarray(up["ambient_l"]), SR, cfg.seed + 5)
    amb_r = decorrelate(np.asarray(up["ambient_r"]), SR, cfg.seed + 6)
    wide = proc._conv_mono(wide_l, float(up["wide_deg"])) + proc._conv_mono(wide_r, -float(up["wide_deg"]))
    rear = proc._conv_mono(amb_l, float(up["rear_deg"])) + proc._conv_mono(amb_r, -float(up["rear_deg"]))
    side = delay_buffer(proc._side_feed(high), latency + int(proc._hrir_delay))
    mid = 0.5 * (high[0] + high[1])
    er_mono, directed = proc._er_file(mid)
    early = np.zeros((2, mid.shape[0]), dtype=np.float64)
    if proc.params.early_level > 0:
        for sig, az, el in directed[:6]:
            early += proc.params.early_level * proc._conv_mono(sig, az, el)
    late = np.zeros_like(early)
    if proc.params.late_level > 0:
        feed = mid * 0.7 + er_mono * 0.3
        late = proc.params.late_level * proc._fdn_file(feed)
    early = delay_buffer(early, latency)
    late = delay_buffer(late, latency)
    low_out = delay_buffer(low_out, latency + int(proc._hrir_delay))
    drr = 10 ** (proc.params.drr_offset_db / 20.0)
    image = (direct + wide + rear + side) * drr
    full = image + early + late + low_out
    stems = {
        "direct_l": np.asarray(up["direct_l"]),
        "direct_r": np.asarray(up["direct_r"]),
        "wide_l": np.asarray(up["wide_l"]),
        "wide_r": np.asarray(up["wide_r"]),
        "ambient_l": np.asarray(up["ambient_l"]),
        "ambient_r": np.asarray(up["ambient_r"]),
    }
    return {
        "full": full,
        "direct": direct * drr,
        "wide": wide * drr,
        "rear": rear * drr,
        "side": side * drr,
        "early": early,
        "late": late,
        "low": low_out,
        "image": image,
        "_coherence": float(up["coherence_mean"]),
        "_width": float(proc.params.width_deg),
        "_ambient": float(proc.params.ambient_ratio),
        "_side_gain": float(proc.params.side_decor_gain),
        "_drr": float(proc.params.drr_offset_db),
        "_az": azs,
        "_stems": stems,
        "_phi_note": "coherence_mean is the upmixer average",
    }


def stem_power(stems: dict[str, np.ndarray]) -> dict[str, float]:
    pairs = {
        "direct": stems["direct_l"] ** 2 + stems["direct_r"] ** 2,
        "wide": stems["wide_l"] ** 2 + stems["wide_r"] ** 2,
        "ambient": stems["ambient_l"] ** 2 + stems["ambient_r"] ** 2,
    }
    total = sum(float(np.mean(v)) for v in pairs.values()) + 1e-20
    return {k: round(float(np.mean(v)) / total, 3) for k, v in pairs.items()}


def main() -> None:
    for cat in ("music", "voice", "ambience"):
        dry = read_wav(ROOT / f"data/assets/real_{cat}.wav")
        for strength in (0.4, 0.8):
            parts = split(dry, strength)
            print(
                f"\n== {cat} s={strength} width={parts['_width']:.1f} "
                f"amb={parts['_ambient']:.2f} side={parts['_side_gain']:.2f} "
                f"drr={parts['_drr']:.1f} coh={parts['_coherence']:.3f} az={np.round(parts['_az'],1)}"
            )
            print("  stem energy fractions", stem_power(parts["_stems"]))
            dry_i = iacc(dry, SR)
            print(f"  dry IACC {dry_i:.3f}")
            for name in ("direct", "wide", "rear", "side", "early", "late", "low", "image", "full"):
                bus = parts[name]
                rel = 20.0 * np.log10(rms(bus) / (rms(dry) + 1e-20))
                delta = band_delta(bus, dry)
                hot = {k: v for k, v in delta.items() if abs(v) >= 3.0}
                print(
                    f"  {name:6} IACC={iacc(bus, SR):.3f}  relRMS={rel:6.1f} dB  "
                    f"max|dev|={max(abs(v) for v in delta.values()):.1f}  >3dB={hot}"
                )


if __name__ == "__main__":
    main()
