#!/usr/bin/env python3
"""Third-octave deltas for P1d, relative to 1 kHz.

Copyrighted local files (carmen, paomo) are read in place and never written
back into the tree. Numbers only.
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
# Copyrighted listening files live in the main checkout, not this worktree.
MAIN = ROOT.parent.parent if (ROOT.parent.parent / "paomo.flac").exists() else ROOT
sys.path.insert(0, str(ROOT))

from aurora_core import Renderer  # noqa: E402

PACK = ROOT / "data/hrtf/sadie_d1_frontal.ahrp"
SOFA = ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa"
PRESET = ROOT / "presets/route_v.toml"
SR = 48_000
BLOCK = 256
BANDS = [
    25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500,
    630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000,
    10000, 12500, 16000,
]
LF = [31.5, 40, 50, 63, 80, 100, 125, 160]
BUMP = [500, 630, 800, 1000]


def read_audio(path: Path, seconds: float | None) -> np.ndarray:
    if path.suffix.lower() == ".wav":
        return read_wav(path, seconds)
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    if seconds is not None:
        cmd[3:3] = ["-t", str(seconds)]
    raw = subprocess.check_output(cmd)
    data = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    if data.size % 2:
        data = data[:-1]
    return data.reshape(-1, 2).T


def read_wav(path: Path, seconds: float | None) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if rate != SR:
        raise SystemExit(f"{path} is {rate} Hz")
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
        raise SystemExit(f"width {width}")
    data = data.reshape(-1, channels).T
    if channels == 1:
        data = np.repeat(data, 2, axis=0)
    if seconds is not None:
        data = data[:, : int(seconds * SR)]
    return np.ascontiguousarray(data[:2])


def render(
    audio: np.ndarray,
    *,
    limiter: bool = False,
    strength: float = 1.0,
    preset: str = "V3",
    preset_path: Path = PRESET,
) -> tuple[np.ndarray, int]:
    renderer = Renderer(
        str(PACK),
        str(SOFA),
        str(preset_path),
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

    renderer.prepare(SR, BLOCK, Config())
    renderer.set_controls(
        {
            "strength": strength,
            "width": 0.5,
            "height": 0.75,
            "room": 0.5,
            "voice_distance": 0.25,
        }
    )
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    pieces = []
    for start in range(0, interleaved.shape[0], BLOCK):
        pieces.append(np.asarray(renderer.process(interleaved[start : start + BLOCK]), dtype=np.float64))
    out = np.concatenate(pieces, axis=0).T
    return out, int(renderer.latency_samples())


def align(dry: np.ndarray, wet: np.ndarray, latency: int) -> tuple[np.ndarray, np.ndarray]:
    if latency > 0 and wet.shape[1] > latency:
        wet_al = wet[:, latency:]
        dry_al = dry[:, : wet_al.shape[1]]
    else:
        n = min(dry.shape[1], wet.shape[1])
        wet_al, dry_al = wet[:, :n], dry[:, :n]
    n = min(dry_al.shape[1], wet_al.shape[1])
    return dry_al[:, :n], wet_al[:, :n]


def band_db(x: np.ndarray) -> dict[float, float]:
    n = x.shape[1]
    window = np.hanning(n)
    spec = np.zeros(n // 2 + 1, dtype=np.float64)
    for ch in range(x.shape[0]):
        spec += np.abs(np.fft.rfft(x[ch] * window)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    out = {}
    for f in BANDS:
        lo, hi = f / 2.0 ** (1.0 / 6.0), f * 2.0 ** (1.0 / 6.0)
        mask = (freqs >= lo) & (freqs < hi)
        out[float(f)] = 10.0 * np.log10(np.mean(spec[mask]) + 1e-30)
    return out


def relative(dry: np.ndarray, wet: np.ndarray, latency: int) -> dict[float, float]:
    dry_al, wet_al = align(dry, wet, latency)
    gd, gw = band_db(dry_al), band_db(wet_al)
    delta = {f: gw[f] - gd[f] for f in gd}
    ref = delta[1000.0]
    return {f: delta[f] - ref for f in delta}


def low_correlation(x: np.ndarray, latency: int = 0) -> float:
    y = x[:, latency:]
    n = y.shape[1]
    spec_l = np.fft.rfft(y[0] * np.hanning(n))
    spec_r = np.fft.rfft(y[1] * np.hanning(n))
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    mask = (freqs >= 20.0) & (freqs < 160.0)
    sl, sr = spec_l[mask], spec_r[mask]
    num = np.real(np.sum(sl * np.conj(sr)))
    den = np.sqrt(np.sum(np.abs(sl) ** 2) * np.sum(np.abs(sr) ** 2)) + 1e-30
    return float(num / den)


def pink(n: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    spec[1:] /= np.sqrt(freqs[1:])
    mono = np.fft.irfft(spec, n=n)
    mono /= np.max(np.abs(mono)) + 1e-12
    mono *= 10 ** (-1.0 / 20.0)
    return np.stack([mono, mono])


def summarize(name: str, rel: dict[float, float]) -> None:
    lf = max(abs(rel[f]) for f in LF)
    bump = max(rel[f] for f in (630.0, 800.0))
    print(f"\n== {name} ==")
    print(f"  LF max |dB| vs 1 kHz: {lf:.2f}    630/800 peak: {bump:+.2f}")
    for f in BANDS:
        mark = ""
        if f in LF or f in (500, 630, 800):
            mark = " *"
        print(f"  {f:8.1f}  {rel[float(f)]:+6.2f}{mark}")


def load_named(name: str, seconds: float) -> np.ndarray:
    if name == "carmen":
        return read_audio(MAIN / "qa/listen/carmen/carmen48.flac", seconds)
    if name == "paomo":
        return read_audio(MAIN / "paomo.flac", seconds)
    if name == "holdout":
        return read_wav(ROOT / "data/assets/holdout_electronic.wav", seconds)
    if name == "pink":
        return pink(int(seconds * SR))
    if name == "hall":
        return read_wav(ROOT / "data/assets/synth_spaced_hall.wav", seconds)
    if name == "pink_asset":
        return read_wav(ROOT / "data/assets/synth_pink_stereo.wav", seconds)
    path = Path(name)
    return read_audio(path if path.is_absolute() else ROOT / path, seconds)


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    names = sys.argv[2:] or ["carmen", "paomo", "holdout", "pink"]
    for name in names:
        dry = load_named(name, seconds)
        wet, latency = render(dry, limiter=False)
        rel = relative(dry, wet, latency)
        summarize(f"{name} {dry.shape[1] / SR:.1f}s lat {latency}", rel)
        if name == "pink":
            print(f"  mono LF correlation {low_correlation(wet, latency):.4f}")


if __name__ == "__main__":
    main()
