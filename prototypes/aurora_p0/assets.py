"""Generate synthetic test signals and fetch redistributable short assets."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from . import SAMPLE_RATE


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_wav(path: Path, stereo_or_mono: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.asarray(stereo_or_mono, dtype=np.float64)
    if x.ndim == 1:
        data = x
    else:
        data = x.T  # (N, ch)
    # peak normalize to -3 dBFS
    peak = np.max(np.abs(data)) + 1e-12
    data = data * (10 ** (-3 / 20) / peak)
    sf.write(str(path), data, sr, subtype="PCM_24")


def pink_noise(n: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    # Voss-McCartney-ish via frequency domain 1/f
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, d=1.0)
    freqs[0] = freqs[1]
    spec /= np.sqrt(freqs)
    pink = np.fft.irfft(spec, n=n)
    return pink.astype(np.float64)


def sweep(n: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(n) / sr
    f0, f1 = 40.0, 16000.0
    # logarithmic chirp
    k = n / sr / np.log(f1 / f0)
    phase = 2 * np.pi * f0 * k * (np.exp(t / k) - 1)
    return np.sin(phase) * np.hanning(n)


def impulse(n: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    x = np.zeros(n)
    x[int(0.1 * sr)] = 0.9
    return x


def voice_substitute(n: int, sr: int = SAMPLE_RATE, seed: int = 2) -> np.ndarray:
    """Formant-ish voiced + unvoiced mono substitute."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    f0 = 120.0 + 20.0 * np.sin(2 * np.pi * 2.5 * t)
    phase = np.cumsum(2 * np.pi * f0 / sr)
    glottal = np.sin(phase) + 0.3 * np.sin(2 * phase)
    # simple formant filters via resonances
    from scipy.signal import lfilter

    y = glottal
    for fc, bw in [(700, 80), (1200, 100), (2600, 120)]:
        r = np.exp(-np.pi * bw / sr)
        theta = 2 * np.pi * fc / sr
        b = [1.0]
        a = [1, -2 * r * np.cos(theta), r * r]
        y = lfilter(b, a, y)
    # amplitude syllables
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t)
    env *= (rng.random(n) > 0.02).astype(float)  # tiny gaps
    noise = 0.05 * rng.standard_normal(n)
    return (y * env + noise).astype(np.float64)


def music_substitute(n: int, sr: int = SAMPLE_RATE, seed: int = 3) -> np.ndarray:
    """Stereo synthetic music-like bed: chords + soft pulse."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    chords = [(220, 277, 330), (196, 247, 294), (247, 311, 370), (220, 277, 330)]
    seg = n // len(chords)
    mono = np.zeros(n)
    for i, freqs in enumerate(chords):
        sl = slice(i * seg, (i + 1) * seg if i < len(chords) - 1 else n)
        tt = t[sl]
        for f in freqs:
            mono[sl] += 0.2 * np.sin(2 * np.pi * f * tt)
        # soft kick
        kick_t = np.arange(sl.stop - sl.start) / sr
        kicks = np.zeros_like(kick_t)
        for kpos in np.arange(0, kick_t[-1], 0.5):
            idx = int(kpos * sr)
            if idx < len(kicks) - 200:
                env = np.exp(-np.arange(200) / (0.03 * sr))
                kicks[idx : idx + 200] += 0.6 * np.sin(2 * np.pi * 60 * np.arange(200) / sr) * env
        mono[sl] += kicks
    # stereo pan
    width = 0.3 * np.sin(2 * np.pi * 0.1 * t)
    left = mono * (0.7 + width)
    right = mono * (0.7 - width)
    left += 0.02 * pink_noise(n, seed=seed + 10)
    right += 0.02 * pink_noise(n, seed=seed + 11)
    return np.stack([left, right], axis=0)


def ambience_substitute(n: int, sr: int = SAMPLE_RATE, seed: int = 4) -> np.ndarray:
    """Stereo outdoor-ish ambience from filtered pink noise."""
    from scipy.signal import butter, sosfilt

    l = pink_noise(n, seed)
    r = pink_noise(n, seed + 1)
    sos = butter(2, [200, 6000], btype="band", fs=sr, output="sos")
    l = sosfilt(sos, l)
    r = sosfilt(sos, r)
    t = np.arange(n) / sr
    l *= 0.7 + 0.3 * np.sin(2 * np.pi * 0.07 * t)
    r *= 0.7 + 0.3 * np.sin(2 * np.pi * 0.09 * t + 0.5)
    return np.stack([l, r], axis=0)


def ensure_assets(asset_dir: Path, duration_s: float = 8.0) -> dict[str, dict]:
    """Create synthetic suite + category proxies. Returns registry dict."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    n = int(duration_s * SAMPLE_RATE)
    registry: dict[str, dict] = {}

    def reg(name: str, category: str, path: Path, note: str) -> None:
        registry[name] = {
            "file": path.name,
            "path": f"data/assets/{path.name}",
            "category": category,
            "source": "generated-by-aurora-p0",
            "license": "CC0-1.0 (original synthetic, dedicated to public domain)",
            "sha256": sha256_file(path),
            "sample_rate": SAMPLE_RATE,
            "duration_s": duration_s,
            "note": note,
        }

    # Synthetic analysis suite
    p = asset_dir / "synth_pink_stereo.wav"
    pink = pink_noise(n, 1)
    write_wav(p, np.stack([pink, pink * 0.95], axis=0))
    reg("synth_pink", "synth", p, "pink noise")

    p = asset_dir / "synth_sweep_mono.wav"
    write_wav(p, sweep(n))
    reg("synth_sweep", "synth", p, "log sweep")

    p = asset_dir / "synth_impulse_mono.wav"
    write_wav(p, impulse(max(n, SAMPLE_RATE)))
    reg("synth_impulse", "synth", p, "impulse")

    p = asset_dir / "synth_voice_mono.wav"
    write_wav(p, voice_substitute(n))
    reg("synth_voice", "synth", p, "formant voice substitute")

    # Category matrix materials (CC0 synthetic proxies; real-media fetch optional)
    p = asset_dir / "cat_voice.wav"
    v = voice_substitute(n, seed=21)
    write_wav(p, np.stack([v, v * 0.98], axis=0))
    reg("cat_voice", "voice", p, "stereo voice-category proxy for P0 matrix")

    p = asset_dir / "cat_music.wav"
    write_wav(p, music_substitute(n, seed=22))
    reg("cat_music", "music", p, "stereo music-category proxy for P0 matrix")

    p = asset_dir / "cat_ambience.wav"
    write_wav(p, ambience_substitute(n, seed=23))
    reg("cat_ambience", "ambience", p, "stereo ambience-category proxy for P0 matrix")

    # Real CC0 / CC BY excerpts. Synthetic cat_* stay for controlled measurements.
    _ensure_real_recordings(asset_dir, registry)

    meta = asset_dir / "registry.json"
    meta.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return registry


REAL_RECORDINGS = [
    {
        "name": "real_voice",
        "file": "real_voice.wav",
        "category": "voice",
        "url": "https://upload.wikimedia.org/wikipedia/commons/2/27/Brittany_Lauda_Sample.ogg",
        "page": "https://commons.wikimedia.org/wiki/File:Brittany_Lauda_Sample.ogg",
        "author": "Animyth Productions, Brittany Lauda",
        "license": "CC BY 3.0",
        "license_url": "https://creativecommons.org/licenses/by/3.0",
        "note": "Voice-actor sample, trimmed to the full 9.1 s source, 48 kHz stereo",
        "ffmpeg_ss": "0",
        "ffmpeg_t": "20",
    },
    {
        "name": "real_music",
        "file": "real_music.wav",
        "category": "music",
        "url": "https://upload.wikimedia.org/wikipedia/commons/2/2e/Luminous_Rain_%28ISRC_USUAN1100169%29.mp3",
        "page": "https://commons.wikimedia.org/wiki/File:Luminous_Rain_(ISRC_USUAN1100169).mp3",
        "author": "Kevin MacLeod (incompetech.com)",
        "license": "CC BY 3.0",
        "license_url": "https://creativecommons.org/licenses/by/3.0",
        "note": "Piano piece Luminous Rain, 20 s excerpt starting at 5 s, 48 kHz stereo",
        "ffmpeg_ss": "5",
        "ffmpeg_t": "20",
    },
    {
        "name": "real_ambience",
        "file": "real_ambience.wav",
        "category": "ambience",
        "url": "https://upload.wikimedia.org/wikipedia/commons/8/81/Rain_clip.theora.ogv",
        "page": "https://commons.wikimedia.org/wiki/File:Rain_clip.theora.ogv",
        "author": "PB (Wikimedia Commons user Buddpaul)",
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "note": "Field recording of rainfall, full 8.7 s audio extracted from the CC0 video",
        "ffmpeg_ss": "0",
        "ffmpeg_t": "20",
    },
]


def _ensure_real_recordings(asset_dir: Path, registry: dict) -> None:
    """Download and trim real recordings when the wav excerpt is missing."""
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    for spec in REAL_RECORDINGS:
        dest = asset_dir / spec["file"]
        if not dest.is_file():
            if not ffmpeg:
                raise RuntimeError(f"ffmpeg required to build {dest.name}")
            raw = asset_dir / "src" / spec["file"].replace(".wav", ".src")
            raw.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["curl", "-fL", "--retry", "3", "-A", "AuroraBinauralP0/1.0", "--max-time", "120", "-o", str(raw), spec["url"]],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"download failed {spec['url']}: {r.stderr[-400:]}")
            conv = subprocess.run(
                [
                    ffmpeg, "-y", "-ss", spec["ffmpeg_ss"], "-i", str(raw),
                    "-t", spec["ffmpeg_t"], "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE),
                    "-c:a", "pcm_s24le", str(dest),
                ],
                capture_output=True,
                text=True,
            )
            if conv.returncode != 0:
                raise RuntimeError(f"ffmpeg failed for {spec['file']}: {conv.stderr[-400:]}")
        info = sf.info(str(dest))
        registry[spec["name"]] = {
            "file": dest.name,
            "path": f"data/assets/{dest.name}",
            "category": spec["category"],
            "source": spec["url"],
            "page": spec["page"],
            "author": spec["author"],
            "license": spec["license"],
            "license_url": spec["license_url"],
            "sha256": sha256_file(dest),
            "sample_rate": info.samplerate,
            "duration_s": round(info.duration, 3),
            "note": spec["note"],
        }


def _try_fetch_cc0(asset_dir: Path, registry: dict) -> None:
    """Best-effort: download a short CC0 clip if network allows."""
    # Wikimedia Commons — File:En-us-hello.ogg is public domain / free
    url = "https://upload.wikimedia.org/wikipedia/commons/6/6d/En-us-hello.ogg"
    dest = asset_dir / "cc0_hello_raw.ogg"
    if dest.exists():
        return
    r = subprocess.run(
        ["curl", "-L", "--fail", "-o", str(dest), url],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        registry["_fetch_note"] = {"curl_exit": r.returncode, "stderr": r.stderr[-500:]}
        return
    wav = asset_dir / "cc0_voice_hello.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(dest),
            "-ac",
            "2",
            "-ar",
            str(SAMPLE_RATE),
            "-t",
            "5",
            "-c:a",
            "pcm_s24le",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    registry["cc0_voice_hello"] = {
        "path": str(wav),
        "category": "voice",
        "source": url,
        "license": "Public domain / Wikimedia Commons (English pronunciation recording)",
        "sha256": sha256_file(wav),
        "note": "Optional real voice snippet; matrix uses cat_* proxies for equal duration",
    }
