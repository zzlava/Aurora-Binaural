#!/usr/bin/env python3
"""Tune and check P1c headroom and low-frequency balance."""

from __future__ import annotations

import sys
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
BANDS = [31.5, 40, 50, 63, 80, 100, 125, 160, 1000]


def read_wav(path: Path, seconds: float) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if rate != SR:
        raise SystemExit(f"{path} is {rate}")
    if width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        signed = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        signed = np.where(signed >= 1 << 23, signed - (1 << 24), signed)
        data = signed.astype(np.float64) / float(1 << 23)
    elif width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    else:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float64) / 2147483648.0
    data = data.reshape(-1, channels).T
    if channels == 1:
        data = np.repeat(data, 2, axis=0)
    return data[:2, : int(seconds * SR)]


def render(audio: np.ndarray, *, limiter: bool, strength: float = 1.0) -> tuple[np.ndarray, object]:
    renderer = Renderer(
        str(PACK), str(SOFA), str(PRESET),
        strength=strength, seed=20261016, preset="V3",
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
    return np.concatenate(pieces, axis=0).T, renderer


def band_gain(dry: np.ndarray, wet: np.ndarray, latency: int) -> dict[float, float]:
    wet_al = wet[:, latency:]
    dry_al = dry[:, : wet_al.shape[1]]
    n = min(dry_al.shape[1], wet_al.shape[1])
    dry_al, wet_al = dry_al[:, :n], wet_al[:, :n]
    window = np.hanning(n)

    def levels(x: np.ndarray) -> dict[float, float]:
        spec = np.zeros(n // 2 + 1)
        for ch in range(x.shape[0]):
            spec += np.abs(np.fft.rfft(x[ch] * window)) ** 2
        freqs = np.fft.rfftfreq(n, d=1.0 / SR)
        out = {}
        for f in BANDS:
            lo, hi = f / 2 ** (1 / 6), f * 2 ** (1 / 6)
            mask = (freqs >= lo) & (freqs < hi)
            out[f] = 10 * np.log10(np.mean(spec[mask]) + 1e-30)
        return out

    gd, gw = levels(dry_al), levels(wet_al)
    return {f: gw[f] - gd[f] for f in BANDS}


def pink(n: int) -> np.ndarray:
    rng = np.random.default_rng(3)
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    spec[1:] /= np.sqrt(freqs[1:])
    mono = np.fft.irfft(spec, n=n)
    mono /= np.max(np.abs(mono)) + 1e-12
    mono *= 10 ** (-1 / 20)
    return np.stack([mono, mono])


def pop_master(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    rng = np.random.default_rng(11)
    bass = 0.55 * np.sin(2 * np.pi * 55 * t) + 0.25 * np.sin(2 * np.pi * 110 * t)
    bass += 0.35 * np.sin(2 * np.pi * (41.2 + 10 * np.sin(2 * np.pi * 2 * t)) * t)
    kick_env = np.zeros(n)
    for start in range(0, n, int(0.5 * SR)):
        m = min(int(0.08 * SR), n - start)
        kick_env[start : start + m] += np.exp(-np.arange(m) / (0.018 * SR))
    kick = kick_env * np.sin(2 * np.pi * 48 * t)
    mid = 0.22 * np.sin(2 * np.pi * 440 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    mid += 0.12 * np.sin(2 * np.pi * 880 * t)
    noise = rng.standard_normal(n)
    hat = np.diff(noise, prepend=0.0)
    hat_env = np.zeros(n)
    for start in range(int(0.25 * SR), n, int(0.5 * SR)):
        m = min(int(0.04 * SR), n - start)
        hat_env[start : start + m] += np.exp(-np.arange(m) / (0.008 * SR))
    hat *= hat_env
    hat /= np.max(np.abs(hat)) + 1e-12
    mono = bass + 0.9 * kick + mid + 0.18 * hat
    mono = np.tanh(1.8 * mono / (np.sqrt(np.mean(mono * mono)) * 4 + 1e-9))
    mono /= np.max(np.abs(mono)) + 1e-12
    mono *= 10 ** (-0.1 / 20)
    side = 0.08 * rng.standard_normal(n)
    return np.stack([np.clip(mono + side, -1, 1), np.clip(mono - side, -1, 1)])


def lf_corr(wet: np.ndarray, latency: int) -> float:
    x = wet[:, latency:]
    # 4th-order butterworth-ish via repeated one-pole at 150 Hz is enough.
    # Use FFT lowpass at 160 Hz.
    n = x.shape[1]
    spec_l = np.fft.rfft(x[0])
    spec_r = np.fft.rfft(x[1])
    freqs = np.fft.rfftfreq(n, d=1.0 / SR)
    spec_l[freqs > 160] = 0
    spec_r[freqs > 160] = 0
    l = np.fft.irfft(spec_l, n=n)
    r = np.fft.irfft(spec_r, n=n)
    l -= l.mean()
    r -= r.mean()
    return float(np.dot(l, r) / (np.linalg.norm(l) * np.linalg.norm(r) + 1e-20))


def show_bands(name: str, gains: dict[float, float]) -> None:
    ref = gains[1000.0]
    worst = 0.0
    parts = []
    for f in BANDS:
        if f == 1000:
            continue
        delta = gains[f] - ref
        worst = max(worst, abs(delta))
        parts.append(f"{f:.0f}:{delta:+.2f}")
    print(f"  {name} vs1k " + " ".join(parts) + f"  |max| {worst:.2f}")


def set_lf(hz: float, db: float, peak_hz: float, peak_db: float, peak_q: float) -> None:
    text = PRESET.read_text()
    repl = {
        "lf_shelf_hz": f"{hz}",
        "lf_shelf_db": f"{db}",
        "lf_peak_hz": f"{peak_hz}",
        "lf_peak_db": f"{peak_db}",
        "lf_peak_q": f"{peak_q}",
    }
    lines = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if key in repl:
            lines.append(f"{key} = {repl[key]}")
        else:
            lines.append(line)
    PRESET.write_text("\n".join(lines) + "\n")


def score(gains: dict[float, float]) -> float:
    ref = gains[1000.0]
    return max(abs(gains[f] - ref) for f in BANDS if f != 1000)


def deviations(gains: dict[float, float]) -> str:
    ref = gains[1000.0]
    return " ".join(f"{f:.0f}:{gains[f] - ref:+.2f}" for f in BANDS if f != 1000)


def main() -> None:
    if "--sweep" not in sys.argv:
        run_once()
        return
    hold = read_wav(ROOT / "data/assets/holdout_electronic.wav", 8.0)
    noise = pink(SR * 6)
    original = PRESET.read_text()
    best = []
    try:
        for hz in (80, 110, 140):
            for db in (1.6, 2.4, 3.2):
                for peak_db in (0.0, -1.5, -2.5):
                    for peak_hz in (130, 170):
                        set_lf(hz, db, peak_hz, peak_db, 0.9)
                        wet_h, ren_h = render(hold, limiter=False)
                        wet_p, ren_p = render(noise, limiter=False)
                        gh = band_gain(hold, wet_h, int(ren_h.latency_samples()))
                        gp = band_gain(noise, wet_p, int(ren_p.latency_samples()))
                        sh, sp = score(gh), score(gp)
                        row = (max(sh, sp), sh, sp, hz, db, peak_hz, peak_db)
                        best.append(row)
                        if max(sh, sp) < 1.15:
                            print(
                                f"hit shelf {hz} {db} peak {peak_hz} {peak_db} "
                                f"hold {sh:.2f} pink {sp:.2f}"
                            )
                            print("  H", deviations(gh))
                            print("  P", deviations(gp))
    finally:
        PRESET.write_text(original)
    best.sort()
    print("\nbest:")
    for row in best[:8]:
        print(
            f"  max {row[0]:.2f} hold {row[1]:.2f} pink {row[2]:.2f} "
            f"shelf {row[3]} {row[4]} peak {row[5]} {row[6]}"
        )


def run_once() -> None:
    hold = read_wav(ROOT / "data/assets/holdout_electronic.wav", 12.0)
    noise = pink(SR * 8)
    pop = pop_master(SR * 12)

    for label, audio, limiter in (
        ("hold off", hold, False),
        ("pink off", noise, False),
        ("hold on", hold, True),
        ("pop on", pop, True),
    ):
        wet, renderer = render(audio, limiter=limiter)
        lat = int(renderer.latency_samples())
        print(f"\n{label} headroom {renderer.headroom_db():.2f} dB latency {lat}")
        if not limiter:
            show_bands(label, band_gain(audio, wet, lat))
            print(f"  lf corr {lf_corr(wet, lat):.4f}")
        else:
            stats = renderer.limiter_stats()
            print(
                f"  GR max {stats['max_gr_db']:.2f}  p95 {stats['p95_gr_db']:.2f}  "
                f"p50 {stats['p50_gr_db']:.2f}  n {stats['samples']}"
            )


if __name__ == "__main__":
    main()
