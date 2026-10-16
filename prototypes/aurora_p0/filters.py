"""Complementary Linkwitz–Riley crossover and utility filters.

LR4 is two cascaded Butterworth 2nd-order sections. A single Butterworth of
order 4 is −3 dB at the cutoff on each side and the in-phase sum peaks about
+3 dB there, so it is not a complementary pair.

Initial state is rest (zeros). ``sosfilt_zi`` is the steady state of a unit
step; seeding the lowpass with it makes the output start at 1.0 and decay,
which showed up as +17 to +22 dB in the 40–63 Hz bands of a whole-file
spectrum.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfreqz


def linkwitz_riley4_sos(cutoff_hz: float, sr: int, kind: str) -> np.ndarray:
    """LR4 lowpass or highpass as cascaded Butterworth SOS (4 sections total)."""
    if kind not in {"low", "high"}:
        raise ValueError(kind)
    half = butter(2, cutoff_hz, btype=kind, fs=sr, output="sos")
    return np.vstack([half, half])


def complementary_crossover(
    x: np.ndarray,
    sr: int,
    cutoff_hz: float = 150.0,
    order: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """Split into low / high with an LR4 pair. ``low + high`` is allpass.

    ``order`` is accepted for callers and must stay 4 (LR4).
    """
    if order != 4:
        raise ValueError("P0 crossover is Linkwitz–Riley 4 (order=4)")
    if x.ndim == 1:
        x = x[np.newaxis, :]
    sos_lp = linkwitz_riley4_sos(cutoff_hz, sr, "low")
    sos_hp = linkwitz_riley4_sos(cutoff_hz, sr, "high")
    low = np.zeros_like(x, dtype=np.float64)
    high = np.zeros_like(x, dtype=np.float64)
    for c in range(x.shape[0]):
        low[c] = sosfilt(sos_lp, x[c])
        high[c] = sosfilt(sos_hp, x[c])
    return low, high


def crossover_magnitude_db(
    sr: int,
    cutoff_hz: float = 150.0,
    f_lo: float = 20.0,
    f_hi: float = 20_000.0,
    n_points: int = 4096,
) -> dict:
    """Magnitude of LR4 low+high relative to 0 dB, from ``f_lo`` to ``f_hi``."""
    sos_lp = linkwitz_riley4_sos(cutoff_hz, sr, "low")
    sos_hp = linkwitz_riley4_sos(cutoff_hz, sr, "high")
    freqs = np.geomspace(f_lo, min(f_hi, sr * 0.49), n_points)
    _, h_lp = sosfreqz(sos_lp, worN=freqs, fs=sr)
    _, h_hp = sosfreqz(sos_hp, worN=freqs, fs=sr)
    db = 20.0 * np.log10(np.maximum(np.abs(h_lp + h_hp), 1e-20))
    return {
        "max_abs_db": float(np.max(np.abs(db))),
        "within_pm_0_5db": bool(np.max(np.abs(db)) <= 0.5),
        "at_cutoff_db": float(db[int(np.argmin(np.abs(freqs - cutoff_hz)))]),
    }


class BlockCrossover:
    """Stateful LR4 crossover for block processing. Rest initial conditions."""

    def __init__(self, n_ch: int, sr: int, cutoff_hz: float = 150.0, order: int = 4):
        if order != 4:
            raise ValueError("P0 crossover is Linkwitz–Riley 4 (order=4)")
        self.n_ch = n_ch
        self.sr = sr
        self.cutoff_hz = cutoff_hz
        self.sos_lp = linkwitz_riley4_sos(cutoff_hz, sr, "low")
        self.sos_hp = linkwitz_riley4_sos(cutoff_hz, sr, "high")
        self.zi_lp = np.zeros((n_ch, self.sos_lp.shape[0], 2), dtype=np.float64)
        self.zi_hp = np.zeros((n_ch, self.sos_hp.shape[0], 2), dtype=np.float64)

    def reset(self) -> None:
        self.zi_lp.fill(0.0)
        self.zi_hp.fill(0.0)

    def process(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """x: (n_ch, n) -> low, high."""
        if x.ndim == 1:
            x = x[np.newaxis, :]
        n_ch, n = x.shape
        low = np.zeros((n_ch, n), dtype=np.float64)
        high = np.zeros((n_ch, n), dtype=np.float64)
        for c in range(n_ch):
            low[c], self.zi_lp[c] = sosfilt(self.sos_lp, x[c], zi=self.zi_lp[c])
            high[c], self.zi_hp[c] = sosfilt(self.sos_hp, x[c], zi=self.zi_hp[c])
        return low, high


def delay_buffer(x: np.ndarray, latency: int) -> np.ndarray:
    """Integer delay. Drops the tail and inserts zeros. ``latency <= 0`` is a copy."""
    if latency <= 0:
        return np.array(x, dtype=np.float64, copy=True)
    y = np.zeros_like(x, dtype=np.float64)
    if x.ndim == 1:
        if latency < x.shape[0]:
            y[latency:] = x[:-latency]
        return y
    if latency < x.shape[1]:
        y[:, latency:] = x[:, :-latency]
    return y


def one_pole_lowpass(x: np.ndarray, coeff: float, state: float = 0.0) -> tuple[np.ndarray, float]:
    y = np.empty_like(x)
    s = state
    for i, v in enumerate(x):
        s = s + coeff * (v - s)
        y[i] = s
    return y, s
