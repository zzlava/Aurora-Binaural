#!/usr/bin/env python3
"""Design the fixed minimum-phase correction stored in presets/p0_draft.toml.

The curve comes only from pink probes rendered with ``apply_fixed_eq=False``:

- dual-mono pink
- partially correlated pink (ρ ≈ 0.6)
- uncorrelated pink

None of these is a delivery file, and none of the real recordings is read
while the gains are chosen. The 1/3-octave mono-downmix error uses the same
median removal as ``qa/tools/p0_metrics.py``. The target is the inverse of
the mean error across the three correlations, then a 3-point moving average
and a limit on the step between adjacent 1/3-octave bands.

Re-run from the repo root:

    prototypes/.venv/bin/python qa/p0/cursor/tools/design_fixed_eq.py room
"""

from __future__ import annotations

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
from prototypes.aurora_p0.strength import PRESET_PATH, clear_preset_cache  # noqa: E402

SR = 48_000
STRENGTHS = (0.2, 0.4, 0.6, 0.8, 1.0)
HRTFS = {
    "thk_ku100": ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa",
    "sadie2_d1": ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa",
}
PRESETS = ("room", "surround", "orbit")
# Verification gate for real files. Not used while choosing gains.
GATE_DB = 3.5
CLAMP = 6.0
# Adjacent 1/3-octave bands may not differ by more than this.
MAX_STEP_DB = 2.5


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
    scale = 0.15
    return {
        "mono": np.stack([left, left]) * scale,
        "rho": np.stack([left, partial]) * scale,
        "unc": np.stack([left, right]) * scale,
    }


def render(audio: np.ndarray, sofa: Path, hrtf: str, strength: float, preset: str, use_eq: bool) -> np.ndarray:
    clear_preset_cache()
    cfg = RenderConfig(
        route="B",
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


def smooth_gains(raw: dict[float, float]) -> dict[float, float]:
    """3-point average, then a cap on the step between neighbouring bands."""
    bands = sorted(raw)
    vals = np.array([raw[b] for b in bands], dtype=np.float64)
    sm = vals.copy()
    if len(vals) >= 3:
        sm[1:-1] = (vals[:-2] + vals[1:-1] + vals[2:]) / 3.0
        sm[0] = 0.5 * (vals[0] + vals[1])
        sm[-1] = 0.5 * (vals[-1] + vals[-2])
    for i in range(1, len(sm)):
        sm[i] = float(np.clip(sm[i], sm[i - 1] - MAX_STEP_DB, sm[i - 1] + MAX_STEP_DB))
    for i in range(len(sm) - 2, -1, -1):
        sm[i] = float(np.clip(sm[i], sm[i + 1] - MAX_STEP_DB, sm[i + 1] + MAX_STEP_DB))
    out: dict[float, float] = {}
    for band, value in zip(bands, sm):
        value = float(np.clip(value, -CLAMP, CLAMP))
        out[band] = 0.0 if abs(value) < 0.05 else value
    return out


def _inverse_mean(rows: list[dict[float, float]]) -> dict[float, float]:
    bands = sorted(rows[0])
    mean = {b: float(np.mean([row[b] for row in rows])) for b in bands}
    return {b: float(np.clip(-mean[b], -CLAMP, CLAMP)) for b in bands}


def pink_gains(rows: list[dict[float, float]]) -> dict[float, float]:
    # Smoothing is optional. The first pass showed a 3-point average wiping
    # out a 2 kHz correction that only the partially correlated probe needs,
    # so the stored curve is the clamped mean inverse with no extra smoothing.
    return _inverse_mean(rows)


def _fmt_row(values: list[float]) -> str:
    return "[" + ", ".join(f"{v:.2f}" for v in values) + "]"


def write_preset(curves: dict[str, dict[str, dict[float, dict[float, float]]]], bands: list[float]) -> None:
    """Replace every ``fixed_eq`` block. ``curves[preset][hrtf][strength][band]``."""
    text = PRESET_PATH.read_text(encoding="utf-8")
    # Drop a previously written EQ so the script is idempotent.
    kept = []
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
    clear_preset_cache()


def design(presets: tuple[str, ...]) -> None:
    noise = probes()
    curves: dict[str, dict[str, dict[float, dict[float, float]]]] = {}
    bands: list[float] = []
    for preset in presets:
        curves[preset] = {}
        for hrtf, sofa in HRTFS.items():
            curves[preset][hrtf] = {}
            for strength in STRENGTHS:
                pink_rows = [demed(render(sig, sofa, hrtf, strength, preset, False), sig) for sig in noise.values()]
                if not bands:
                    bands = sorted(pink_rows[0])
                gains = pink_gains(pink_rows)
                curves[preset][hrtf][strength] = gains
                peak = max(abs(v) for v in gains.values())
                print(f"designed {preset} {hrtf} s={strength:.1f}  peak {peak:.2f} dB", flush=True)
    write_preset(curves, bands)
    print(f"wrote {PRESET_PATH}")


def _catalog() -> dict[str, tuple[np.ndarray, str]]:
    """Real files for verification only. ``role`` is train or holdout."""
    found: dict[str, tuple[np.ndarray, str]] = {}
    for cat in ("music", "voice", "ambience"):
        found[cat] = (read_wav(ROOT / f"data/assets/real_{cat}.wav"), "train")
    for name in ("holdout_piano", "holdout_electronic"):
        found[name] = (read_wav(ROOT / f"data/assets/{name}.wav"), "holdout")
    return found


def _is_music(name: str) -> bool:
    return name == "music" or name.startswith("holdout_")


def verify(presets: tuple[str, ...]) -> int:
    files = _catalog()
    failed = 0
    for preset in presets:
        for hrtf, sofa in HRTFS.items():
            for strength in STRENGTHS:
                for cat, (dry, role) in files.items():
                    wet = render(dry, sofa, hrtf, strength, preset, True)
                    delta = demed(wet, dry)
                    mx = max(abs(v) for v in delta.values())
                    hot = {int(k): round(v, 2) for k, v in delta.items() if abs(v) > GATE_DB}
                    iacc = metrics.metrics(wet.T, SR)["iacc"]
                    dry_iacc = metrics.metrics(dry.T, SR)["iacc"]
                    drop = dry_iacc - iacc
                    ok = not hot
                    if preset == "room" and _is_music(cat) and abs(strength - 0.4) < 1e-9:
                        ok = ok and drop >= 0.08 - 1e-9
                    if preset == "room" and _is_music(cat) and abs(strength - 0.8) < 1e-9:
                        ok = ok and drop >= 0.15 - 1e-9
                    if preset == "room" and cat == "voice" and abs(strength - 0.4) < 1e-9:
                        ok = ok and iacc >= 0.85 - 1e-9
                    if not ok:
                        failed += 1
                    flag = "OK" if ok else "FAIL"
                    print(
                        f"{flag} {role:7} {preset:9} {cat:20} {hrtf:11} s={strength:.1f}  "
                        f"IACC {iacc:.3f} drop {drop:+.3f} max {mx:.2f} {hot}",
                        flush=True,
                    )
    return failed


def diagnose(presets: tuple[str, ...]) -> None:
    """Predict residuals of the pink curve. Does not write the preset.

    Real files are rendered only so the prediction can be printed. Their
    errors are not fed back into the gains.
    """
    noise = probes()
    files = _catalog()
    strengths = (0.4, 0.8)
    for preset in presets:
        for hrtf, sofa in HRTFS.items():
            for strength in strengths:
                pink_rows = [demed(render(sig, sofa, hrtf, strength, preset, False), sig) for sig in noise.values()]
                plain = _inverse_mean(pink_rows)
                smoothed = smooth_gains(plain)
                print(
                    f"\n== predict {preset} {hrtf} s={strength:.1f} "
                    f"plain {max(abs(v) for v in plain.values()):.2f} "
                    f"smooth {max(abs(v) for v in smoothed.values()):.2f}",
                    flush=True,
                )
                rendered = {
                    cat: demed(render(dry, sofa, hrtf, strength, preset, False), dry)
                    for cat, (dry, _role) in files.items()
                }
                for label, gains in (("plain", plain), ("smooth", smoothed)):
                    worst = 0.0
                    bits = []
                    for cat, err in rendered.items():
                        pred = {b: err[b] + gains[min(gains, key=lambda g: abs(g - b))] for b in err}
                        mx = max(abs(v) for v in pred.values())
                        worst = max(worst, mx)
                        hot = {int(k): round(v, 2) for k, v in pred.items() if abs(v) > GATE_DB}
                        if hot:
                            bits.append(f"{cat} {mx:.2f} {hot}")
                    print(f"  {label:6} worst {worst:.2f}  {'; '.join(bits) or 'all inside'}", flush=True)


def main() -> None:
    presets = tuple(a for a in sys.argv[1:] if not a.startswith("-")) or PRESETS
    if "--adjust" in sys.argv:
        raise SystemExit("refusing --adjust: that pass fits real recordings into the curve")
    if "--diagnose" in sys.argv:
        diagnose(presets)
        return
    if "--verify" not in sys.argv:
        design(presets)
    failed = verify(presets)
    if failed:
        raise SystemExit(f"{failed} rows still outside the gate")


if __name__ == "__main__":
    main()
