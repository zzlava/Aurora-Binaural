#!/usr/bin/env python3
"""Design the fixed minimum-phase correction stored in presets/p0_draft.toml.

Probes (none of them is a delivery file):

- dual-mono pink
- partially correlated pink (ρ ≈ 0.6)
- uncorrelated pink

Each probe is rendered with ``apply_fixed_eq=False``. The 1/3-octave
mono-downmix error uses the same median removal as ``qa/tools/p0_metrics.py``.
The pink inverse is the target curve.

The three delivery files are a constraint, not a target: a band gain is
moved off the pink inverse only when that inverse would leave one of them
outside ±2.85 dB, and only as far as the nearest gain that puts every file
back inside. The filter still does not look at the signal at runtime.

Re-run from the repo root:

    prototypes/.venv/bin/python qa/p0/cursor/tools/design_fixed_eq.py
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
# Stay inside the ±3 dB gate with a little room for the FIR.
LIMIT = 2.85
CLAMP = 6.0


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


def solve(pink_mean: dict[float, float], materials: list[dict[float, float]]) -> dict[float, float]:
    gains: dict[float, float] = {}
    for band, target_src in pink_mean.items():
        values = [row[band] for row in materials]
        lo = max(-CLAMP, max(-LIMIT - v for v in values))
        hi = min(CLAMP, min(LIMIT - v for v in values))
        target = float(np.clip(-target_src, -CLAMP, CLAMP))
        if lo <= hi:
            gains[band] = float(np.clip(target, lo, hi))
        else:
            # The three files already span more than 2*LIMIT. Place the gain
            # so the loudest and quietest file sit the same distance from 0.
            center = -0.5 * (max(values) + min(values))
            gains[band] = float(np.clip(center, -CLAMP, CLAMP))
        if abs(gains[band]) < 0.05:
            gains[band] = 0.0
    return gains


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
    files = {cat: read_wav(ROOT / f"data/assets/real_{cat}.wav") for cat in ("music", "voice", "ambience")}
    noise = probes()
    curves: dict[str, dict[str, dict[float, dict[float, float]]]] = {}
    bands: list[float] = []
    for preset in presets:
        curves[preset] = {}
        for hrtf, sofa in HRTFS.items():
            curves[preset][hrtf] = {}
            for strength in STRENGTHS:
                pink_rows = [demed(render(sig, sofa, hrtf, strength, preset, False), sig) for sig in noise.values()]
                mat_rows = [demed(render(sig, sofa, hrtf, strength, preset, False), sig) for sig in files.values()]
                if not bands:
                    bands = sorted(mat_rows[0])
                pink_mean = {b: float(np.mean([row[b] for row in pink_rows])) for b in bands}
                gains = solve(pink_mean, mat_rows)
                curves[preset][hrtf][strength] = gains
                nz = sum(1 for v in gains.values() if abs(v) >= 0.05)
                print(f"designed {preset} {hrtf} s={strength:.1f}  nonzero bands {nz}", flush=True)
    write_preset(curves, bands)
    print(f"wrote {PRESET_PATH}")


def verify(presets: tuple[str, ...]) -> int:
    files = {cat: read_wav(ROOT / f"data/assets/real_{cat}.wav") for cat in ("music", "voice", "ambience")}
    failed = 0
    for preset in presets:
        for hrtf, sofa in HRTFS.items():
            for strength in STRENGTHS:
                for cat, dry in files.items():
                    wet = render(dry, sofa, hrtf, strength, preset, True)
                    delta = demed(wet, dry)
                    mx = max(abs(v) for v in delta.values())
                    hot = {int(k): round(v, 2) for k, v in delta.items() if abs(v) > 3.0}
                    iacc = metrics.metrics(wet.T, SR)["iacc"]
                    dry_iacc = metrics.metrics(dry.T, SR)["iacc"]
                    drop = dry_iacc - iacc
                    ok = not hot
                    if preset == "room" and cat == "music" and abs(strength - 0.4) < 1e-9:
                        ok = ok and drop >= 0.08 - 1e-9
                    if preset == "room" and cat == "music" and abs(strength - 0.8) < 1e-9:
                        ok = ok and drop >= 0.15 - 1e-9
                    if preset == "room" and cat == "voice" and abs(strength - 0.4) < 1e-9:
                        ok = ok and iacc >= 0.85 - 1e-9
                    if not ok:
                        failed += 1
                    flag = "OK" if ok else "FAIL"
                    print(
                        f"{flag} {preset:9} {cat:9} {hrtf:11} s={strength:.1f}  "
                        f"IACC {iacc:.3f} drop {drop:+.3f} max {mx:.2f} {hot}",
                        flush=True,
                    )
    return failed


def _load_curves(presets: tuple[str, ...]) -> tuple[dict, list[float]]:
    import tomllib

    data = tomllib.loads(PRESET_PATH.read_text(encoding="utf-8"))
    curves: dict = {}
    bands: list[float] = []
    for preset in presets:
        block = data[preset].get("fixed_eq")
        if not isinstance(block, dict):
            raise SystemExit(f"{preset} has no fixed_eq; run a design pass first")
        bands = [float(b) for b in block["bands_hz"]]
        curves[preset] = {}
        for hrtf in HRTFS:
            matrix = block[hrtf]["gain_db"]
            curves[preset][hrtf] = {}
            for strength, row in zip(STRENGTHS, matrix):
                curves[preset][hrtf][strength] = {b: float(g) for b, g in zip(bands, row)}
    return curves, bands


def adjust(presets: tuple[str, ...], passes: int = 2) -> None:
    """Pull the stored curve until the rendered files sit inside the gate.

    The pink inverse is already in the file. Each pass adds only the extra
    gain the files still need, so the curve stays next to that inverse.
    """
    files = {cat: read_wav(ROOT / f"data/assets/real_{cat}.wav") for cat in ("music", "voice", "ambience")}
    curves, bands = _load_curves(presets)
    for attempt in range(passes):
        changed = False
        for preset in presets:
            for hrtf, sofa in HRTFS.items():
                for strength in STRENGTHS:
                    measured = [
                        demed(render(sig, sofa, hrtf, strength, preset, True), sig) for sig in files.values()
                    ]
                    rows = []
                    for row in measured:
                        rows.append({b: row[min(row, key=lambda k: abs(k - b))] for b in bands})
                    extra = solve({b: 0.0 for b in bands}, rows)
                    if all(abs(v) < 0.05 for v in extra.values()):
                        continue
                    changed = True
                    current = curves[preset][hrtf][strength]
                    for band in bands:
                        current[band] = float(np.clip(current[band] + extra[band], -CLAMP, CLAMP))
                    print(f"adjust {attempt + 1} {preset} {hrtf} s={strength:.1f}", flush=True)
        if not changed:
            break
        write_preset(curves, bands)
    print(f"wrote {PRESET_PATH}")


def main() -> None:
    presets = tuple(a for a in sys.argv[1:] if not a.startswith("-")) or PRESETS
    if "--adjust" in sys.argv:
        adjust(presets)
    elif "--verify" not in sys.argv:
        design(presets)
    failed = verify(presets)
    if failed:
        raise SystemExit(f"{failed} rows still outside the gate")


if __name__ == "__main__":
    main()
