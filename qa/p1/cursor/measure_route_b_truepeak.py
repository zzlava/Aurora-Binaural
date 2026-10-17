#!/usr/bin/env python3
"""Route B true peak after the detector-ceiling fix.

Measures `holdout_electronic` (room, strength 0.4 and 1.0, SADIE, seed
20261016) and a 0 dBFS log sine sweep. The meter is the prototype's 4×
`scipy.signal.resample_poly` true peak.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from aurora_core import Renderer  # noqa: E402
from prototypes.aurora_p0.loudness import true_peak_dbtp  # noqa: E402

SR = 48_000
PACK = ROOT / "data/hrtf/sadie_d1_frontal.ahrp"
SOFA = ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa"
PRESET = ROOT / "presets/p0_draft.toml"
HOLD = ROOT / "data/assets/holdout_electronic.wav"


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
        signed = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        signed = np.where(signed >= 1 << 23, signed - (1 << 24), signed)
        data = signed.astype(np.float64) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise SystemExit(f"unsupported wav width {width}")
    data = data.reshape(-1, channels).T
    if channels == 1:
        data = np.repeat(data, 2, axis=0)
    return np.ascontiguousarray(data[:2])


def log_sweep(seconds: float = 5.0) -> np.ndarray:
    """0 dBFS log sweep, 20 Hz–20 kHz, both channels in phase."""
    n = int(SR * seconds)
    t = np.arange(n, dtype=np.float64) / SR
    f0, f1 = 20.0, 20_000.0
    phase = 2.0 * np.pi * f0 * seconds / np.log(f1 / f0) * ((f1 / f0) ** (t / seconds) - 1.0)
    sweep = np.sin(phase)
    # Full-scale at the samples. A short fade keeps the limiter from seeing a click.
    fade = int(0.005 * SR)
    window = np.ones(n, dtype=np.float64)
    ramp = np.linspace(0.0, 1.0, fade)
    window[:fade] = ramp
    window[-fade:] = ramp[::-1]
    sweep = sweep * window
    return np.vstack([sweep, sweep])


def render(audio: np.ndarray, strength: float) -> np.ndarray:
    renderer = Renderer(
        str(PACK),
        str(SOFA),
        str(PRESET),
        strength=strength,
        seed=20261016,
        preset="room",
        limiter=True,
        route="B",
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


def main() -> None:
    hold = read_wav(HOLD)
    for strength in (0.4, 1.0):
        out = render(hold, strength)
        print(f"holdout_electronic strength {strength:.1f}  {true_peak_dbtp(out, SR):.3f} dBTP", flush=True)
    sweep = log_sweep()
    for strength in (0.4, 1.0):
        out = render(sweep, strength)
        print(f"sine sweep 0 dBFS strength {strength:.1f}  {true_peak_dbtp(out, SR):.3f} dBTP", flush=True)


if __name__ == "__main__":
    main()
