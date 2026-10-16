"""Fixed minimum-phase timbre correction.

The gains depend on HRTF, equalization mode, preset and strength. They are
designed offline (see ``qa/p0/cursor/tools/design_fixed_eq.py``) and stored in the
preset. Nothing here looks at the program being rendered.
"""

from __future__ import annotations

import numpy as np
from scipy.fft import irfft, rfft
from scipy.signal import fftconvolve


def minphase_from_bands(
    bands_hz: np.ndarray,
    gain_db: np.ndarray,
    sr: int,
    taps: int = 2048,
    nfft: int = 8192,
) -> np.ndarray:
    """Minimum-phase FIR whose 1/3-octave curve follows ``gain_db``."""
    bands = np.asarray(bands_hz, dtype=np.float64)
    gains = np.asarray(gain_db, dtype=np.float64)
    freqs = np.fft.rfftfreq(nfft, d=1.0 / sr)
    gdb = np.interp(freqs, bands, gains, left=0.0, right=0.0)
    # Leave the sub-bass and the top octave to the crossover / Nyquist.
    fade_lo = np.clip((freqs - 20.0) / 20.0, 0.0, 1.0)
    fade_hi = np.clip((18000.0 - freqs) / 2000.0, 0.0, 1.0)
    gdb = gdb * fade_lo * fade_hi
    log_g = np.log(np.maximum(10.0 ** (gdb / 20.0), 1e-8))
    cep = irfft(log_g, n=nfft)
    cep[1 : nfft // 2] *= 2.0
    cep[nfft // 2 + 1 :] = 0.0
    impulse = irfft(np.exp(rfft(cep, n=nfft)), n=nfft)
    window = np.hanning(2 * taps)[taps:]
    fir = impulse[:taps] * window
    # Unity at the design sample rate's mid band so a flat curve stays ~0 dB.
    return fir.astype(np.float64)


def apply_stereo(stereo: np.ndarray, fir: np.ndarray) -> np.ndarray:
    """Same FIR on both ears. The magnitude curve can still move broadband IACC."""
    fir = np.asarray(fir, dtype=np.float64).ravel()
    if fir.size == 0 or float(np.max(np.abs(fir))) < 1e-15:
        return stereo
    n = stereo.shape[1]
    out = np.zeros_like(stereo, dtype=np.float64)
    for ch in range(stereo.shape[0]):
        out[ch] = fftconvolve(np.asarray(stereo[ch], dtype=np.float64), fir, mode="full")[:n]
    return out
