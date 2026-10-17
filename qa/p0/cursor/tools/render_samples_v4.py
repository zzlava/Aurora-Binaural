#!/usr/bin/env python3
"""Listening samples for route V, plus the engine measurements for REPORT-v4.

Samples are broadband-loudness matched to −20 LUFS. IACC and the 1/3-octave
deviation are taken from ``Processor.process_file`` before that gain, which
does not change either number. The true-peak limiter inside the engine stays
on.

    prototypes/.venv/bin/python qa/p0/cursor/tools/render_samples_v4.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, correlate, sosfilt

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa" / "tools"))
sys.path.insert(0, str(ROOT / "prototypes"))

import p0_metrics as metrics  # noqa: E402
from aurora_p0 import SAMPLE_RATE, TARGET_LUFS  # noqa: E402
from aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from aurora_p0.loudness import broadband_loudness_match, true_peak_dbtp  # noqa: E402
from aurora_p0.route_v import clear_route_v_cache, map_route_v  # noqa: E402

SR = SAMPLE_RATE
OUT = ROOT / "qa" / "p0" / "cursor" / "samples_v4"
HRTF = {
    "sadie2_d1": ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa",
    "thk_ku100": ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa",
}
MATERIALS = {
    "voice": "real_voice.wav",
    "music": "real_music.wav",
    "ambience": "real_ambience.wav",
    "electronic": "holdout_electronic.wav",
}
JOBS = [(preset, strength, "sadie2_d1") for preset in ("V1", "V2", "V3") for strength in (0.5, 1.0)]
JOBS += [("V2", 1.0, "thk_ku100")]


def load_material(name: str) -> np.ndarray:
    audio, sr = sf.read(ROOT / "data/assets" / MATERIALS[name], always_2d=True)
    if sr != SR:
        raise ValueError(f"{name} sr={sr}")
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    return np.ascontiguousarray(audio[:, :2].T, dtype=np.float64)


def render(audio: np.ndarray, preset: str, strength: float, hrtf: str) -> tuple[np.ndarray, dict]:
    clear_route_v_cache()
    cfg = RenderConfig(
        route="V",
        strength=strength,
        preset=preset,
        hrtf_path=HRTF[hrtf],
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
    return wet, dict(proc.last_stats)


def evaluate(wet: np.ndarray, dry: np.ndarray) -> dict:
    mw = metrics.metrics(wet.T, SR)
    md = metrics.metrics(dry.T, SR)
    delta = {k: mw["_levels"][k] - md["_levels"][k] for k in mw["_levels"] if 40 <= k <= 16000}
    med = float(np.median(list(delta.values())))
    shifted = {float(k): float(v - med) for k, v in delta.items()}
    hot = {str(int(k)): round(v, 2) for k, v in shifted.items() if abs(v) > 5.0}
    return {
        "iacc": float(mw["iacc"]),
        "dry_iacc": float(md["iacc"]),
        "delta_iacc": float(md["iacc"] - mw["iacc"]),
        "max_abs_dev_db": float(max(abs(v) for v in shifted.values())),
        "offset_db": med,
        "hot_db": hot,
        "bands_db": {str(int(k)): round(v, 2) for k, v in shifted.items()},
    }


def projection_drr(wet: np.ndarray, dry: np.ndarray, sr: int = SR) -> float:
    """Ratio of the dry-aligned mid to what is left, 200 Hz–3.5 kHz.

    Same estimator on route V and on anchor G. HRTF coloration stays in the
    residual, so the number is lower than the engine's bus DRR; the ordering
    is what the comparison uses.
    """
    sos = butter(4, [200.0, 3500.0], btype="band", fs=sr, output="sos")
    w = sosfilt(sos, 0.5 * (wet[0] + wet[1]))
    d = sosfilt(sos, 0.5 * (dry[0] + dry[1]))
    n = min(w.shape[0], d.shape[0])
    w, d = w[:n], d[:n]
    corr = correlate(w, d, mode="full", method="fft")
    mid = n - 1
    span = int(0.04 * sr)
    region = corr[mid - span : mid + span + 1]
    lag = int(np.argmax(np.abs(region))) - span
    if lag >= 0:
        aligned = np.concatenate([np.zeros(lag), d])[:n]
    else:
        aligned = np.concatenate([d[-lag:], np.zeros(-lag)])[:n]
    gain = float(np.dot(aligned, w) / (np.dot(aligned, aligned) + 1e-20))
    direct = gain * aligned
    resid = w - direct
    return float(10.0 * np.log10((np.sum(direct**2) + 1e-20) / (np.sum(resid**2) + 1e-20)))


def anchor_g_voice(dry: np.ndarray) -> dict:
    """Component energies of calibration anchor G. The tool file is not modified."""
    text = (ROOT / "qa/tools/calibration_anchors.py").read_text(encoding="utf-8")
    cut = text.split("meter = pyln.Meter")[0]
    ns: dict = {}
    saved = sys.argv
    sys.argv = ["calibration_anchors.py", str(ROOT), "/tmp/aurora_anchor_unused"]
    try:
        exec(cut, ns)  # noqa: S102 — read-only load of the reference functions
    finally:
        sys.argv = saved
    xin = np.ascontiguousarray(dry.T)
    direct = ns["place"](xin[:, 0], 45) + ns["place"](xin[:, 1], -45)
    early = ns["early"](xin) * ns["db"](-3)
    late = ns["reverb"](xin, 1.0) * ns["db"](-6)
    mid = xin.mean(1)
    ceiling = ns["reverb"](np.stack([mid, mid], 1), 1.0, pre_ms=6) * ns["db"](-12)
    room = early + late + ceiling
    mix = ns["anchor_G"](xin)

    def energy(z: np.ndarray) -> float:
        return float(np.sum(np.asarray(z) ** 2))

    return {
        "hrtf": "thk_ku100",
        "bus_drr_db": float(10.0 * np.log10((energy(direct) + 1e-20) / (energy(room) + 1e-20))),
        "early_vs_direct_db": float(10.0 * np.log10((energy(early) + 1e-20) / (energy(direct) + 1e-20))),
        "late_vs_direct_db": float(10.0 * np.log10((energy(late) + 1e-20) / (energy(direct) + 1e-20))),
        "projection_drr_db": projection_drr(mix.T, dry),
        "note": "G sends the whole mid into early reflections at −3 dB and the late tail at −6 dB, on KU100.",
    }


def resolved_params() -> dict:
    out = {}
    for preset in ("V1", "V2", "V3"):
        for strength in (0.5, 1.0):
            p = map_route_v(strength, preset, "sadie2_d1")
            out[f"{preset}@{strength:.1f}"] = {
                "front_az": round(p.front_az, 2),
                "center_spread_deg": round(p.center_spread_deg, 2),
                "center_distance_m": p.center_distance_m,
                "direct_room_send_db": p.direct_room_send_db,
                "wide_lin_db": round(20.0 * np.log10(p.wide_lin + 1e-20), 2),
                "surround_lin_db": round(20.0 * np.log10(p.surround_lin + 1e-20), 2),
                "height_lin_db": round(20.0 * np.log10(p.height_lin + 1e-20), 2),
                "early_lin_db": round(20.0 * np.log10(p.early_lin + 1e-20), 2),
                "late_lin_db": round(20.0 * np.log10(p.late_lin + 1e-20), 2),
                "height_el": p.height_el,
                "wide_az": p.wide_az,
                "surround_az": p.surround_az,
                "t60": p.t60,
                "ceiling_db": p.ceiling_db,
            }
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dry = {name: load_material(name) for name in MATERIALS}
    report: dict = {
        "method": "Processor.process_file route V, fixed EQ on, true-peak limiter on, metrics before the broadband loudness match",
        "gate_db": 5.0,
        "target_lufs": TARGET_LUFS,
        "params": resolved_params(),
        "rows": [],
    }
    for name, audio in dry.items():
        matched, before, after, gain_db = broadband_loudness_match(audio, SR, TARGET_LUFS)
        path = OUT / f"{name}_dry_lufs20.wav"
        sf.write(path, matched.T, SR, subtype="PCM_24")
        print(f"dry {name} {before:.1f} -> {after:.1f} LUFS", flush=True)
    for preset, strength, hrtf in JOBS:
        for name, audio in dry.items():
            wet, stats = render(audio, preset, strength, hrtf)
            row = evaluate(wet, audio)
            matched, before, after, gain_db = broadband_loudness_match(wet, SR, TARGET_LUFS)
            path = OUT / f"{name}_{preset}_str{strength:.1f}_hrtf-{hrtf}.wav"
            sf.write(path, matched.T, SR, subtype="PCM_24")
            sample_tp = true_peak_dbtp(matched, SR)
            entry = {
                "file": path.name,
                "material": name,
                "preset": preset,
                "strength": strength,
                "hrtf": hrtf,
                "loudness_before": before,
                "loudness_lufs": after,
                "match_gain_db": gain_db,
                "sample_true_peak_dbtp": sample_tp,
                "engine_true_peak_dbtp": stats.get("true_peak_dbtp"),
                "drr_center_db": stats.get("drr_center_db"),
                "drr_direct_db": stats.get("drr_direct_db"),
                "drr_ambient_db": stats.get("drr_ambient_db"),
                "projection_drr_db": projection_drr(wet, audio) if name == "voice" else None,
                "front_az": stats.get("front_az"),
                "center_spread_deg": stats.get("center_spread_deg"),
                "taps": stats.get("taps") if name == "voice" and preset == "V2" and strength == 1.0 and hrtf == "sadie2_d1" else None,
                **row,
            }
            report["rows"].append(entry)
            print(
                f"{path.name} IACC {row['iacc']:.3f} drop {row['delta_iacc']:+.3f} "
                f"max {row['max_abs_dev_db']:.2f} drrC {entry['drr_center_db']:.1f} tp {sample_tp:.2f}",
                flush=True,
            )
    print("anchor G on voice…", flush=True)
    report["anchor_g_voice"] = anchor_g_voice(dry["voice"])
    print(
        f"G bus DRR {report['anchor_g_voice']['bus_drr_db']:.2f}  "
        f"projection {report['anchor_g_voice']['projection_drr_db']:.2f}",
        flush=True,
    )
    # Where the music actually has energy. 8 kHz readings sit on a quiet band.
    levels = metrics.metrics(dry["music"].T, SR)["_levels"]
    ref = levels.get(1000.0, levels[min(levels, key=lambda f: abs(f - 1000.0))])
    report["music_vs_1khz_db"] = {str(int(k)): round(v - ref, 1) for k, v in levels.items() if 1000 <= k <= 16000}
    dest = ROOT / "qa" / "p0" / "cursor" / "metrics_v4.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
