"""SOFA HRTF loading, equalization, nearest-neighbour / IDW interpolation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sofar
from scipy.signal import fftconvolve, resample_poly
from scipy.fft import rfft, irfft

from .coords import angular_distance_deg


@dataclass
class HrtfDataset:
    name: str
    path: Path
    sr: int
    azimuths: np.ndarray  # (N,) deg, +left
    elevations: np.ndarray  # (N,) deg
    hrir_l: np.ndarray  # (N, taps)
    hrir_r: np.ndarray  # (N, taps)
    eq_mode: str = "diffuse"  # "diffuse" | "frontal" | "none"

    @property
    def n_dirs(self) -> int:
        return int(self.azimuths.shape[0])

    @property
    def n_taps(self) -> int:
        return int(self.hrir_l.shape[1])


def _sofa_az_el(sofa) -> tuple[np.ndarray, np.ndarray]:
    """Extract azimuth/elevation in plan §6.9 convention from SOFA SourcePosition."""
    pos = np.asarray(sofa.SourcePosition, dtype=np.float64)
    # SOFA convention typically: azi (deg, 0 front, increasing CCW/+left), ele, radius
    # Some files use cartesian — detect by units / ranges.
    units = getattr(sofa, "SourcePosition_Units", "degree, degree, metre")
    if isinstance(units, (list, tuple)):
        units = ", ".join(units)
    units_l = str(units).lower()
    if "metre" in units_l and "degree" not in units_l:
        # cartesian
        x, y, z = pos[:, 0], pos[:, 1], pos[:, 2]
        az = np.rad2deg(np.arctan2(y, x))
        r = np.linalg.norm(pos[:, :3], axis=1)
        el = np.rad2deg(np.arcsin(np.clip(z / np.maximum(r, 1e-12), -1.0, 1.0)))
        return az, el
    az = pos[:, 0]
    el = pos[:, 1]
    # Normalize azi to [-180, 180]
    az = ((az + 180.0) % 360.0) - 180.0
    return az.astype(np.float64), el.astype(np.float64)


def load_sofa(path: Path | str, name: str | None = None, target_sr: int = 48_000, n_taps: int = 256) -> HrtfDataset:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"HRTF SOFA missing: {path}. Refusing synthetic fallback. See data/hrtf/SOURCES.md."
        )
    sofa = sofar.read_sofa(str(path))
    sr = int(sofa.Data_SamplingRate)
    data = np.asarray(sofa.Data_IR, dtype=np.float64)
    # Expected shape (M, R, N) with R=2 (L,R)
    if data.ndim != 3 or data.shape[1] < 2:
        raise ValueError(f"Unexpected SOFA IR shape {data.shape} in {path}")
    az, el = _sofa_az_el(sofa)
    hrir_l = data[:, 0, :]
    hrir_r = data[:, 1, :]
    if sr != target_sr:
        # Resample HRIRs, not audio (plan §5.3)
        from math import gcd

        g = gcd(target_sr, sr)
        up, down = target_sr // g, sr // g
        hrir_l = np.stack([resample_poly(h, up, down) for h in hrir_l], axis=0)
        hrir_r = np.stack([resample_poly(h, up, down) for h in hrir_r], axis=0)
        sr = target_sr
    # Truncate / pad to n_taps
    cur = hrir_l.shape[1]
    if cur >= n_taps:
        hrir_l = hrir_l[:, :n_taps]
        hrir_r = hrir_r[:, :n_taps]
    else:
        pad = n_taps - cur
        hrir_l = np.pad(hrir_l, ((0, 0), (0, pad)))
        hrir_r = np.pad(hrir_r, ((0, 0), (0, pad)))
    return HrtfDataset(
        name=name or path.stem,
        path=path,
        sr=sr,
        azimuths=az,
        elevations=el,
        hrir_l=hrir_l,
        hrir_r=hrir_r,
    )


def apply_equalization(ds: HrtfDataset, mode: str = "diffuse") -> HrtfDataset:
    """Diffuse-field or frontal equalization (inverse of mean power spectrum)."""
    mode = mode.lower()
    if mode not in {"diffuse", "frontal", "none"}:
        raise ValueError(mode)
    out = HrtfDataset(
        name=ds.name,
        path=ds.path,
        sr=ds.sr,
        azimuths=ds.azimuths.copy(),
        elevations=ds.elevations.copy(),
        hrir_l=ds.hrir_l.copy(),
        hrir_r=ds.hrir_r.copy(),
        eq_mode=mode,
    )
    if mode == "none":
        return out
    n = out.n_taps
    nfft = 1 << (n - 1).bit_length() + 1  # >= 2n
    if mode == "diffuse":
        spec_l = np.mean(np.abs(rfft(out.hrir_l, n=nfft, axis=1)) ** 2, axis=0)
        spec_r = np.mean(np.abs(rfft(out.hrir_r, n=nfft, axis=1)) ** 2, axis=0)
    else:
        # nearest frontal
        idx = int(np.argmin(angular_distance_deg_vec(out.azimuths, out.elevations, 0.0, 0.0)))
        spec_l = np.abs(rfft(out.hrir_l[idx], n=nfft)) ** 2
        spec_r = np.abs(rfft(out.hrir_r[idx], n=nfft)) ** 2
    # Regularized inverse
    eps = 1e-3 * np.max(spec_l)
    inv_l = 1.0 / np.sqrt(np.maximum(spec_l, eps))
    inv_r = 1.0 / np.sqrt(np.maximum(spec_r, eps))
    # Normalize so DC gain ~ 1
    inv_l /= inv_l[0] if inv_l[0] > 0 else 1.0
    inv_r /= inv_r[0] if inv_r[0] > 0 else 1.0
    eq_l = irfft(inv_l, n=nfft)[:n]
    eq_r = irfft(inv_r, n=nfft)[:n]
    # Apply via frequency-domain multiply per direction
    for i in range(out.n_dirs):
        Hl = rfft(out.hrir_l[i], n=nfft) * inv_l
        Hr = rfft(out.hrir_r[i], n=nfft) * inv_r
        out.hrir_l[i] = irfft(Hl, n=nfft)[:n]
        out.hrir_r[i] = irfft(Hr, n=nfft)[:n]
    return out


def angular_distance_deg_vec(az: np.ndarray, el: np.ndarray, az0: float, el0: float) -> np.ndarray:
    az0r, el0r = np.deg2rad(az0), np.deg2rad(el0)
    azr, elr = np.deg2rad(az), np.deg2rad(el)
    x0 = np.cos(el0r) * np.cos(az0r)
    y0 = np.cos(el0r) * np.sin(az0r)
    z0 = np.sin(el0r)
    x = np.cos(elr) * np.cos(azr)
    y = np.cos(elr) * np.sin(azr)
    z = np.sin(elr)
    c = np.clip(x * x0 + y * y0 + z * z0, -1.0, 1.0)
    return np.rad2deg(np.arccos(c))


def nearest_hrir(ds: HrtfDataset, azimuth: float, elevation: float = 0.0) -> tuple[np.ndarray, np.ndarray, int]:
    d = angular_distance_deg_vec(ds.azimuths, ds.elevations, azimuth, elevation)
    idx = int(np.argmin(d))
    return ds.hrir_l[idx].copy(), ds.hrir_r[idx].copy(), idx


def interpolate_hrir(
    ds: HrtfDataset, azimuth: float, elevation: float = 0.0, k: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Inverse-distance weighted interpolation over k nearest directions."""
    d = angular_distance_deg_vec(ds.azimuths, ds.elevations, azimuth, elevation)
    idx = np.argsort(d)[:k]
    w = 1.0 / np.maximum(d[idx], 1e-3)
    w /= w.sum()
    hl = np.sum(ds.hrir_l[idx] * w[:, None], axis=0)
    hr = np.sum(ds.hrir_r[idx] * w[:, None], axis=0)
    return hl, hr


class BinauralConvolver:
    """Per-source FFT convolver with optional crossfade on IR change."""

    def __init__(self, n_taps: int, block: int):
        self.n_taps = n_taps
        self.block = block
        self.ir_l = np.zeros(n_taps)
        self.ir_r = np.zeros(n_taps)
        self._prev_l = np.zeros(n_taps)
        self._prev_r = np.zeros(n_taps)
        self._fade = 0
        self._hist_in = np.zeros(n_taps - 1)
        self._set = False

    def set_ir(self, hl: np.ndarray, hr: np.ndarray, crossfade_blocks: int = 2) -> None:
        hl = np.asarray(hl, dtype=np.float64)[: self.n_taps]
        hr = np.asarray(hr, dtype=np.float64)[: self.n_taps]
        if hl.shape[0] < self.n_taps:
            hl = np.pad(hl, (0, self.n_taps - hl.shape[0]))
            hr = np.pad(hr, (0, self.n_taps - hr.shape[0]))
        if self._set and crossfade_blocks > 0:
            self._prev_l = self.ir_l.copy()
            self._prev_r = self.ir_r.copy()
            self._fade = crossfade_blocks
        self.ir_l = hl
        self.ir_r = hr
        self._set = True

    def process(self, x: np.ndarray) -> np.ndarray:
        """Mono in -> stereo out (2, n)."""
        x = np.asarray(x, dtype=np.float64).ravel()
        n = x.shape[0]
        buf = np.concatenate([self._hist_in, x])
        self._hist_in = buf[-(self.n_taps - 1) :]
        yl = fftconvolve(buf, self.ir_l, mode="valid")
        yr = fftconvolve(buf, self.ir_r, mode="valid")
        if self._fade > 0:
            yl_p = fftconvolve(buf, self._prev_l, mode="valid")
            yr_p = fftconvolve(buf, self._prev_r, mode="valid")
            # linear fade over remaining fade blocks within this block
            alpha = 1.0 - (self._fade - 0.5) / max(self._fade, 1)
            alpha = float(np.clip(alpha, 0.0, 1.0))
            yl = (1 - alpha) * yl_p + alpha * yl
            yr = (1 - alpha) * yr_p + alpha * yr
            self._fade -= 1
        # fftconvolve valid length may be n if hist is n_taps-1
        if yl.shape[0] != n:
            # pad/trim
            out_l = np.zeros(n)
            out_r = np.zeros(n)
            m = min(n, yl.shape[0])
            out_l[:m] = yl[:m]
            out_r[:m] = yr[:m]
            return np.stack([out_l, out_r], axis=0)
        return np.stack([yl, yr], axis=0)


def render_mono_at(
    x: np.ndarray,
    ds: HrtfDataset,
    azimuth: float,
    elevation: float = 0.0,
    distance: float = 1.0,
) -> np.ndarray:
    """Offline mono -> binaural via interpolated HRIR + 1/r gain."""
    hl, hr = interpolate_hrir(ds, azimuth, elevation)
    gain = 1.0 / max(distance, 0.25)
    yl = fftconvolve(x, hl, mode="full")[: x.shape[0]] * gain
    yr = fftconvolve(x, hr, mode="full")[: x.shape[0]] * gain
    return np.stack([yl, yr], axis=0)
