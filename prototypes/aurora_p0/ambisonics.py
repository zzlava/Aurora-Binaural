"""3rd-order Ambisonics (ACN/SN3D) encoding + MagLS-style binaural decode."""

from __future__ import annotations

import math

import numpy as np
from scipy.fft import irfft, rfft
from scipy.signal import fftconvolve
from scipy.special import lpmv

from .hrtf import HrtfDataset


def n_channels(order: int) -> int:
    return (order + 1) ** 2


def real_spherical_harmonics_acn_sn3d(az_deg: float, el_deg: float, order: int) -> np.ndarray:
    """Real SH coefficients, ACN ordering, SN3D normalization."""
    az = np.deg2rad(az_deg)
    el = np.deg2rad(el_deg)
    theta = 0.5 * np.pi - el
    mu = float(np.cos(theta))
    coeffs = []
    for n in range(order + 1):
        for m in range(-n, n + 1):
            abs_m = abs(m)
            norm_n3d = np.sqrt(
                (2 * n + 1)
                / (4 * np.pi)
                * (math.factorial(n - abs_m) / math.factorial(n + abs_m))
            )
            if m != 0:
                norm_n3d *= np.sqrt(2.0)
            norm_sn3d = norm_n3d / np.sqrt(2 * n + 1)
            p = float(lpmv(abs_m, n, mu))
            if m > 0:
                y = norm_sn3d * p * np.cos(m * az)
            elif m < 0:
                y = norm_sn3d * p * np.sin(abs_m * az)
            else:
                y = norm_sn3d * p
            coeffs.append(y)
    return np.asarray(coeffs, dtype=np.float64)


def encode_plane_wave(mono: np.ndarray, az_deg: float, el_deg: float = 0.0, order: int = 3) -> np.ndarray:
    """Encode mono source as Ambisonics bed (C, N)."""
    y = real_spherical_harmonics_acn_sn3d(az_deg, el_deg, order)
    return y[:, None] * np.asarray(mono, dtype=np.float64).ravel()[None, :]


def encode_stereo_ambient(
    left: np.ndarray,
    right: np.ndarray,
    order: int = 3,
    positions: list[tuple[float, float]] | None = None,
) -> np.ndarray:
    """Place L/R ambient into side-rear Ambisonics positions."""
    if positions is None:
        positions = [(120.0, 0.0), (-120.0, 0.0), (90.0, 15.0), (-90.0, 15.0)]
    n = min(left.shape[0], right.shape[0])
    bed = np.zeros((n_channels(order), n), dtype=np.float64)
    sources = [
        (left[:n] * 0.7, positions[0]),
        (right[:n] * 0.7, positions[1]),
        (left[:n] * 0.3, positions[2]),
        (right[:n] * 0.3, positions[3]),
    ]
    for sig, (az, el) in sources:
        bed += encode_plane_wave(sig, az, el, order)
    return bed


class MagLSDecoder:
    """Magnitude least-squares binaural Ambisonics decoder from an HRTF set.

    Dual-band flavour: LS decode with high-frequency order weighting
    (MagLS-inspired). Suitable for P0 route comparison.
    """

    def __init__(self, ds: HrtfDataset, order: int = 3, f_dual: float = 2000.0, n_grid: int = 120):
        self.order = order
        self.ds = ds
        self.sr = ds.sr
        self.n_ch = n_channels(order)
        self.n_taps = ds.n_taps
        rng = np.random.default_rng(0)
        if ds.n_dirs > n_grid:
            idx = rng.choice(ds.n_dirs, size=n_grid, replace=False)
        else:
            idx = np.arange(ds.n_dirs)
        az = ds.azimuths[idx]
        el = ds.elevations[idx]
        Y = np.stack([real_spherical_harmonics_acn_sn3d(a, e, order) for a, e in zip(az, el)], axis=0)
        H_l = ds.hrir_l[idx]
        H_r = ds.hrir_r[idx]
        reg = 1e-3 * np.eye(self.n_ch)
        pinv = np.linalg.solve(Y.T @ Y + reg, Y.T)
        self.dec_l = pinv @ H_l
        self.dec_r = pinv @ H_r
        nfft = 1 << ((self.n_taps - 1).bit_length() + 1)
        Dl = rfft(self.dec_l, n=nfft, axis=1)
        Dr = rfft(self.dec_r, n=nfft, axis=1)
        freqs = np.fft.rfftfreq(nfft, d=1.0 / self.sr)
        w_high = 1.0 / (1.0 + np.exp(-(freqs - f_dual) / 200.0))
        for c in range(self.n_ch):
            n_ord = int(np.floor(np.sqrt(c)))
            scale = 1.0 - 0.15 * w_high * n_ord / max(order, 1)
            Dl[c] *= scale
            Dr[c] *= scale
        self.dec_l = irfft(Dl, n=nfft, axis=1)[:, : self.n_taps]
        self.dec_r = irfft(Dr, n=nfft, axis=1)[:, : self.n_taps]
        self.f_dual = f_dual

    def decode(self, bed: np.ndarray) -> np.ndarray:
        """bed (C, N) -> stereo (2, N)."""
        C, n = bed.shape
        if C != self.n_ch:
            raise ValueError(f"expected {self.n_ch} channels, got {C}")
        yl = np.zeros(n, dtype=np.float64)
        yr = np.zeros(n, dtype=np.float64)
        for c in range(C):
            yl += fftconvolve(bed[c], self.dec_l[c], mode="full")[:n]
            yr += fftconvolve(bed[c], self.dec_r[c], mode="full")[:n]
        return np.stack([yl, yr], axis=0)


def rotate_yaw(bed: np.ndarray, yaw_deg: float, order: int = 3) -> np.ndarray:
    """Rotate Ambisonics scene about +Z. Positive = scene rotates left."""
    yaw = np.deg2rad(yaw_deg)
    out = bed.copy()
    if order >= 1 and bed.shape[0] >= 4:
        y, z, x = bed[1].copy(), bed[2].copy(), bed[3].copy()
        out[3] = x * np.cos(yaw) - y * np.sin(yaw)
        out[1] = x * np.sin(yaw) + y * np.cos(yaw)
        out[2] = z
    return out
