"""Shoebox early reflections + FDN late reverb (seeded RNG)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import fftconvolve


@dataclass
class RoomParams:
    size: tuple[float, float, float] = (4.0, 5.0, 2.8)
    listener: tuple[float, float, float] = (2.0, 2.5, 1.6)
    absorption: float = 0.25
    early_level_db: float = -12.0
    t60: float = 0.35
    max_order: int = 1
    n_fdn: int = 8
    seed: int = 20261016


def _image_sources(
    source: np.ndarray,
    listener: np.ndarray,
    size: np.ndarray,
    max_order: int,
    absorption: float,
    c: float = 343.0,
) -> list[tuple[float, float, float, float]]:
    Lx, Ly, Lz = size
    out: list[tuple[float, float, float, float]] = []
    for nx in range(-max_order, max_order + 1):
        for ny in range(-max_order, max_order + 1):
            for nz in range(-max_order, max_order + 1):
                if nx == 0 and ny == 0 and nz == 0:
                    continue
                sx = (2 * nx * Lx) + (source[0] if nx % 2 == 0 else 2 * Lx - source[0])
                sy = (2 * ny * Ly) + (source[1] if ny % 2 == 0 else 2 * Ly - source[1])
                sz = (2 * nz * Lz) + (source[2] if nz % 2 == 0 else 2 * Lz - source[2])
                vec = np.array([sx, sy, sz], dtype=np.float64) - listener
                dist = float(np.linalg.norm(vec))
                if dist < 1e-6:
                    continue
                n_walls = abs(nx) + abs(ny) + abs(nz)
                gain = ((1.0 - absorption) ** n_walls) / dist
                delay = dist / c
                az = float(np.rad2deg(np.arctan2(vec[1], vec[0])))
                el = float(np.rad2deg(np.arcsin(np.clip(vec[2] / dist, -1.0, 1.0))))
                out.append((delay, gain, az, el))
    out.sort(key=lambda t: t[0])
    return out[:12]


def shoebox_image_taps(
    source: np.ndarray,
    listener: np.ndarray,
    size: np.ndarray,
    max_order: int = 1,
    absorption: float = 0.22,
    n_keep: int = 12,
    c: float = 343.0,
) -> list[tuple[float, float, float, float]]:
    """Allen & Berkley image sources for route V.

    Route B keeps ``_image_sources``. This one uses the even/odd formula
    ``n*L + src`` / ``n*L + (L - src)``, so the near wall, the far wall,
    the ceiling and the floor each produce a first-order tap.

    Returns the earliest ``n_keep`` taps, after reserving the highest
    ceiling tap and the lowest floor tap. Each item is
    ``(delay_s, gain, azimuth_deg, elevation_deg)``. Gain is
    ``(1 - absorption) ** bounces / distance``.
    """
    source = np.asarray(source, dtype=np.float64)
    listener = np.asarray(listener, dtype=np.float64)
    size = np.asarray(size, dtype=np.float64)
    out: list[tuple[float, float, float, float]] = []
    for nx in range(-max_order, max_order + 1):
        for ny in range(-max_order, max_order + 1):
            for nz in range(-max_order, max_order + 1):
                if nx == 0 and ny == 0 and nz == 0:
                    continue
                image = np.array(
                    [
                        _image_coord(source[0], nx, size[0]),
                        _image_coord(source[1], ny, size[1]),
                        _image_coord(source[2], nz, size[2]),
                    ],
                    dtype=np.float64,
                )
                vec = image - listener
                dist = float(np.linalg.norm(vec))
                if dist < 1e-6:
                    continue
                bounces = abs(nx) + abs(ny) + abs(nz)
                gain = ((1.0 - absorption) ** bounces) / dist
                delay = dist / c
                az = float(np.rad2deg(np.arctan2(vec[1], vec[0])))
                el = float(np.rad2deg(np.arcsin(np.clip(vec[2] / dist, -1.0, 1.0))))
                out.append((delay, gain, az, el))
    out.sort(key=lambda t: t[0])
    return _reserve_vertical(out, n_keep)


def _image_coord(src: float, n: int, length: float) -> float:
    if n % 2 == 0:
        return n * length + src
    return n * length + (length - src)


def _reserve_vertical(
    taps: list[tuple[float, float, float, float]], n_keep: int
) -> list[tuple[float, float, float, float]]:
    if n_keep <= 0 or len(taps) <= n_keep:
        return taps
    ceiling = max(taps, key=lambda t: t[3])
    floor = min(taps, key=lambda t: t[3])
    chosen: list[tuple[float, float, float, float]] = []
    for tap in (ceiling, floor):
        if abs(tap[3]) >= 20.0 and tap not in chosen:
            chosen.append(tap)
    for tap in taps:
        if tap in chosen:
            continue
        chosen.append(tap)
        if len(chosen) >= n_keep:
            break
    chosen.sort(key=lambda t: t[0])
    return chosen


class EarlyReflections:
    def __init__(
        self,
        sr: int,
        params: RoomParams,
        source_pos: tuple[float, float, float] | None = None,
    ):
        self.sr = sr
        self.params = params
        self.level = 10 ** (params.early_level_db / 20.0)
        src = np.array(
            source_pos or (params.listener[0] + 1.5, params.listener[1], params.listener[2]),
            dtype=np.float64,
        )
        lis = np.array(params.listener, dtype=np.float64)
        size = np.array(params.size, dtype=np.float64)
        self.taps = _image_sources(src, lis, size, params.max_order, params.absorption)
        max_delay = int(max((t[0] for t in self.taps), default=0.05) * sr) + 1
        self._hist = np.zeros(max(max_delay, 1), dtype=np.float64)

    def reset(self) -> None:
        self._hist.fill(0.0)

    def process(self, x: np.ndarray) -> tuple[np.ndarray, list[tuple[np.ndarray, float, float]]]:
        x = np.asarray(x, dtype=np.float64).ravel()
        n = x.shape[0]
        hist = np.concatenate([self._hist, x])
        self._hist = hist[-self._hist.shape[0] :].copy()
        offset = hist.shape[0] - n
        wet = np.zeros(n, dtype=np.float64)
        directed: list[tuple[np.ndarray, float, float]] = []
        for delay_s, gain, az, el in self.taps:
            d = int(round(delay_s * self.sr))
            sig = np.zeros(n, dtype=np.float64)
            src_idx = offset + np.arange(n) - d
            valid = src_idx >= 0
            sig[valid] = hist[src_idx[valid]] * gain * self.level
            wet += sig
            directed.append((sig, az, el))
        return wet, directed


def _run_fdn_stream(x: np.ndarray, sr: int, params: RoomParams) -> np.ndarray:
    """Reference sample-wise FDN (used to bake IR)."""
    n_lines = params.n_fdn
    rng = np.random.default_rng(params.seed)
    delays_ms = np.sort(rng.uniform(20.0, 85.0, size=n_lines))
    delays = np.maximum(np.round(delays_ms * 1e-3 * sr).astype(int), 2)
    for i in range(1, n_lines):
        if delays[i] <= delays[i - 1]:
            delays[i] = delays[i - 1] + 1
    bufs = [np.zeros(int(d), dtype=np.float64) for d in delays]
    idx = [0 for _ in range(n_lines)]
    m = rng.standard_normal((n_lines, n_lines))
    q, _ = np.linalg.qr(m)
    feedback = q
    gains = np.array([10 ** (-3.0 * d / (params.t60 * sr)) for d in delays])
    b_l = rng.standard_normal(n_lines)
    b_r = rng.standard_normal(n_lines)
    b_l /= np.linalg.norm(b_l) + 1e-12
    b_r /= np.linalg.norm(b_r) + 1e-12
    yl = np.zeros(x.shape[0])
    yr = np.zeros(x.shape[0])
    lp_l = lp_r = 0.0
    # One-pole brightness. a=0.35 rolled off near 3 kHz and, after loudness
    # match, pulled 4–16 kHz down. ~0.82 keeps the tail near 12 kHz.
    a_l, a_r = 0.82, 0.80
    for i in range(x.shape[0]):
        taps = np.array([bufs[k][idx[k]] for k in range(n_lines)])
        yl[i] = (lp_l := (1 - a_l) * lp_l + a_l * float(taps @ b_l))
        yr[i] = (lp_r := (1 - a_r) * lp_r + a_r * float(taps @ b_r))
        fb = feedback @ (taps * gains)
        inj = x[i] / n_lines
        for k in range(n_lines):
            bufs[k][idx[k]] = fb[k] + inj
            idx[k] = (idx[k] + 1) % bufs[k].shape[0]
    return np.stack([yl, yr], axis=0) * 0.12


class FDNReverb:
    """Seeded FDN realized as baked stereo IR + partitioned-friendly convolution.

    Baking uses the explicit sample-wise FDN with the given seed, so all randomness
    is seed-derived. Applying the IR via FFT convolution is LTI and therefore
    block-size consistent.
    """

    def __init__(self, sr: int, params: RoomParams, ir_len: int | None = None, ir: np.ndarray | None = None):
        self.sr = sr
        self.params = params
        self.level = 1.0  # already in baked IR
        if ir is not None:
            self.ir = np.asarray(ir, dtype=np.float64)
        else:
            ir_len = ir_len or int(min(max(params.t60 * 2.0, 0.2), 0.45) * sr)
            impulse = np.zeros(ir_len, dtype=np.float64)
            impulse[0] = 1.0
            self.ir = _run_fdn_stream(impulse, sr, params)  # (2, ir_len)
        self._hist = np.zeros((2, self.ir.shape[1] - 1), dtype=np.float64)

    def reset(self) -> None:
        self._hist.fill(0.0)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Mono in -> stereo out, stateful across blocks (overlap-add tail)."""
        x = np.asarray(x, dtype=np.float64).ravel()
        n = x.shape[0]
        yl = fftconvolve(x, self.ir[0], mode="full")
        yr = fftconvolve(x, self.ir[1], mode="full")
        tail_n = self._hist.shape[1]
        yl[:tail_n] += self._hist[0]
        yr[:tail_n] += self._hist[1]
        out = np.stack([yl[:n], yr[:n]], axis=0)
        new_tail_l = yl[n : n + tail_n]
        new_tail_r = yr[n : n + tail_n]
        self._hist[0] = np.pad(new_tail_l, (0, max(0, tail_n - len(new_tail_l))))[:tail_n]
        self._hist[1] = np.pad(new_tail_r, (0, max(0, tail_n - len(new_tail_r))))[:tail_n]
        return out
