"""Block-processing render engine for routes A / B / C."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

from . import CROSSOVER_HZ, SAMPLE_RATE
from .ambisonics import MagLSDecoder, encode_plane_wave, encode_stereo_ambient, rotate_yaw
from .filters import BlockCrossover
from .hrtf import HrtfDataset, apply_equalization, interpolate_hrir, load_sofa
from .room import EarlyReflections, FDNReverb, RoomParams
from .strength import StrengthParams, map_strength
from .upmix import decorrelate, stereo_upmix

# Process-wide caches (seeded / path-keyed) to keep the sample matrix tractable
_HRTF_CACHE: dict[tuple[str, str], HrtfDataset] = {}
_FDN_IR_CACHE: dict[tuple[int, float, int], np.ndarray] = {}
_MAGLS_CACHE: dict[tuple[str, str], MagLSDecoder] = {}


def _get_hrtf(path: Path, name: str, eq_mode: str, sr: int) -> HrtfDataset:
    key = (str(path.resolve()), eq_mode)
    if key not in _HRTF_CACHE:
        raw = load_sofa(path, name=name, target_sr=sr, n_taps=256)
        _HRTF_CACHE[key] = apply_equalization(raw, mode=eq_mode)
    return _HRTF_CACHE[key]


@dataclass
class RenderConfig:
    route: str  # "A" | "B" | "C"
    strength: float = 0.4
    preset: str = "room"
    hrtf_path: Path | None = None
    hrtf_name: str = "thk_ku100"
    eq_mode: str = "diffuse"
    seed: int = 20261016
    block_size: int = 512
    sample_rate: int = SAMPLE_RATE
    crossover_hz: float = CROSSOVER_HZ
    orbit_deg_per_sec: float = 0.0


class Processor:
    """Python stand-in for the planned Rust Processor trait (plan §7)."""

    def __init__(self):
        self.cfg: RenderConfig | None = None
        self.ds: HrtfDataset | None = None
        self.params: StrengthParams | None = None
        self.xo: BlockCrossover | None = None
        self.er: EarlyReflections | None = None
        self.fdn: FDNReverb | None = None
        self.magls: MagLSDecoder | None = None
        self._latency = 0
        self._tail = 0
        self._hrir_cache: dict[tuple[float, float], tuple[np.ndarray, np.ndarray]] = {}

    def prepare(self, sample_rate: int, max_block: int, config: RenderConfig) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"P0 prototype fixed at {SAMPLE_RATE} Hz, got {sample_rate}")
        self.cfg = config
        if config.hrtf_path is None:
            raise FileNotFoundError("hrtf_path required; no synthetic fallback")
        self.ds = _get_hrtf(config.hrtf_path, config.hrtf_name, config.eq_mode, sample_rate)
        self.params = map_strength(config.strength, config.preset)
        self.xo = BlockCrossover(2, sample_rate, config.crossover_hz)
        t60 = 0.35 if config.preset != "surround" else 0.55
        room = RoomParams(seed=config.seed, t60=t60)
        self.er = EarlyReflections(sample_rate, room)
        self.fdn = FDNReverb(sample_rate, room)
        fdn_key = (config.seed, t60, sample_rate)
        if fdn_key in _FDN_IR_CACHE:
            self.fdn.ir = _FDN_IR_CACHE[fdn_key]
            self.fdn._hist = np.zeros((2, self.fdn.ir.shape[1] - 1), dtype=np.float64)
        else:
            _FDN_IR_CACHE[fdn_key] = self.fdn.ir.copy()
        if config.route.upper() == "C":
            mkey = (str(Path(config.hrtf_path).resolve()), config.eq_mode)
            if mkey not in _MAGLS_CACHE:
                _MAGLS_CACHE[mkey] = MagLSDecoder(self.ds, order=3)
            self.magls = _MAGLS_CACHE[mkey]
        else:
            self.magls = None
        self._latency = 0
        self._tail = int(0.2 * sample_rate)
        self._hrir_cache.clear()

    def set_params(self, params: StrengthParams) -> None:
        self.params = params

    def latency_samples(self) -> int:
        return self._latency

    def tail_samples(self) -> int:
        return self._tail

    def reset(self) -> None:
        if self.xo:
            self.xo.reset()
        if self.er:
            self.er.reset()
        if self.fdn:
            self.fdn.reset()

    def _hrir(self, az: float, el: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
        key = (round(az, 1), round(el, 1))
        if key not in self._hrir_cache:
            assert self.ds
            self._hrir_cache[key] = interpolate_hrir(self.ds, az, el)
        return self._hrir_cache[key]

    def _conv_mono(self, mono: np.ndarray, az: float, el: float = 0.0) -> np.ndarray:
        hl, hr = self._hrir(az, el)
        n = mono.shape[0]
        yl = fftconvolve(mono, hl, mode="full")[:n]
        yr = fftconvolve(mono, hr, mode="full")[:n]
        return np.stack([yl, yr], axis=0)

    def _crossover_file(self, stereo: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Run stateful crossover in configured block size."""
        assert self.cfg and self.xo
        self.xo.reset()
        n = stereo.shape[1]
        bs = self.cfg.block_size
        low = np.zeros_like(stereo)
        high = np.zeros_like(stereo)
        for start in range(0, n, bs):
            end = min(start + bs, n)
            lo, hi = self.xo.process(stereo[:, start:end])
            low[:, start:end] = lo
            high[:, start:end] = hi
        return low, high

    def _fdn_file(self, mono: np.ndarray) -> np.ndarray:
        assert self.cfg and self.fdn
        self.fdn.reset()
        n = mono.shape[0]
        bs = self.cfg.block_size
        out = np.zeros((2, n), dtype=np.float64)
        for start in range(0, n, bs):
            end = min(start + bs, n)
            out[:, start:end] = self.fdn.process(mono[start:end])
        return out

    def _er_file(self, mono: np.ndarray) -> tuple[np.ndarray, list[tuple[np.ndarray, float, float]]]:
        assert self.cfg and self.er
        self.er.reset()
        # Early reflections are LTI taps — process whole file once (stateful hist ok in one go)
        return self.er.process(mono)

    def process_file(self, stereo: np.ndarray) -> np.ndarray:
        assert self.cfg and self.params and self.ds
        if self.params.bypass:
            return stereo.copy()
        route = self.cfg.route.upper()
        low, high = self._crossover_file(stereo)
        low_mono = 0.5 * (low[0] + low[1])
        low_out = np.stack([low_mono, low_mono], axis=0)
        if route == "A":
            spat = self._route_a(high)
        elif route == "B":
            spat = self._route_b(high)
        else:
            spat = self._route_c(high)
        return spat + low_out

    def _room_from_mid(self, mid: np.ndarray) -> np.ndarray:
        assert self.params
        er_mono, directed = self._er_file(mid)
        room = np.zeros((2, mid.shape[0]), dtype=np.float64)
        if self.params.early_level > 0:
            # Limit to 6 loudest taps for P0 cost; still directional
            for sig, az, el in directed[:6]:
                room += self.params.early_level * self._conv_mono(sig, az, el)
        if self.params.late_level > 0:
            feed = mid * 0.7 + er_mono * 0.3
            room += self.params.late_level * self._fdn_file(feed)
        return room

    def _route_a(self, high: np.ndarray) -> np.ndarray:
        assert self.params
        direct = self._conv_mono(high[0], +30.0) + self._conv_mono(high[1], -30.0)
        mid = 0.5 * (high[0] + high[1])
        room = self._room_from_mid(mid)
        drr_lin = 10 ** (self.params.drr_offset_db / 20.0)
        return direct * drr_lin + room

    def _route_b(self, high: np.ndarray) -> np.ndarray:
        assert self.params and self.cfg
        up = stereo_upmix(
            high[0],
            high[1],
            self.cfg.sample_rate,
            width_deg=self.params.width_deg,
            ambient_mix=self.params.ambient_ratio,
        )
        d_mono = 0.5 * (up["direct_l"] + up["direct_r"])
        az_block = float(np.mean(up["azimuth_samples"])) if up["azimuth_samples"].size else 0.0
        direct = self._conv_mono(d_mono, az_block)
        direct += 0.35 * self._conv_mono(up["direct_l"], min(az_block + 10.0, self.params.width_deg))
        direct += 0.35 * self._conv_mono(up["direct_r"], max(az_block - 10.0, -self.params.width_deg))
        al = decorrelate(up["ambient_l"], self.cfg.sample_rate, self.cfg.seed)
        ar = decorrelate(up["ambient_r"], self.cfg.sample_rate, self.cfg.seed + 1)
        amb = self._conv_mono(al, 120.0) + self._conv_mono(ar, -120.0)
        mid = 0.5 * (high[0] + high[1])
        room = self._room_from_mid(mid)
        drr_lin = 10 ** (self.params.drr_offset_db / 20.0)
        return direct * drr_lin + amb + room

    def _route_c(self, high: np.ndarray) -> np.ndarray:
        assert self.params and self.cfg and self.magls
        up = stereo_upmix(
            high[0],
            high[1],
            self.cfg.sample_rate,
            width_deg=self.params.width_deg,
            ambient_mix=self.params.ambient_ratio,
        )
        d_mono = 0.5 * (up["direct_l"] + up["direct_r"])
        az_block = float(np.mean(up["azimuth_samples"])) if up["azimuth_samples"].size else 0.0
        direct = self._conv_mono(d_mono, az_block)
        al = decorrelate(up["ambient_l"], self.cfg.sample_rate, self.cfg.seed)
        ar = decorrelate(up["ambient_r"], self.cfg.sample_rate, self.cfg.seed + 1)
        bed = encode_stereo_ambient(al, ar, order=3)
        mid = 0.5 * (high[0] + high[1])
        er_mono, directed = self._er_file(mid)
        if self.params.early_level > 0:
            for sig, az_e, el in directed[:6]:
                bed += self.params.early_level * encode_plane_wave(sig, az_e, el, order=3)
        if self.cfg.orbit_deg_per_sec != 0.0:
            # Block-wise yaw for orbit (keeps block API meaningful)
            n = bed.shape[1]
            bs = self.cfg.block_size
            amb_bi = np.zeros((2, n), dtype=np.float64)
            for start in range(0, n, bs):
                end = min(start + bs, n)
                t_c = (start + (end - start) * 0.5) / self.cfg.sample_rate
                yaw = self.cfg.orbit_deg_per_sec * t_c
                chunk = rotate_yaw(bed[:, start:end], yaw, order=3)
                amb_bi[:, start:end] = self.magls.decode(chunk)
        else:
            amb_bi = self.magls.decode(bed)
        room = np.zeros((2, mid.shape[0]), dtype=np.float64)
        if self.params.late_level > 0:
            room += self.params.late_level * self._fdn_file(mid)
        drr_lin = 10 ** (self.params.drr_offset_db / 20.0)
        return direct * drr_lin + amb_bi + room


def process_blocks_compare(stereo: np.ndarray, config: RenderConfig, blocks: list[int]) -> dict[int, np.ndarray]:
    outs = {}
    for b in blocks:
        cfg = RenderConfig(**{**config.__dict__, "block_size": b})
        p = Processor()
        p.prepare(cfg.sample_rate, b, cfg)
        outs[b] = p.process_file(stereo)
    return outs
