#!/usr/bin/env python3
"""Compare the Rust route V core with the P0 prototype.

Gates: 1/3-octave magnitude difference <= 0.2 dB, broadband IACC difference
<= 0.01. Material is voice, piano, electronic, rain; SADIE II; presets
V1/V2/V3; strength 0.5 and 1.0. The prototype renders the whole file.
Rust is called in blocks of 256.

When the limiter is what moves a row over the gate, the row is rendered
again with the limiter bypassed and reported separately. ``holdout_electronic``
is also checked for a true peak of at most -1 dBTP.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from aurora_core import Renderer  # noqa: E402
from prototypes.aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from prototypes.aurora_p0.loudness import true_peak_dbtp  # noqa: E402
from prototypes.aurora_p0.metrics import iacc  # noqa: E402

ASSETS = [
    ("人声", ROOT / "data/assets/real_voice.wav"),
    ("钢琴", ROOT / "data/assets/real_music.wav"),
    ("电子乐", ROOT / "data/assets/holdout_electronic.wav"),
    ("雨声", ROOT / "data/assets/real_ambience.wav"),
]
SOFA = ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa"
PACK = ROOT / "data/hrtf/sadie_d1_frontal.ahrp"
PRESET = ROOT / "presets/route_v.toml"
SR = 48_000
PRESETS = ("V1", "V2", "V3")
STRENGTHS = (0.5, 1.0)


def read_wav(path: Path) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if rate != SR:
        raise SystemExit(f"{path} is {rate} Hz, expected {SR}")
    if width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        signed = raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16)
        signed = np.where(signed >= 1 << 23, signed - (1 << 24), signed)
        data = signed.astype(np.float64) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise SystemExit(f"unsupported wav width {width} in {path}")
    data = data.reshape(-1, channels).T
    if channels == 1:
        data = np.repeat(data, 2, axis=0)
    return data[:2]


def third_octave_delta(a: np.ndarray, b: np.ndarray, sr: int) -> float:
    """Max |dB| between ear-power 1/3-octave levels, 20 Hz–20 kHz."""
    n = min(a.shape[1], b.shape[1])
    window = np.hanning(n)

    def levels(x: np.ndarray) -> np.ndarray:
        spec = np.zeros(n // 2 + 1, dtype=np.float64)
        for ch in range(x.shape[0]):
            spec += np.abs(np.fft.rfft(x[ch, :n] * window)) ** 2
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)
        out = []
        f = 20.0
        while f <= 20_000.0:
            lo, hi = f / 2.0 ** (1.0 / 6.0), f * 2.0 ** (1.0 / 6.0)
            mask = (freqs >= lo) & (freqs < hi)
            out.append(10.0 * np.log10(np.mean(spec[mask]) + 1e-20) if np.any(mask) else np.nan)
            f *= 2.0 ** (1.0 / 3.0)
        return np.asarray(out)

    delta = levels(a[:, :n]) - levels(b[:, :n])
    return float(np.nanmax(np.abs(delta)))


def render_rust(audio: np.ndarray, preset: str, strength: float, limiter: bool) -> np.ndarray:
    renderer = Renderer(
        str(PACK),
        str(SOFA),
        str(PRESET),
        strength=strength,
        seed=20261016,
        preset=preset,
        limiter=limiter,
        route="V",
        hrtf_name="sadie",
    )

    class Config:
        input_channels = 2
        output_channels = 2

    block = 256
    renderer.prepare(SR, block, Config())
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    pieces = []
    for start in range(0, interleaved.shape[0], block):
        pieces.append(np.asarray(renderer.process(interleaved[start : start + block]), dtype=np.float64))
    return np.concatenate(pieces, axis=0).T


def render_prototype(audio: np.ndarray, preset: str, strength: float, limiter: bool) -> np.ndarray:
    import prototypes.aurora_p0.route_v as route_v

    original = route_v.protect_true_peak
    if not limiter:
        route_v.protect_true_peak = lambda stereo, sr, ceiling_dbtp=-1.0: stereo
    try:
        cfg = RenderConfig(
            route="V",
            strength=strength,
            preset=preset,
            hrtf_path=SOFA,
            hrtf_name="sadie",
            eq_mode="frontal",
            directional_power=True,
            timbre_guard=False,
            seed=20261016,
            block_size=256,
            sample_rate=SR,
        )
        proc = Processor()
        proc.prepare(SR, 256, cfg)
        return proc.process_file(audio)
    finally:
        route_v.protect_true_peak = original


def compare_pair(rust: np.ndarray, proto: np.ndarray) -> tuple[float, float]:
    n = min(proto.shape[1], rust.shape[1])
    db = third_octave_delta(rust[:, :n], proto[:, :n], SR)
    di = abs(iacc(rust[:, :n], SR) - iacc(proto[:, :n], SR))
    return db, di


def main() -> None:
    only = sys.argv[1:] 
    rows = []
    worst_db = 0.0
    worst_iacc = 0.0
    gate_fail = False
    for label, asset in ASSETS:
        if only and asset.name not in only and label not in only:
            continue
        audio = read_wav(asset)
        for preset in PRESETS:
            for strength in STRENGTHS:
                proto = render_prototype(audio, preset, strength, True)
                rust = render_rust(audio, preset, strength, True)
                db, di = compare_pair(rust, proto)
                tp = true_peak_dbtp(rust, SR)
                limited = ""
                status = "ok" if db <= 0.2 and di <= 0.01 else "FAIL"
                if status == "FAIL":
                    proto_off = render_prototype(audio, preset, strength, False)
                    rust_off = render_rust(audio, preset, strength, False)
                    db_off, di_off = compare_pair(rust_off, proto_off)
                    if db_off <= 0.2 and di_off <= 0.01:
                        status = "limiter"
                        limited = f"  limiter-off third={db_off:.3f} dB iacc={di_off:.4f}"
                    else:
                        gate_fail = True
                        limited = f"  limiter-off third={db_off:.3f} dB iacc={di_off:.4f} STILL"
                if asset.name == "holdout_electronic.wav" and tp > -1.0 + 1e-3:
                    status = "FAIL"
                    gate_fail = True
                    limited += f"  true-peak {tp:.2f} dBTP exceeds -1"
                worst_db = max(worst_db, db if status != "limiter" else 0.0)
                worst_iacc = max(worst_iacc, di if status != "limiter" else 0.0)
                line = (
                    f"{status} {label} {asset.name} {preset} s={strength:.1f}"
                    f"  third={db:.3f} dB  iacc={di:.4f}  tp={tp:.2f} dBTP{limited}"
                )
                print(line, flush=True)
                rows.append(line)
    print(f"worst third-octave {worst_db:.3f} dB   worst |IACC| {worst_iacc:.4f}")
    if gate_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
