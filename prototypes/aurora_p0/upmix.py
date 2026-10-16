"""STFT direct/ambient stereo upmix (Avendano & Jot style)."""

from __future__ import annotations

import numpy as np
from scipy.signal import stft, istft


def _sqrt_hann(n: int) -> np.ndarray:
    w = np.hanning(n)
    return np.sqrt(w)


def stereo_upmix(
    left: np.ndarray,
    right: np.ndarray,
    sr: int,
    n_fft: int = 1024,
    hop: int = 256,
    width_deg: float = 30.0,
    ambient_mix: float = 1.0,
) -> dict[str, np.ndarray | float]:
    """Separate stereo into direct (with pan azimuth) and ambient.

    Returns dict with keys:
      direct_l, direct_r, ambient_l, ambient_r, azimuth_deg (per-frame estimate mean),
      pan_index (time-freq), coherence
    """
    left = np.asarray(left, dtype=np.float64).ravel()
    right = np.asarray(right, dtype=np.float64).ravel()
    n = min(left.shape[0], right.shape[0])
    left, right = left[:n], right[:n]
    window = _sqrt_hann(n_fft)
    stft_kw = dict(fs=sr, window=window, nperseg=n_fft, noverlap=n_fft - hop, boundary="zeros", padded=True)
    f, t, L = stft(left, **stft_kw)
    _, _, R = stft(right, **stft_kw)
    # Cross-spectrum coherence
    eps = 1e-12
    Gll = (L * np.conj(L)).real
    Grr = (R * np.conj(R)).real
    Glr = L * np.conj(R)
    num = np.abs(Glr) ** 2
    den = np.maximum(Gll * Grr, eps)
    phi = np.sqrt(np.clip(num / den, 0.0, 1.0))  # coherence [0,1]
    # Smooth masks lightly along time
    phi = _smooth2d(phi, t_win=3, f_win=3)
    # Panning index from level difference
    # g = (Gll - Grr) / (Gll + Grr) ∈ [-1,1]; +1 = left
    g = (Gll - Grr) / np.maximum(Gll + Grr, eps)
    g = np.clip(g, -1.0, 1.0)
    # Additive complementary masks so direct+ambient reconstructs input when ambient_mix=1
    m_dir = np.clip(phi, 0.0, 1.0)
    m_amb = (1.0 - m_dir) * float(np.clip(ambient_mix, 0.0, 1.0))
    # Limit mask rate (simple temporal clamp); re-complement after limiting
    m_dir = _limit_rate(m_dir, max_delta=0.35)
    m_amb = (1.0 - m_dir) * float(np.clip(ambient_mix, 0.0, 1.0))
    m_amb = _limit_rate(m_amb, max_delta=0.35)
    Ld = L * m_dir
    Rd = R * m_dir
    La = L * m_amb
    Ra = R * m_amb
    istft_kw = dict(
        fs=sr,
        window=window,
        nperseg=n_fft,
        noverlap=n_fft - hop,
        input_onesided=True,
        boundary=True,
    )
    _, direct_l = istft(Ld, **istft_kw)
    _, direct_r = istft(Rd, **istft_kw)
    _, ambient_l = istft(La, **istft_kw)
    _, ambient_r = istft(Ra, **istft_kw)
    # Trim / pad to n
    direct_l = _match_len(direct_l, n)
    direct_r = _match_len(direct_r, n)
    ambient_l = _match_len(ambient_l, n)
    ambient_r = _match_len(ambient_r, n)
    # Azimuth map: pan g → ±width_deg (plan: +left)
    az_tf = g * width_deg
    # Frame-wise energy-weighted mean azimuth for object rendering
    w = Gll + Grr
    az_frame = np.sum(az_tf * w, axis=0) / np.maximum(np.sum(w, axis=0), eps)
    # Per-sample azimuth via hold
    az_samples = np.interp(np.arange(n), t * sr, az_frame)
    return {
        "direct_l": direct_l,
        "direct_r": direct_r,
        "ambient_l": ambient_l,
        "ambient_r": ambient_r,
        "azimuth_samples": az_samples,
        "azimuth_frame": az_frame,
        "coherence_mean": float(np.mean(phi)),
    }


def decorrelate(x: np.ndarray, sr: int, seed: int, n_ap: int = 4) -> np.ndarray:
    """Seeded cascaded allpass decorrelator (scipy lfilter)."""
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    y = np.asarray(x, dtype=np.float64).copy()
    for _ in range(n_ap):
        d = int(rng.integers(int(0.001 * sr), int(0.012 * sr)))
        g = float(rng.uniform(0.3, 0.7))
        # y = (-g + z^-d) / (1 - g z^-d) * x
        b = np.zeros(d + 1)
        a = np.zeros(d + 1)
        b[0] = -g
        b[d] = 1.0
        a[0] = 1.0
        a[d] = -g
        y = lfilter(b, a, y)
    return y


def _match_len(x: np.ndarray, n: int) -> np.ndarray:
    if x.shape[0] >= n:
        return x[:n]
    return np.pad(x, (0, n - x.shape[0]))


def _smooth2d(a: np.ndarray, t_win: int = 3, f_win: int = 3) -> np.ndarray:
    from scipy.ndimage import uniform_filter

    return uniform_filter(a, size=(f_win, t_win), mode="nearest")


def _limit_rate(m: np.ndarray, max_delta: float) -> np.ndarray:
    out = m.copy()
    for t in range(1, out.shape[1]):
        delta = out[:, t] - out[:, t - 1]
        delta = np.clip(delta, -max_delta, max_delta)
        out[:, t] = out[:, t - 1] + delta
    return out


def energy_conservation_error(left: np.ndarray, right: np.ndarray, up: dict) -> float:
    """RMS error of direct+ambient vs input (should be small when ambient_mix=1)."""
    recon_l = up["direct_l"] + up["ambient_l"]
    recon_r = up["direct_r"] + up["ambient_r"]
    err = np.mean((recon_l - left) ** 2 + (recon_r - right) ** 2)
    ref = np.mean(left**2 + right**2) + 1e-12
    return float(10 * np.log10(err / ref + 1e-20))
