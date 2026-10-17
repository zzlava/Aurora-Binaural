#!/usr/bin/env python3
"""Pink-noise fixed EQ for route V, same method as P0-05.

Probes are dual-mono pink, partially correlated pink (ρ ≈ 0.6) and
uncorrelated pink. Real recordings are not an input. The stored curve is
the inverse of the mean 1/3-octave error, clamped to ±5 dB. SADIE II D1
is the design reference; KU100 is fitted only for preset V2, the
comparison render.

    prototypes/.venv/bin/python qa/p0/cursor/tools/design_fixed_eq_v.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa" / "tools"))
sys.path.insert(0, str(ROOT / "prototypes"))

import p0_metrics as metrics  # noqa: E402
from aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from aurora_p0.loudness import true_peak_dbtp  # noqa: E402
from aurora_p0.route_v import PRESET_PATH, clear_route_v_cache  # noqa: E402

SR = 48_000
STRENGTHS = (0.5, 1.0)
HRTFS = {
    "thk_ku100": ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa",
    "sadie2_d1": ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa",
}
# KU100 is the listening control for V2 only.
PLAN = {
    "V1": ("sadie2_d1",),
    "V2": ("sadie2_d1", "thk_ku100"),
    "V3": ("sadie2_d1",),
}
CLAMP = 5.0
GATE_DB = 5.0
PROBE_SCALE = 0.04


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    freqs[0] = 1.0
    spec = spec / np.sqrt(freqs)
    out = np.fft.irfft(spec, n=n)
    out /= np.sqrt(np.mean(out**2)) + 1e-12
    return out


def probes(seconds: float = 4.0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(20261016)
    n = int(SR * seconds)
    left = _pink(n, rng)
    right = _pink(n, rng)
    rho = 0.6
    partial = rho * left + np.sqrt(1.0 - rho * rho) * right
    return {
        "mono": np.stack([left, left]) * PROBE_SCALE,
        "rho": np.stack([left, partial]) * PROBE_SCALE,
        "unc": np.stack([left, right]) * PROBE_SCALE,
    }


def render(audio: np.ndarray, sofa: Path, hrtf: str, strength: float, preset: str, use_eq: bool) -> np.ndarray:
    clear_route_v_cache()
    cfg = RenderConfig(
        route="V",
        strength=strength,
        preset=preset,
        hrtf_path=sofa,
        hrtf_name=hrtf,
        eq_mode="frontal",
        directional_power=True,
        timbre_guard=False,
        apply_fixed_eq=use_eq,
        seed=20261016,
        block_size=256,
        sample_rate=SR,
    )
    proc = Processor()
    proc.prepare(SR, 256, cfg)
    return proc.process_file(audio)


def demed(wet: np.ndarray, dry: np.ndarray) -> dict[float, float]:
    aw = metrics.metrics(wet.T, SR)
    ad = metrics.metrics(dry.T, SR)
    delta = {k: float(aw["_levels"][k] - ad["_levels"][k]) for k in aw["_levels"] if 40 <= k <= 16000}
    med = float(np.median(list(delta.values())))
    return {k: v - med for k, v in delta.items()}


def _inverse_mean(rows: list[dict[float, float]]) -> dict[float, float]:
    bands = sorted(rows[0])
    mean = {b: float(np.mean([row[b] for row in rows])) for b in bands}
    out: dict[float, float] = {}
    for band in bands:
        value = float(np.clip(-mean[band], -CLAMP, CLAMP))
        out[band] = 0.0 if abs(value) < 0.05 else value
    return out


def _fmt_row(values: list[float]) -> str:
    return "[" + ", ".join(f"{v:.2f}" for v in values) + "]"


def write_preset(curves: dict[str, dict[str, dict[float, dict[float, float]]]], bands: list[float]) -> None:
    text = PRESET_PATH.read_text(encoding="utf-8")
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.startswith("[") and ".fixed_eq" in line:
            skipping = True
            continue
        if skipping:
            if line.startswith("[") and ".fixed_eq" not in line:
                skipping = False
            else:
                continue
        kept.append(line)
    while kept and kept[-1] == "":
        kept.pop()
    chunks = ["\n".join(kept), ""]
    for preset, hrtfs in curves.items():
        chunks.append(f"[{preset}.fixed_eq]")
        chunks.append("bands_hz = [" + ", ".join(f"{b:.3f}" for b in bands) + "]")
        chunks.append("strengths = [" + ", ".join(f"{s:.1f}" for s in STRENGTHS) + "]")
        chunks.append("")
        for hrtf, by_strength in hrtfs.items():
            chunks.append(f"[{preset}.fixed_eq.{hrtf}]")
            rows = []
            for strength in STRENGTHS:
                row = by_strength[strength]
                rows.append("  " + _fmt_row([row[b] for b in bands]))
            chunks.append("gain_db = [")
            chunks.append(",\n".join(rows))
            chunks.append("]")
            chunks.append("")
    PRESET_PATH.write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")
    clear_route_v_cache()


def design() -> list[float]:
    noise = probes()
    # The limiter must stay idle, or the curve fits peak shaving instead of the HRTF.
    probe_tp = true_peak_dbtp(
        render(noise["unc"], HRTFS["sadie2_d1"], "sadie2_d1", 1.0, "V2", False), SR
    )
    print(f"uncorrelated pink true peak at strength 1, no EQ: {probe_tp:.2f} dBTP", flush=True)
    if probe_tp > -2.0:
        raise SystemExit("pink probe is hot enough to engage the limiter; lower PROBE_SCALE")
    curves: dict[str, dict[str, dict[float, dict[float, float]]]] = {}
    bands: list[float] = []
    for preset, hrtf_names in PLAN.items():
        curves[preset] = {}
        for hrtf in hrtf_names:
            sofa = HRTFS[hrtf]
            curves[preset][hrtf] = {}
            for strength in STRENGTHS:
                rows = [demed(render(sig, sofa, hrtf, strength, preset, False), sig) for sig in noise.values()]
                if not bands:
                    bands = sorted(rows[0])
                gains = _inverse_mean(rows)
                curves[preset][hrtf][strength] = gains
                peak = max(abs(v) for v in gains.values())
                print(f"designed {preset} {hrtf} s={strength:.1f}  peak {peak:.2f} dB", flush=True)
    write_preset(curves, bands)
    print(f"wrote {PRESET_PATH}")
    return bands


def _catalog() -> dict[str, np.ndarray]:
    found = {}
    for name in ("real_music", "real_voice", "real_ambience", "holdout_electronic"):
        audio, sr = __import__("soundfile").read(ROOT / "data/assets" / f"{name}.wav", always_2d=True)
        if sr != SR:
            raise ValueError(name)
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        found[name] = audio[:, :2].T.astype(np.float64)
    return found


def verify() -> int:
    files = _catalog()
    failed = 0
    for preset, hrtf_names in PLAN.items():
        for hrtf in hrtf_names:
            sofa = HRTFS[hrtf]
            for strength in STRENGTHS:
                for cat, dry in files.items():
                    wet = render(dry, sofa, hrtf, strength, preset, True)
                    delta = demed(wet, dry)
                    mx = max(abs(v) for v in delta.values())
                    hot = {int(k): round(v, 2) for k, v in delta.items() if abs(v) > GATE_DB}
                    iacc = metrics.metrics(wet.T, SR)["iacc"]
                    dry_iacc = metrics.metrics(dry.T, SR)["iacc"]
                    if hot:
                        failed += 1
                    flag = "OK" if not hot else "FAIL"
                    print(
                        f"{flag} {preset:3} {cat:20} {hrtf:11} s={strength:.1f}  "
                        f"IACC {iacc:.3f} drop {dry_iacc - iacc:+.3f} max {mx:.2f} {hot}",
                        flush=True,
                    )
    return failed


if __name__ == "__main__":
    design()
    raise SystemExit(verify())
