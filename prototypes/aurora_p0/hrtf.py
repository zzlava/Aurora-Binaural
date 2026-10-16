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


def _log_smooth_rows(mag: np.ndarray, freqs: np.ndarray, octave: float = 1.0 / 3.0) -> np.ndarray:
    """Smooth linear magnitude along frequency, 1/N-octave moving average.

    ``mag`` is (F,) or (N, F). DC bin is copied through.
    """
    row = mag.ndim == 1
    if row:
        mag = mag[np.newaxis, :]
    f = np.maximum(freqs.astype(np.float64), 1.0)
    logf = np.log(f)
    n_f = mag.shape[1]
    grid = np.linspace(logf[1], logf[-1], n_f)
    warped = np.empty_like(mag)
    for i in range(mag.shape[0]):
        warped[i] = np.interp(grid, logf, mag[i])
    d = float(grid[1] - grid[0])
    half = max(1, int(round((np.log(2.0) * octave * 0.5) / d)))
    kernel = np.ones(2 * half + 1, dtype=np.float64) / (2 * half + 1)
    smoothed = np.empty_like(warped)
    for i in range(warped.shape[0]):
        smoothed[i] = np.convolve(warped[i], kernel, mode="same")
    out = np.empty_like(mag)
    for i in range(mag.shape[0]):
        out[i] = np.interp(logf, grid, smoothed[i])
    out[:, 0] = mag[:, 0]
    return out[0] if row else out


def _minphase_gain(gain: np.ndarray, nfft: int) -> np.ndarray:
    """Minimum-phase spectrum from a linear magnitude curve (F,) or (N, F)."""
    row = gain.ndim == 1
    if row:
        gain = gain[np.newaxis, :]
    log_g = np.log(np.maximum(gain, 1e-8))
    cep = irfft(log_g, n=nfft, axis=1)
    cep[:, 1 : nfft // 2] *= 2.0
    cep[:, nfft // 2 + 1 :] = 0.0
    spec = np.exp(rfft(cep, n=nfft, axis=1))
    return spec[0] if row else spec


def _limited_inverse(mag: np.ndarray, max_db: float = 12.0) -> np.ndarray:
    """Invert a smoothed magnitude and clamp the boost/cut. ``mag`` (F,) or (N, F)."""
    row = mag.ndim == 1
    if row:
        mag = mag[np.newaxis, :]
    # Reference is the mean magnitude over the speech/music band so the
    # correction removes color, not the absolute HRIR scale.
    ref = np.mean(mag[:, 1:], axis=1, keepdims=True)
    gain = ref / np.maximum(mag, 1e-8)
    cap = 10.0 ** (max_db / 20.0)
    gain = np.clip(gain, 1.0 / cap, cap)
    return gain[0] if row else gain


def apply_equalization(
    ds: HrtfDataset,
    mode: str = "diffuse",
    directional_power: bool = False,
    max_db: float = 12.0,
) -> HrtfDataset:
    """Diffuse-field or frontal equalization, optional per-direction power flatten.

    The inverse is a 1/3-octave-smoothed minimum-phase filter. Unsmoothed
    inverses, once truncated to the HRIR length, were the main high-frequency
    notch (around 8 kHz) in the previous prototype.
    """
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
        eq_mode=mode if not directional_power else f"{mode}+power",
    )
    n = out.n_taps
    nfft = max(2048, 1 << ((2 * n - 1).bit_length()))
    freqs = np.fft.rfftfreq(nfft, d=1.0 / out.sr)
    if mode != "none":
        Hl = rfft(out.hrir_l, n=nfft, axis=1)
        Hr = rfft(out.hrir_r, n=nfft, axis=1)
        if mode == "diffuse":
            power = np.mean(0.5 * (np.abs(Hl) ** 2 + np.abs(Hr) ** 2), axis=0)
        else:
            idx = int(np.argmin(angular_distance_deg_vec(out.azimuths, out.elevations, 0.0, 0.0)))
            power = 0.5 * (np.abs(Hl[idx]) ** 2 + np.abs(Hr[idx]) ** 2)
        mag = _log_smooth_rows(np.sqrt(np.maximum(power, 0.0)), freqs, octave=1.0 / 3.0)
        gain = _limited_inverse(mag, max_db=max_db)
        # Leave the sub-crossover region alone so the low bypass stays flat.
        below = freqs < 80.0
        gain = np.asarray(gain, dtype=np.float64).copy()
        gain[below] = 1.0
        h_eq = _minphase_gain(gain, nfft)
        Hl = Hl * h_eq
        Hr = Hr * h_eq
        out.hrir_l = irfft(Hl, n=nfft, axis=1)[:, :n]
        out.hrir_r = irfft(Hr, n=nfft, axis=1)[:, :n]
    if directional_power:
        # Second cap is smaller so it cannot stack with the global inverse
        # into a 20 dB+ high-frequency boost.
        out = _normalize_directional_power(out, nfft=nfft, max_db=min(6.0, max_db))
        out.eq_mode = f"{mode}+power"
    return out


def _normalize_directional_power(ds: HrtfDataset, nfft: int = 2048, max_db: float = 12.0) -> HrtfDataset:
    """Flatten each direction's 1/3-octave binaural power sum. ILD and phase stay."""
    freqs = np.fft.rfftfreq(nfft, d=1.0 / ds.sr)
    Hl = rfft(ds.hrir_l, n=nfft, axis=1)
    Hr = rfft(ds.hrir_r, n=nfft, axis=1)
    mag = np.sqrt(np.maximum(0.5 * (np.abs(Hl) ** 2 + np.abs(Hr) ** 2), 0.0))
    smooth = _log_smooth_rows(mag, freqs, octave=1.0 / 3.0)
    gain = _limited_inverse(smooth, max_db=max_db)
    below = freqs < 80.0
    gain = np.asarray(gain, dtype=np.float64).copy()
    gain[:, below] = 1.0
    h_eq = _minphase_gain(gain, nfft)
    ds.hrir_l = irfft(Hl * h_eq, n=nfft, axis=1)[:, : ds.n_taps]
    ds.hrir_r = irfft(Hr * h_eq, n=nfft, axis=1)[:, : ds.n_taps]
    return ds


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
