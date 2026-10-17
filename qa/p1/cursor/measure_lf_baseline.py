#!/usr/bin/env python3
"""Baseline (pre-P1c) route V3 measurements: peak growth, band gains, limiter GR."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
from aurora_core import Renderer  # noqa: E402

PACK = ROOT / "data/hrtf/sadie_d1_frontal.ahrp"
SOFA = ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa"
PRESET = ROOT / "presets/route_v.toml"
SR = 48_000
BLOCK = 256
OUT = ROOT / "qa/p1/cursor/lf"
BANDS = [31.5, 40, 50, 63, 80, 100, 125, 160, 250, 500, 1000, 2000, 4000]


def read_wav(path: Path, seconds: float | None = None) -> np.ndarray:
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
    return data[:2]


def write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(audio, -1.0, 1.0)
    interleaved = np.ascontiguousarray(pcm.T, dtype=np.float32)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(4)
        handle.setframerate(SR)
        handle.writeframes(interleaved.tobytes())


def render(audio: np.ndarray, *, limiter: bool, strength: float = 1.0, preset: str = "V3") -> tuple[np.ndarray, int]:
    renderer = Renderer(
        str(PACK), str(SOFA), str(PRESET),
        strength=strength, seed=20261016, preset=preset,
        limiter=limiter, route="V", hrtf_name="sadie",
    )

    class Config:
        input_channels = 2
        output_channels = 2

    renderer.prepare(SR, BLOCK, Config())
    renderer.set_controls({"strength": strength, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.25})
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    pieces = []
    for start in range(0, interleaved.shape[0], BLOCK):
        pieces.append(np.asarray(renderer.process(interleaved[start : start + BLOCK]), dtype=np.float64))
    out = np.concatenate(pieces, axis=0).T
    return out, int(renderer.latency_samples())


def band_db(x: np.ndarray, sr: int) -> dict[float, float]:
    n = x.shape[1]
    window = np.hanning(n)
    spec = np.zeros(n // 2 + 1, dtype=np.float64)
    for ch in range(x.shape[0]):
        spec += np.abs(np.fft.rfft(x[ch] * window)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    out = {}
    for f in BANDS:
        lo, hi = f / 2.0 ** (1.0 / 6.0), f * 2.0 ** (1.0 / 6.0)
        mask = (freqs >= lo) & (freqs < hi)
        out[f] = 10.0 * np.log10(np.mean(spec[mask]) + 1e-30)
    return out


def peak_db(x: np.ndarray) -> float:
    return 20.0 * np.log10(np.max(np.abs(x)) + 1e-30)


def rms_db(x: np.ndarray) -> float:
    return 10.0 * np.log10(np.mean(x * x) + 1e-30)


def summarize(name: str, dry: np.ndarray, wet: np.ndarray, latency: int) -> None:
    # Drop the latency so the compared region is the same musical content.
    if latency > 0 and wet.shape[1] > latency:
        wet_al = wet[:, latency:]
        dry_al = dry[:, : wet_al.shape[1]]
    else:
        n = min(dry.shape[1], wet.shape[1])
        wet_al, dry_al = wet[:, :n], dry[:, :n]
    n = min(dry_al.shape[1], wet_al.shape[1])
    dry_al, wet_al = dry_al[:, :n], wet_al[:, :n]
    gd, gw = band_db(dry_al, SR), band_db(wet_al, SR)
    print(f"\n== {name} ==")
    print(f"peak in {peak_db(dry):.2f} out {peak_db(wet):.2f}  delta {peak_db(wet) - peak_db(dry):.2f}")
    print(f"rms  in {rms_db(dry_al):.2f} out {rms_db(wet_al):.2f}  delta {rms_db(wet_al) - rms_db(dry_al):.2f}")
    ref = gw[1000.0] - gd[1000.0]
    for f in BANDS:
        gain = gw[f] - gd[f]
        print(f"  {f:7.1f} Hz  gain {gain:+6.2f}  vs 1k {gain - ref:+6.2f}")


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    spec[1:] /= np.sqrt(freqs[1:])
    mono = np.fft.irfft(spec, n=n)
    mono /= np.max(np.abs(mono)) + 1e-12
    mono *= 10 ** (-1.0 / 20.0)  # -1 dBFS peak
    return np.stack([mono, mono])


def pop_master(n: int) -> np.ndarray:
    """Dense -ish pop: bass + kick + mid chords, then clip toward 0 dBFS."""
    t = np.arange(n) / SR
    rng = np.random.default_rng(11)
    bass = 0.55 * np.sin(2 * np.pi * 55 * t) + 0.25 * np.sin(2 * np.pi * 110 * t)
    # pitch glide every 0.5 s
    bass += 0.35 * np.sin(2 * np.pi * (41.2 + 10 * np.sin(2 * np.pi * 2 * t)) * t)
    kick_env = np.zeros(n)
    for start in range(0, n, int(0.5 * SR)):
        m = min(int(0.08 * SR), n - start)
        env = np.exp(-np.arange(m) / (0.018 * SR))
        kick_env[start : start + m] += env
    kick = kick_env * np.sin(2 * np.pi * 48 * t)
    mid = 0.22 * np.sin(2 * np.pi * 440 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    mid += 0.12 * np.sin(2 * np.pi * 880 * t)
    noise = rng.standard_normal(n)
    # crude highpass via diff
    hat = np.diff(noise, prepend=0.0)
    hat_env = np.zeros(n)
    for start in range(int(0.25 * SR), n, int(0.5 * SR)):
        m = min(int(0.04 * SR), n - start)
        hat_env[start : start + m] += np.exp(-np.arange(m) / (0.008 * SR))
    hat *= hat_env
    hat /= np.max(np.abs(hat)) + 1e-12
    mono = bass + 0.9 * kick + mid + 0.18 * hat
    # soft clip then peak normalize to -0.1 dBFS
    mono = np.tanh(1.8 * mono / (np.sqrt(np.mean(mono * mono)) * 4 + 1e-9))
    mono /= np.max(np.abs(mono)) + 1e-12
    mono *= 10 ** (-0.1 / 20.0)
    side = 0.08 * rng.standard_normal(n)
    left = np.clip(mono + side, -1, 1)
    right = np.clip(mono - side, -1, 1)
    return np.stack([left, right])


def limiter_gr(off: np.ndarray, on: np.ndarray, shift: int) -> None:
    """Envelope ratio of limiter-off vs limiter-on, aligned by lookahead."""
    if shift < 0:
        shift = 0
    a = off[:, : off.shape[1] - shift] if shift else off
    b = on[:, shift : shift + a.shape[1]]
    n = min(a.shape[1], b.shape[1])
    a, b = a[:, :n], b[:, :n]
    # 5 ms RMS envelopes
    win = int(0.005 * SR)
    kernel = np.ones(win) / win
    ea = np.sqrt(np.convolve(np.mean(a * a, axis=0), kernel, mode="valid"))
    eb = np.sqrt(np.convolve(np.mean(b * b, axis=0), kernel, mode="valid"))
    gr = 20.0 * np.log10((ea + 1e-12) / (eb + 1e-12))
    gr = np.clip(gr, 0.0, None)
    print(
        f"  GR env max {np.max(gr):.2f} dB  p95 {np.quantile(gr, 0.95):.2f}  "
        f"p50 {np.quantile(gr, 0.50):.2f}  mean {np.mean(gr):.2f}"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hold = read_wav(ROOT / "data/assets/holdout_electronic.wav", 20.0)
    real = read_wav(ROOT / "data/assets/real_music.wav", 20.0)
    write_wav(OUT / "holdout_electronic_dry.wav", hold)
    write_wav(OUT / "real_music_dry.wav", real)

    for name, audio in (("holdout_electronic", hold), ("real_music", real)):
        off, lat_off = render(audio, limiter=False)
        on, lat_on = render(audio, limiter=True)
        print(f"latency off {lat_off} on {lat_on}")
        summarize(f"{name} limiter off", audio, off, lat_off)
        summarize(f"{name} limiter on", audio, on, lat_on)
        limiter_gr(off, on, lat_on - lat_off)
        if name == "holdout_electronic":
            write_wav(OUT / "holdout_electronic_v3_before.wav", on)
        else:
            write_wav(OUT / "real_music_v3_before.wav", on)

    rng = np.random.default_rng(3)
    noise = pink(SR * 8, rng)
    off, lat = render(noise, limiter=False)
    summarize("pink limiter off", noise, off, lat)

    pop = pop_master(SR * 12)
    print(f"\npop peak {peak_db(pop):.2f} rms {rms_db(pop):.2f}")
    off, lat_off = render(pop, limiter=False)
    on, lat_on = render(pop, limiter=True)
    summarize("pop limiter off", pop, off, lat_off)
    summarize("pop limiter on", pop, on, lat_on)
    limiter_gr(off, on, lat_on - lat_off)


if __name__ == "__main__":
    main()
