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


def broadband_loudness_match(
    stereo: np.ndarray, sr: int, target_lufs: float = -20.0, ceiling_dbtp: float = -1.0
) -> tuple[np.ndarray, float, float, float]:
    """One scalar gain. Loudness target, then a second scalar only if true-peak clips.

    This is the only level match allowed on delivery samples. It does not
    change spectral shape. Returns (audio, lufs_before, lufs_after, gain_db).
    """
    before = integrate_loudness(stereo, sr)
    if not np.isfinite(before):
        return stereo.copy(), before, before, 0.0
    gain_db = target_lufs - before
    out = np.asarray(stereo, dtype=np.float64) * (10.0 ** (gain_db / 20.0))
    tp = true_peak_dbtp(out, sr)
    if np.isfinite(tp) and tp > ceiling_dbtp:
        trim = ceiling_dbtp - tp
        gain_db += trim
        out *= 10.0 ** (trim / 20.0)
    after = integrate_loudness(out, sr)
    return out, before, after, gain_db


def diagnostic_equal_loudness_timbre(
    wet: np.ndarray,
    dry: np.ndarray,
    sr: int,
    target_lufs: float = -20.0,
) -> tuple[np.ndarray, float, float, dict]:
    """DIAGNOSTIC ONLY. Do not use for delivery samples or reported metrics.

    Fits a minimum-phase 1/3-octave curve to this specific dry file. That
    depends on the input signal, so it cannot run in the realtime engine.
    Delivery timbre correction is the fixed filter inside ``Processor``.
    """
    wet1, before, _ = match_loudness(wet, sr, target_lufs)
    dry1, _, _ = match_loudness(dry, sr, target_lufs)
    guarded, info = diagnostic_timbre_guard(wet1, dry1, sr, f_lo=40.0, f_hi=16000.0)
    final, _, after = match_loudness(guarded, sr, target_lufs)
    return final, before, after, info


def diagnostic_timbre_guard(
    wet: np.ndarray,
    dry: np.ndarray,
    sr: int,
    f_lo: float = 40.0,
    f_hi: float = 16000.0,
    max_db: float = 18.0,
) -> tuple[np.ndarray, dict]:
    """DIAGNOSTIC ONLY. Do not use for delivery samples or reported metrics.

    Minimum-phase 1/3-octave timbre EQ from 40 Hz to 16 kHz, fit to this
    dry file. Signal-dependent, so it is not part of the render chain.

    A common gain (same on both ears) recenters level. Where decorrelation
    has pushed the mono downmix and the ear-power sum more than 4 dB apart,
    a little mid/side energy is moved so the two observations can both land
    inside ±3 dB. ITD of the mid stays; only that band's side level changes.
    """
    wet = np.asarray(wet, dtype=np.float64)
    dry = np.asarray(dry, dtype=np.float64)
    info: dict = {}
    for _pass in range(3):
        wet, info = _timbre_guard_once(wet, dry, sr, f_lo, f_hi, max_db)
    return wet, info


def _iso_centers(f_lo: float, f_hi: float) -> np.ndarray:
    ks = np.arange(-20, 16)
    centers = 1000.0 * (2.0 ** (ks / 3.0))
    return centers[(centers >= f_lo * 0.99) & (centers <= f_hi * 1.01)]


def _band_power_db(x: np.ndarray, centers: np.ndarray, nfft: int, sr: int, mode: str) -> np.ndarray:
    """1/3-octave levels. ``ear`` sums ear power; ``mono`` uses (L+R)/2."""
    n = x.shape[1]
    freqs = np.fft.rfftfreq(nfft, d=1.0 / sr)
    if mode == "mono":
        mono = np.mean(np.asarray(x[:, :n], dtype=np.float64), axis=0)
        spec = np.abs(np.fft.rfft(mono, n=nfft)) ** 2
    else:
        spec = np.zeros(nfft // 2 + 1, dtype=np.float64)
        for ch in range(x.shape[0]):
            spec += np.abs(np.fft.rfft(np.asarray(x[ch, :n], dtype=np.float64), n=nfft)) ** 2
    out = np.zeros(centers.shape[0], dtype=np.float64)
    for i, f in enumerate(centers):
        lo, hi = f / 2.0 ** (1.0 / 6.0), f * 2.0 ** (1.0 / 6.0)
        m = (freqs >= lo) & (freqs < hi)
        out[i] = float(10.0 * np.log10(spec[m].mean() + 1e-20)) if np.any(m) else 0.0
    return out


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
    nfft = 1 << (max(n, 4096) - 1).bit_length()
    freqs = np.fft.rfftfreq(nfft, d=1.0 / sr)
    centers = _iso_centers(f_lo, f_hi)
    wet_n = wet[:, :n] if wet.shape[1] != n else wet
    dry_n = dry[:, :n] if dry.shape[1] != n else dry
    delta_ear = _band_power_db(dry_n, centers, nfft, sr, "ear") - _band_power_db(wet_n, centers, nfft, sr, "ear")
    delta_mono = _band_power_db(dry_n, centers, nfft, sr, "mono") - _band_power_db(wet_n, centers, nfft, sr, "mono")
    # Positive gap: mono is quiet relative to ear power, i.e. this band is
    # more decorrelated than the dry file. Move just enough side energy onto
    # the mid to leave a 4 dB gap, which the common gain then centers at ±2 dB.
    gap = delta_mono - delta_ear
    shift = np.where(np.abs(gap) > 4.0, gap - np.sign(gap) * 4.0, 0.0)
    shift = np.clip(shift, -6.0, 6.0)
    mid = 0.5 * (wet_n[0] + wet_n[1])
    side = 0.5 * (wet_n[0] - wet_n[1])
    pm = 10.0 ** (_band_power_db(mid[np.newaxis, :], centers, nfft, sr, "ear") / 10.0)
    ps = 10.0 ** (_band_power_db(side[np.newaxis, :], centers, nfft, sr, "ear") / 10.0)
    total = pm + ps
    target_pm = pm * (10.0 ** (shift / 10.0))
    target_pm = np.minimum(target_pm, total * 0.98)
    target_ps = np.maximum(total - target_pm, total * 1e-6)
    with np.errstate(divide="ignore", invalid="ignore"):
        gm = np.sqrt(np.maximum(target_pm, 1e-20) / np.maximum(pm, 1e-20))
        gs = np.sqrt(np.maximum(target_ps, 1e-20) / np.maximum(ps, 1e-20))
    gm = np.clip(gm, 10.0 ** (-max_db / 20.0), 10.0 ** (max_db / 20.0))
    gs = np.clip(gs, 10.0 ** (-max_db / 20.0), 10.0 ** (max_db / 20.0))
    common = np.clip(0.5 * (delta_mono - shift + delta_ear), -max_db, max_db)
    gm_db = np.clip(20.0 * np.log10(gm) + common, -max_db, max_db)
    gs_db = np.clip(20.0 * np.log10(gs) + common, -max_db, max_db)
    fade_lo = f_lo / 2.0
    fade = np.clip((freqs - fade_lo) / max(f_lo - fade_lo, 1.0), 0.0, 1.0)

    def _fir(gain_db_bands: np.ndarray) -> np.ndarray:
        gain_db = np.interp(freqs, centers, gain_db_bands, left=0.0, right=float(gain_db_bands[-1]))
        gain_db = gain_db * fade
        log_g = np.log(np.maximum(10.0 ** (gain_db / 20.0), 1e-8))
        cep = irfft(log_g, n=nfft)
        cep[1 : nfft // 2] *= 2.0
        cep[nfft // 2 + 1 :] = 0.0
        h = irfft(np.exp(rfft(cep, n=nfft)), n=nfft)
        taps = 2048
        window = np.hanning(2 * taps)[taps:]
        return h[:taps] * window

    fir_m = _fir(gm_db)
    fir_s = _fir(gs_db)
    mid_f = fftconvolve(0.5 * (wet[0] + wet[1]), fir_m, mode="full")[: wet.shape[1]]
    side_f = fftconvolve(0.5 * (wet[0] - wet[1]), fir_s, mode="full")[: wet.shape[1]]
    out = np.zeros_like(wet)
    out[0] = mid_f + side_f
    out[1] = mid_f - side_f
    hi = np.abs(common[centers >= 4000.0])
    info = {
        "max_abs_correction_db_4_16k": float(np.max(hi)) if hi.size else 0.0,
        "mean_correction_db_4_16k": float(np.mean(common[centers >= 4000.0])) if hi.size else 0.0,
        "max_abs_correction_db_40_16k": float(np.max(np.abs(common))) if common.size else 0.0,
        "max_abs_ms_shift_db": float(np.max(np.abs(shift))) if shift.size else 0.0,
        "band_correction_db": common.tolist(),
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
