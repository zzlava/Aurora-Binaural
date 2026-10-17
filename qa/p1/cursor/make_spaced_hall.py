#!/usr/bin/env python3
"""Synthetic spaced-omni hall stem for the low-band regression.

Two independent noises in 30–140 Hz (ρ near 0) plus one shared
noise from 300 Hz up. 48 kHz, 24-bit, 8 s. Safe to commit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

SR = 48_000
SECONDS = 8.0
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/assets/synth_spaced_hall.wav"


def band(noise: np.ndarray, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(noise.size, d=1.0 / SR)
    spec[(freqs < lo) | (freqs >= hi)] = 0.0
    return np.fft.irfft(spec, n=noise.size)


def rho(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-30))


def write_24(path: Path, audio: np.ndarray) -> None:
    clipped = np.clip(audio, -1.0, 1.0)
    ints = np.round(clipped * 8_388_607.0).astype(np.int32)
    interleaved = np.ascontiguousarray(ints.T.reshape(-1))
    packed = interleaved.view(np.uint8).reshape(-1, 4)[:, :3].copy()
    path.parent.mkdir(parents=True, exist_ok=True)
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(3)
        handle.setframerate(SR)
        handle.writeframes(packed.tobytes())


def main() -> None:
    n = int(SR * SECONDS)
    rng = np.random.default_rng(20261017)
    low_l = band(rng.standard_normal(n), 30.0, 140.0)
    low_r = band(rng.standard_normal(n), 30.0, 140.0)
    shared = band(rng.standard_normal(n), 300.0, 12_000.0)
    low_l /= np.sqrt(np.mean(low_l**2)) + 1e-12
    low_r /= np.sqrt(np.mean(low_r**2)) + 1e-12
    shared /= np.sqrt(np.mean(shared**2)) + 1e-12
    left = 0.12 * low_l + 0.08 * shared
    right = 0.12 * low_r + 0.08 * shared
    peak = np.max(np.abs(np.stack([left, right])))
    audio = np.stack([left, right]) * (10 ** (-1.0 / 20.0) / peak)
    write_24(OUT, audio)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"wrote {OUT}")
    print(f"low rho {rho(low_l, low_r):+.3f}  full rho {rho(audio[0], audio[1]):+.3f}")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
