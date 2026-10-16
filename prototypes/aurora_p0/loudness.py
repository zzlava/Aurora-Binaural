"""BS.1770 loudness + true-peak (4× oversample)."""

from __future__ import annotations

import numpy as np
import pyloudnorm as pyln
from scipy.signal import resample_poly


def integrate_loudness(stereo: np.ndarray, sr: int) -> float:
    """stereo (2, N) -> integrated LUFS."""
    meter = pyln.Meter(sr)
    # pyloudnorm expects (N, ch)
    data = np.asarray(stereo, dtype=np.float64).T
    if data.shape[0] < sr // 10:
        # too short — pad for meter stability
        data = np.pad(data, ((0, sr // 4), (0, 0)))
    try:
        return float(pyln.Meter(sr).integrated_loudness(data))
    except Exception:
        return float("nan")


def true_peak_dbtp(stereo: np.ndarray, sr: int, oversample: int = 4) -> float:
    """True peak in dBTP with 4× oversampling (BS.1770)."""
    x = np.asarray(stereo, dtype=np.float64)
    peaks = []
    for ch in range(x.shape[0]):
        y = resample_poly(x[ch], oversample, 1)
        peaks.append(float(np.max(np.abs(y))))
    peak = max(peaks) if peaks else 0.0
    if peak <= 0:
        return float("-inf")
    return float(20.0 * np.log10(peak + 1e-20))


def third_octave_centers(f_lo: float = 4000.0, f_hi: float = 16000.0) -> list[float]:
    centers: list[float] = []
    f = f_lo
    while f < f_hi * 1.01:
        centers.append(f)
        f *= 2.0 ** (1.0 / 3.0)
    return centers


def equal_loudness_timbre(
    wet: np.ndarray,
    dry: np.ndarray,
    sr: int,
    target_lufs: float = -20.0,
) -> tuple[np.ndarray, float, float, dict]:
    """Loudness-match, pull 1/3-octave power toward the dry file, match again.

    The second match is a single gain. Correcting the whole 0.4–16 kHz
    shape (not only the top octave) keeps that gain from tilting 4–16 kHz
    back outside ±3 dB.
    """
    wet1, before, _ = match_loudness(wet, sr, target_lufs)
    dry1, _, _ = match_loudness(dry, sr, target_lufs)
    guarded, info = timbre_guard(wet1, dry1, sr, f_lo=400.0, f_hi=16000.0)
    final, _, after = match_loudness(guarded, sr, target_lufs)
    return final, before, after, info


def timbre_guard(
    wet: np.ndarray,
    dry: np.ndarray,
    sr: int,
    f_lo: float = 400.0,
    f_hi: float = 16000.0,
    max_db: float = 18.0,
) -> tuple[np.ndarray, dict]:
    """Shared minimum-phase 1/3-octave EQ so ear-sum power tracks the dry signal.

    The same filter is applied to both ears, so ITD/ILD are unchanged.
    Band gains use the same rectangular 1/3-octave edges as the QA metric.
    """
    from scipy.fft import irfft, rfft
    from scipy.signal import fftconvolve

    wet = np.asarray(wet, dtype=np.float64)
    dry = np.asarray(dry, dtype=np.float64)
    # Two passes: a minimum-phase FIR only approximates the rectangular bands.
    info: dict = {}
    for _pass in range(3):
        wet, info = _timbre_guard_once(wet, dry, sr, f_lo, f_hi, max_db)
    return wet, info


def _timbre_guard_once(
    wet: np.ndarray,
    dry: np.ndarray,
    sr: int,
    f_lo: float,
    f_hi: float,
    max_db: float,
) -> tuple[np.ndarray, dict]:
    from scipy.fft import irfft, rfft
    from scipy.signal import fftconvolve

    n = min(wet.shape[1], dry.shape[1])
    nfft = 1 << (max(n, 2048) - 1).bit_length()
    freqs = np.fft.rfftfreq(nfft, d=1.0 / sr)

    def band_db(x: np.ndarray) -> list[tuple[float, float]]:
        # Power sum of the ears, not L+R. A coherent sum confuses decorrelation
        # with a timbre change and then loudness matching undoes the EQ.
        spec = np.zeros(nfft // 2 + 1, dtype=np.float64)
        for ch in range(x.shape[0]):
            spec += np.abs(np.fft.rfft(np.asarray(x[ch, :n], dtype=np.float64), n=nfft)) ** 2
        out = []
        f = f_lo
        while f < f_hi * 1.01:
            lo, hi = f / 2.0 ** (1.0 / 6.0), f * 2.0 ** (1.0 / 6.0)
            m = (freqs >= lo) & (freqs < hi)
            level = float(10.0 * np.log10(spec[m].mean() + 1e-20)) if np.any(m) else 0.0
            out.append((f, level))
            f *= 2.0 ** (1.0 / 3.0)
        return out

    wd = band_db(wet[:, :n] if wet.shape[1] != n else wet)
    dd = band_db(dry[:, :n] if dry.shape[1] != n else dry)
    centers = np.array([c for c, _ in wd], dtype=np.float64)
    delta = np.array([d[1] - w[1] for w, d in zip(wd, dd)], dtype=np.float64)
    delta = np.clip(delta, -max_db, max_db)
    # Interpolate band corrections onto FFT bins; 0 dB below one octave under f_lo.
    gain_db = np.interp(freqs, centers, delta, left=0.0, right=float(delta[-1]) if len(delta) else 0.0)
    fade_lo = f_lo / 2.0
    fade = np.clip((freqs - fade_lo) / max(f_lo - fade_lo, 1.0), 0.0, 1.0)
    gain_db = gain_db * fade
    gain = 10.0 ** (gain_db / 20.0)
    log_g = np.log(np.maximum(gain, 1e-8))
    cep = irfft(log_g, n=nfft)
    cep[1 : nfft // 2] *= 2.0
    cep[nfft // 2 + 1 :] = 0.0
    h = irfft(np.exp(rfft(cep, n=nfft)), n=nfft)
    taps = 512
    window = np.hanning(2 * taps)[taps:]
    fir = h[:taps] * window
    out = np.zeros_like(wet)
    out[0] = fftconvolve(wet[0], fir, mode="full")[: wet.shape[1]]
    out[1] = fftconvolve(wet[1], fir, mode="full")[: wet.shape[1]]
    info = {
        "max_abs_correction_db_4_16k": float(np.max(np.abs(delta))) if delta.size else 0.0,
        "mean_correction_db_4_16k": float(np.mean(delta)) if delta.size else 0.0,
        "band_correction_db": delta.tolist(),
    }
    return out, info


def limit_true_peak(
    stereo: np.ndarray, sr: int, ceiling_dbtp: float = -1.0, release_ms: float = 40.0
) -> np.ndarray:
    """Attenuate only the oversampled peaks, leaving the body of the signal.

    A single broadband gain that satisfies the true-peak ceiling pulls a whole
    file down when a few HRTF transients stick out, and the later loudness
    match then misses 4–16 kHz. Instant attack, ~40 ms release, 4× oversampled.
    """
    ceiling = 10.0 ** (ceiling_dbtp / 20.0)
    oversample = 4
    release = max(1, int(sr * oversample * release_ms / 1000.0))
    out = np.zeros_like(stereo, dtype=np.float64)
    for ch in range(stereo.shape[0]):
        y = resample_poly(np.asarray(stereo[ch], dtype=np.float64), oversample, 1)
        mag = np.abs(y)
        target = np.ones(y.shape[0], dtype=np.float64)
        hot = mag > ceiling
        target[hot] = ceiling / mag[hot]
        gain = np.empty_like(target)
        g = 1.0
        for i in range(target.shape[0]):
            t = target[i]
            if t < g:
                g = t
            else:
                g += (1.0 - g) / release
            gain[i] = g
        y *= gain
        down = resample_poly(y, 1, oversample)
        n = stereo.shape[1]
        if down.shape[0] < n:
            out[ch, : down.shape[0]] = down
        else:
            out[ch] = down[:n]
    return out


def match_loudness(stereo: np.ndarray, sr: int, target_lufs: float = -20.0) -> tuple[np.ndarray, float, float]:
    """Gain to target integrated loudness. Returns (audio, before, after)."""
    before = integrate_loudness(stereo, sr)
    if not np.isfinite(before):
        return stereo.copy(), before, before
    gain_db = target_lufs - before
    gain = 10 ** (gain_db / 20.0)
    out = stereo * gain
    # Large overshoot: duck only the peaks (a broadband cut used to miss the
    # loudness target by the crest-factor surplus). Small overshoot: a single
    # trim. The peak limiter's gain steps spray energy back into 10–16 kHz
    # after the timbre guard has already flattened those bands.
    tp = true_peak_dbtp(out, sr)
    if tp > -1.0:
        if tp - (-1.0) <= 2.0:
            out *= 10 ** ((-1.0 - tp) / 20.0)
        else:
            out = limit_true_peak(out, sr, ceiling_dbtp=-1.0)
            tp = true_peak_dbtp(out, sr)
            if tp > -1.0:
                out *= 10 ** ((-1.0 - tp) / 20.0)
    after = integrate_loudness(out, sr)
    return out, before, after
