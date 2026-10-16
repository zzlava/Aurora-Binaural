#!/usr/bin/env python3
"""Compare the Rust route B core with the P0 prototype on the three real assets.

Gates from the task: 1/3-octave magnitude difference <= 0.5 dB, broadband
IACC difference <= 0.02. The prototype renders the whole file at once; the
Rust core is called in blocks of 256. A constant extra delay (partitioned
convolution and the limiter lookahead) does not move either metric.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from aurora_core import Renderer  # noqa: E402
from prototypes.aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from prototypes.aurora_p0.metrics import iacc  # noqa: E402

ASSETS = [
    ROOT / "data/assets/real_voice.wav",
    ROOT / "data/assets/real_music.wav",
    ROOT / "data/assets/real_ambience.wav",
]
HRTF = {
    "ku100": (
        ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa",
        ROOT / "data/hrtf/ku100_frontal.ahrp",
    ),
    "sadie": (
        ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa",
        ROOT / "data/hrtf/sadie_d1_frontal.ahrp",
    ),
}
PRESET = ROOT / "presets/p0_draft.toml"
SR = 48_000


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


def render_rust(audio: np.ndarray, pack: Path, source: Path, strength: float) -> np.ndarray:
    renderer = Renderer(str(pack), str(source), str(PRESET), strength=strength, seed=20261016, preset="room")

    class Config:
        input_channels = 2
        output_channels = 2

    block = 256
    renderer.prepare(SR, block, Config())
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    pieces = []
    for start in range(0, interleaved.shape[0], block):
        pieces.append(np.asarray(renderer.process(interleaved[start : start + block]), dtype=np.float64))
    out = np.concatenate(pieces, axis=0).T
    return out


def render_prototype(audio: np.ndarray, sofa: Path, name: str, strength: float) -> np.ndarray:
    cfg = RenderConfig(
        route="B",
        strength=strength,
        preset="room",
        hrtf_path=sofa,
        hrtf_name=name,
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


def main() -> None:
    rows = []
    worst_db = 0.0
    worst_iacc = 0.0
    for asset in ASSETS:
        audio = read_wav(asset)
        for name, (sofa, pack) in HRTF.items():
            for strength in (0.4, 0.8):
                proto = render_prototype(audio, sofa, name, strength)
                rust = render_rust(audio, pack, sofa, strength)
                n = min(proto.shape[1], rust.shape[1])
                db = third_octave_delta(rust[:, :n], proto[:, :n], SR)
                di = abs(iacc(rust[:, :n], SR) - iacc(proto[:, :n], SR))
                worst_db = max(worst_db, db)
                worst_iacc = max(worst_iacc, di)
                status = "ok" if db <= 0.5 and di <= 0.02 else "FAIL"
                line = f"{status} {asset.name} {name} s={strength:.1f}  third={db:.3f} dB  iacc={di:.4f}"
                print(line, flush=True)
                rows.append(line)
    print(f"worst third-octave {worst_db:.3f} dB   worst |IACC| {worst_iacc:.4f}")
    if worst_db > 0.5 or worst_iacc > 0.02:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
