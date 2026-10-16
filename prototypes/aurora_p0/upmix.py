"""Streaming STFT direct/ambient upmix.

Classification uses the short-time smoothed magnitude of the normalized
cross-spectrum (coherence). The left/right level ratio is used only to
estimate the direct-path azimuth. Hop length is fixed so an outer block
size of 64 / 256 / 1024 produces the same samples.
"""

from __future__ import annotations

import numpy as np
from scipy.fft import irfft, rfft
from scipy.signal import lfilter


N_FFT = 1024
HOP = 256


def _periodic_hann(n: int) -> np.ndarray:
    k = np.arange(n, dtype=np.float64)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * k / n)


class StreamingUpmixer:
    """Fixed-hop streaming upmix. ``process`` returns ``len(input)`` samples.

    Output is delayed by ``latency`` (``n_fft``) samples. State (overlap-add,
    coherence smoother, mask slew) carries across calls.
    """

    def __init__(
        self,
        sr: int,
        width_deg: float = 30.0,
        ambient_mix: float = 1.0,
        n_fft: int = N_FFT,
        hop: int = HOP,
        n_sectors: int = 5,
        tau_s: float = 0.040,
        wide_deg: float = 90.0,
        rear_deg: float = 130.0,
    ):
        if hop <= 0 or n_fft < hop or (n_fft % hop) != 0:
            raise ValueError(f"n_fft {n_fft} must be a multiple of hop {hop}")
        self.sr = int(sr)
        self.n_fft = int(n_fft)
        self.hop = int(hop)
        self.latency = int(n_fft)
        self.width_deg = float(max(width_deg, 0.0))
        self.ambient_mix = float(np.clip(ambient_mix, 0.0, 1.0))
        self.wide_deg = float(wide_deg)
        self.rear_deg = float(rear_deg)
        self.hann = _periodic_hann(self.n_fft)
        self.aw = np.sqrt(self.hann)
        self.alpha = float(1.0 - np.exp(-self.hop / (tau_s * self.sr)))
        if self.width_deg < 5.0:
            self.sector_az = np.array([0.0], dtype=np.float64)
        else:
            self.sector_az = np.linspace(-self.width_deg, self.width_deg, int(n_sectors))
        self.n_sectors = int(self.sector_az.shape[0])
        # acc layout: dir_l, dir_r, wide_l, wide_r, amb_l, amb_r, sectors...
        self.n_stems = 6 + self.n_sectors
        nbin = self.n_fft // 2 + 1
        self.Gll = np.zeros(nbin, dtype=np.float64)
        self.Grr = np.zeros(nbin, dtype=np.float64)
        self.Glr = np.zeros(nbin, dtype=np.complex128)
        self._prev_mdir: np.ndarray | None = None
        self._in_l = np.zeros(0, dtype=np.float64)
        self._in_r = np.zeros(0, dtype=np.float64)
        self._in_origin = 0
        self._consumed = 0
        self._frame = 0
        self._acc = np.zeros((self.n_stems, 0), dtype=np.float64)
        self._w = np.zeros(0, dtype=np.float64)
        self._acc_origin = 0
        self._read = 0
        self.coherence_sum = 0.0
        self.coherence_n = 0

    def process(self, left: np.ndarray, right: np.ndarray) -> dict[str, np.ndarray]:
        left = np.asarray(left, dtype=np.float64).ravel()
        right = np.asarray(right, dtype=np.float64).ravel()
        n = int(left.shape[0])
        if right.shape[0] != n:
            raise ValueError("stereo block length mismatch")
        self._append_input(left, right)
        self._consumed += n
        self._analyze()
        stems = self._emit(n)
        self._compact()
        return self._pack(stems)

    @property
    def coherence_mean(self) -> float:
        if self.coherence_n == 0:
            return float("nan")
        return float(self.coherence_sum / self.coherence_n)

    def _append_input(self, left: np.ndarray, right: np.ndarray) -> None:
        self._in_l = np.concatenate([self._in_l, left])
        self._in_r = np.concatenate([self._in_r, right])

    def _analyze(self) -> None:
        while self._consumed >= self._frame * self.hop + self.n_fft:
            start = self._frame * self.hop
            local = start - self._in_origin
            fl = self._in_l[local : local + self.n_fft] * self.aw
            fr = self._in_r[local : local + self.n_fft] * self.aw
            spec_l = rfft(fl)
            spec_r = rfft(fr)
            phi, g = self._coherence_and_pan(spec_l, spec_r)
            m_dir, m_wide, m_amb = self._masks(phi)
            pieces = []
            pieces.append(self._synth(spec_l * m_dir))
            pieces.append(self._synth(spec_r * m_dir))
            pieces.append(self._synth(spec_l * m_wide))
            pieces.append(self._synth(spec_r * m_wide))
            pieces.append(self._synth(spec_l * m_amb))
            pieces.append(self._synth(spec_r * m_amb))
            mono = _mono_spectrum(spec_l * m_dir, spec_r * m_dir)
            weights = _sector_weights(g, self.sector_az, self.width_deg)
            for s in range(self.n_sectors):
                pieces.append(self._synth(mono * weights[:, s]))
            stacked = np.stack(pieces, axis=0)
            self._overlap(start, stacked)
            self.coherence_sum += float(np.mean(phi))
            self.coherence_n += 1
            self._frame += 1

    def _synth(self, spec: np.ndarray) -> np.ndarray:
        return irfft(spec, n=self.n_fft) * self.aw

    def _coherence_and_pan(self, spec_l: np.ndarray, spec_r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        a = self.alpha
        p_l = _smooth_bins(np.abs(spec_l) ** 2, radius=3)
        p_r = _smooth_bins(np.abs(spec_r) ** 2, radius=3)
        cross = spec_l * np.conj(spec_r)
        c_r = _smooth_bins(cross.real, radius=3)
        c_i = _smooth_bins(cross.imag, radius=3)
        self.Gll = (1.0 - a) * self.Gll + a * p_l
        self.Grr = (1.0 - a) * self.Grr + a * p_r
        self.Glr = (1.0 - a) * self.Glr + a * (c_r + 1j * c_i)
        den = np.sqrt(np.maximum(self.Gll * self.Grr, 1e-20))
        phi = np.clip(np.abs(self.Glr) / den, 0.0, 1.0)
        g = (self.Gll - self.Grr) / np.maximum(self.Gll + self.Grr, 1e-20)
        return phi, np.clip(g, -1.0, 1.0)

    def _masks(self, phi: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        # Single-frame coherence is identically 1; the smoother's noise floor
        # for uncorrelated bins sits near ~0.2. Gate it, then square so only
        # clearly coherent bins become direct.
        m_dir = np.clip((phi - 0.22) / 0.78, 0.0, 1.0) ** 2
        if self._prev_mdir is None:
            self._prev_mdir = m_dir.copy()
        else:
            delta = np.clip(m_dir - self._prev_mdir, -0.35, 0.35)
            m_dir = self._prev_mdir + delta
            self._prev_mdir = m_dir
        incoh = 1.0 - m_dir
        m_amb = incoh * self.ambient_mix
        m_wide = incoh * (1.0 - self.ambient_mix)
        return m_dir, m_wide, m_amb

    def _overlap(self, start: int, frames: np.ndarray) -> None:
        end = start + self.n_fft
        self._ensure_acc(end)
        local = start - self._acc_origin
        self._acc[:, local : local + self.n_fft] += frames
        self._w[local : local + self.n_fft] += self.hann

    def _ensure_acc(self, abs_end: int) -> None:
        if self._acc.shape[1] == 0:
            self._acc_origin = 0
            self._acc = np.zeros((self.n_stems, abs_end + self.n_fft), dtype=np.float64)
            self._w = np.zeros(abs_end + self.n_fft, dtype=np.float64)
            return
        have_end = self._acc_origin + self._acc.shape[1]
        if abs_end <= have_end:
            return
        extra = abs_end - have_end + self.n_fft
        self._acc = np.concatenate([self._acc, np.zeros((self.n_stems, extra))], axis=1)
        self._w = np.concatenate([self._w, np.zeros(extra)])

    def _emit(self, n: int) -> np.ndarray:
        out = np.zeros((self.n_stems, n), dtype=np.float64)
        for i in range(n):
            j = self._read + i - self.latency
            if j < 0:
                continue
            local = j - self._acc_origin
            if local < 0 or local >= self._w.shape[0]:
                continue
            w = self._w[local]
            if w > 1e-12:
                out[:, i] = self._acc[:, local] / w
        self._read += n
        return out

    def _compact(self) -> None:
        keep_in = self._frame * self.hop
        drop_in = keep_in - self._in_origin
        if drop_in > self.hop:
            self._in_l = self._in_l[drop_in:]
            self._in_r = self._in_r[drop_in:]
            self._in_origin = keep_in
        keep_acc = max(0, self._read - self.latency - self.n_fft)
        drop_acc = keep_acc - self._acc_origin
        if drop_acc > self.n_fft and drop_acc < self._w.shape[0]:
            self._acc = self._acc[:, drop_acc:]
            self._w = self._w[drop_acc:]
            self._acc_origin = keep_acc

    def _pack(self, stems: np.ndarray) -> dict[str, np.ndarray]:
        return {
            "direct_l": stems[0],
            "direct_r": stems[1],
            "wide_l": stems[2],
            "wide_r": stems[3],
            "ambient_l": stems[4],
            "ambient_r": stems[5],
            "sectors": stems[6:],
            "sector_azimuths": self.sector_az.copy(),
        }


def streaming_upmix_file(
    left: np.ndarray,
    right: np.ndarray,
    sr: int,
    block: int,
    width_deg: float = 30.0,
    ambient_mix: float = 1.0,
    **kwargs,
) -> dict[str, np.ndarray | float]:
    """Run the streaming upmixer over ``left``/``right`` in fixed-size blocks."""
    left = np.asarray(left, dtype=np.float64).ravel()
    right = np.asarray(right, dtype=np.float64).ravel()
    n = min(left.shape[0], right.shape[0])
    left, right = left[:n], right[:n]
    up = StreamingUpmixer(sr, width_deg=width_deg, ambient_mix=ambient_mix, **kwargs)
    parts: list[dict[str, np.ndarray]] = []
    for start in range(0, n, block):
        end = min(start + block, n)
        parts.append(up.process(left[start:end], right[start:end]))
    sectors = np.concatenate([p["sectors"] for p in parts], axis=1)
    out: dict[str, np.ndarray | float] = {
        "direct_l": np.concatenate([p["direct_l"] for p in parts]),
        "direct_r": np.concatenate([p["direct_r"] for p in parts]),
        "wide_l": np.concatenate([p["wide_l"] for p in parts]),
        "wide_r": np.concatenate([p["wide_r"] for p in parts]),
        "ambient_l": np.concatenate([p["ambient_l"] for p in parts]),
        "ambient_r": np.concatenate([p["ambient_r"] for p in parts]),
        "sectors": sectors,
        "sector_azimuths": parts[0]["sector_azimuths"],
        "latency": up.latency,
        "coherence_mean": up.coherence_mean,
        "wide_deg": up.wide_deg,
        "rear_deg": up.rear_deg,
    }
    return out


def decorrelate(x: np.ndarray, sr: int, seed: int, n_ap: int = 4) -> np.ndarray:
    """Seeded cascaded allpass. Magnitude response is unity."""
    rng = np.random.default_rng(seed)
    y = np.asarray(x, dtype=np.float64).copy()
    for _ in range(n_ap):
        d = int(rng.integers(int(0.002 * sr), int(0.011 * sr)))
        g = float(rng.uniform(0.4, 0.7))
        b = np.zeros(d + 1)
        a = np.zeros(d + 1)
        b[0] = -g
        b[d] = 1.0
        a[0] = 1.0
        a[d] = -g
        y = lfilter(b, a, y)
    return y


def energy_conservation_error(left: np.ndarray, right: np.ndarray, up: dict, latency: int | None = None) -> float:
    """Power error of direct+wide+ambient vs input, after the streaming delay."""
    recon_l = np.asarray(up["direct_l"]) + np.asarray(up["wide_l"]) + np.asarray(up["ambient_l"])
    recon_r = np.asarray(up["direct_r"]) + np.asarray(up["wide_r"]) + np.asarray(up["ambient_r"])
    lag = int(up.get("latency", latency or 0))
    n = min(left.shape[0], right.shape[0], recon_l.shape[0])
    # Compare the aligned region; the first ``lag`` output samples are the delay.
    if lag >= n - 8:
        return float("nan")
    err = np.mean((recon_l[lag:n] - left[: n - lag]) ** 2 + (recon_r[lag:n] - right[: n - lag]) ** 2)
    ref = np.mean(left[: n - lag] ** 2 + right[: n - lag] ** 2) + 1e-20
    return float(10.0 * np.log10(err / ref + 1e-30))


def _smooth_bins(x: np.ndarray, radius: int = 3) -> np.ndarray:
    if radius <= 0:
        return x
    kernel = np.ones(2 * radius + 1, dtype=np.float64) / (2 * radius + 1)
    return np.convolve(x, kernel, mode="same")


def _mono_spectrum(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    power = np.abs(left) ** 2 + np.abs(right) ** 2
    mix = left + right
    phase = np.ones_like(mix)
    nz = np.abs(mix) > 1e-20
    phase[nz] = mix[nz] / np.abs(mix[nz])
    return np.sqrt(power) * phase


def _sector_weights(g: np.ndarray, centers: np.ndarray, width_deg: float) -> np.ndarray:
    """Constant-power weights (n_bins, n_sectors) from pan index g in [-1, 1]."""
    nbin = g.shape[0]
    n_sec = centers.shape[0]
    weights = np.zeros((nbin, n_sec), dtype=np.float64)
    if n_sec == 1 or width_deg < 5.0:
        weights[:, 0] = 1.0
        return weights
    az = g * width_deg
    span = centers[-1] - centers[0]
    pos = (az - centers[0]) / (span / (n_sec - 1))
    i0 = np.clip(np.floor(pos).astype(int), 0, n_sec - 2)
    frac = np.clip(pos - i0, 0.0, 1.0)
    w0 = np.cos(frac * np.pi / 2.0)
    w1 = np.sin(frac * np.pi / 2.0)
    rows = np.arange(nbin)
    weights[rows, i0] += w0
    weights[rows, i0 + 1] += w1
    return weights
