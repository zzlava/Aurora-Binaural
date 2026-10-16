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


def match_loudness(stereo: np.ndarray, sr: int, target_lufs: float = -20.0) -> tuple[np.ndarray, float, float]:
    """Gain to target integrated loudness. Returns (audio, before, after)."""
    before = integrate_loudness(stereo, sr)
    if not np.isfinite(before):
        return stereo.copy(), before, before
    gain_db = target_lufs - before
    gain = 10 ** (gain_db / 20.0)
    out = stereo * gain
    # True-peak safety: if > -1 dBTP, scale down
    tp = true_peak_dbtp(out, sr)
    if tp > -1.0:
        out *= 10 ** ((-1.0 - tp) / 20.0)
    after = integrate_loudness(out, sr)
    return out, before, after
