#!/usr/bin/env python3
"""Loudness-match P1c listening clips and draw the third-octave curves.

WAVs under qa/p1/cursor/lf/ are gitignored. The PNG next to the report is not.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import qa.p1.cursor.tune_p1c as t  # noqa: E402
from aurora_binaural.media import measure_loudness_file

OUT = t.ROOT / "qa/p1/cursor/lf"
FIG = t.ROOT / "qa/p1/cursor/p1c-lf-bands.png"
BANDS = [31.5, 40, 50, 63, 80, 100, 125, 160]


def write_s16(path: Path, audio: np.ndarray) -> None:
    pcm = np.clip(audio, -1.0, 1.0).T
    frames = (pcm * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(t.SR)
        handle.writeframes(frames)


def lufs_of(audio: np.ndarray, path: Path) -> float:
    write_s16(path, audio)
    return measure_loudness_file(path).integrated_lufs


def match(audio: np.ndarray, target_lufs: float, path: Path) -> float:
    current = lufs_of(audio, path)
    gain_db = target_lufs - current
    scaled = audio * (10 ** (gain_db / 20.0))
    write_s16(path, scaled)
    matched = measure_loudness_file(path).integrated_lufs
    print(f"  {path.name}: {current:.2f} -> {matched:.2f} LUFS ({gain_db:+.2f} dB)")
    return matched


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hold = t.read_wav(t.ROOT / "data/assets/holdout_electronic.wav", 20.0)
    real = t.read_wav(t.ROOT / "data/assets/real_music.wav", 20.0)
    before = {
        "holdout_electronic": t.read_wav(OUT / "holdout_electronic_v3_before.wav", 20.0),
        "real_music": t.read_wav(OUT / "real_music_v3_before.wav", 20.0),
    }
    new = {}
    for key, audio in (("holdout_electronic", hold), ("real_music", real)):
        wet, _ren = t.render(audio, limiter=True)
        lat = int(_ren.latency_samples())
        # Drop the fixed latency so the clip lines up with the dry file.
        aligned = np.zeros_like(audio)
        body = wet[:, lat:]
        n = min(aligned.shape[1], body.shape[1])
        aligned[:, :n] = body[:, :n]
        new[key] = aligned

    for key, dry in (("holdout_electronic", hold), ("real_music", real)):
        dry_path = OUT / f"{key}_dry.wav"
        levels = {
            "dry": lufs_of(dry, dry_path),
            "before": lufs_of(before[key], OUT / f"{key}_v3_before.wav"),
            "new": lufs_of(new[key], OUT / f"{key}_v3_new.wav"),
        }
        # The new render sits under the headroom pad, so it is the quietest.
        # Matching the louder two down to it keeps the peaks legal.
        target = min(levels.values())
        print(key, {k: round(v, 2) for k, v in levels.items()}, "target", round(target, 2))
        matched = {
            "dry": dry * 10 ** ((target - levels["dry"]) / 20.0),
            "before": before[key] * 10 ** ((target - levels["before"]) / 20.0),
            "new": new[key] * 10 ** ((target - levels["new"]) / 20.0),
        }
        # Same extra gain on all three, up to -1 dBFS on the hottest peak.
        hottest = max(float(np.max(np.abs(clip))) for clip in matched.values())
        makeup = (10 ** (-1.0 / 20.0)) / max(hottest, 1e-12)
        for kind, clip in matched.items():
            path = dry_path if kind == "dry" else OUT / f"{key}_v3_{kind}.wav"
            write_s16(path, clip * makeup)
            print(
                f"  {path.name}: {measure_loudness_file(path).integrated_lufs:.2f} LUFS, "
                f"peak {20 * np.log10(np.max(np.abs(clip * makeup)) + 1e-12):.2f} dBFS"
            )

    # Curves use the un-matched renders (the processing gain, not the audition gain).
    pink = t.pink(t.SR * 16)
    jobs = []
    for label, dry in (("holdout", hold), ("pink", pink)):
        wet, ren = t.render(dry, limiter=False)
        jobs.append((label, t.band_gain(dry, wet, int(ren.latency_samples()))))
    # before holdout, limiter on, from the saved float render
    jobs.append(("holdout before, limiter on", t.band_gain(hold, before["holdout_electronic"], 1229)))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.2, 4.4), dpi=140)
    colors = {
        "holdout": "#1f4b99",
        "pink": "#c45c26",
        "holdout before, limiter on": "#8aa0c8",
    }
    for label, gains in jobs:
        ref = gains[1000.0]
        ys = [gains[f] - ref for f in BANDS]
        ax.plot(range(len(BANDS)), ys, marker="o", label=label, color=colors[label])
    ax.axhline(1.0, color="#999", lw=0.8, ls="--")
    ax.axhline(-1.0, color="#999", lw=0.8, ls="--")
    ax.axhline(0.0, color="#ccc", lw=0.6)
    ax.set_xticks(range(len(BANDS)))
    ax.set_xticklabels([str(int(f)) if f >= 40 else "31.5" for f in BANDS])
    ax.set_xlabel("1/3-octave center (Hz)")
    ax.set_ylabel("Gain relative to 1 kHz (dB)")
    ax.set_title("Route V3, strength 1, limiter off")
    ax.legend(frameon=False)
    ax.set_ylim(-5.5, 3.5)
    fig.tight_layout()
    fig.savefig(FIG)
    print("wrote", FIG)


if __name__ == "__main__":
    main()
