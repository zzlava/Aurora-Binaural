"""Complementary crossover and utility filters."""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi


def complementary_crossover(
    x: np.ndarray,
    sr: int,
    cutoff_hz: float = 150.0,
    order: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """Split signal into low / high bands with complementary Butterworth SOS.

    Uses cascaded lowpass / highpass of the same order and cutoff so that
    low + high approximately reconstructs the input (Linkwitz–Riley–like
    complementary pair via matching butterworth halves).
    """
    if x.ndim == 1:
        x = x[np.newaxis, :]
    n_ch, n = x.shape
    sos_lp = butter(order, cutoff_hz, btype="low", fs=sr, output="sos")
    sos_hp = butter(order, cutoff_hz, btype="high", fs=sr, output="sos")
    low = np.zeros_like(x)
    high = np.zeros_like(x)
    for c in range(n_ch):
        low[c] = sosfilt(sos_lp, x[c])
        high[c] = sosfilt(sos_hp, x[c])
    return low, high


class BlockCrossover:
    """Stateful complementary crossover for block processing."""

    def __init__(self, n_ch: int, sr: int, cutoff_hz: float = 150.0, order: int = 4):
        self.n_ch = n_ch
        self.sos_lp = butter(order, cutoff_hz, btype="low", fs=sr, output="sos")
        self.sos_hp = butter(order, cutoff_hz, btype="high", fs=sr, output="sos")
        self.zi_lp = np.tile(sosfilt_zi(self.sos_lp), (n_ch, 1, 1))
        self.zi_hp = np.tile(sosfilt_zi(self.sos_hp), (n_ch, 1, 1))

    def reset(self) -> None:
        self.zi_lp = np.tile(sosfilt_zi(self.sos_lp), (self.n_ch, 1, 1))
        self.zi_hp = np.tile(sosfilt_zi(self.sos_hp), (self.n_ch, 1, 1))

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


def one_pole_lowpass(x: np.ndarray, coeff: float, state: float = 0.0) -> tuple[np.ndarray, float]:
    y = np.empty_like(x)
    s = state
    for i, v in enumerate(x):
        s = s + coeff * (v - s)
        y[i] = s
    return y, s
