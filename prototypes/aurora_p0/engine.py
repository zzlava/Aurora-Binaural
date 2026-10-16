"""Block-processing render engine for routes A / B / C."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt


def _band_lift(rows: np.ndarray, sr: int, lo: float, hi: float, gain_db: float) -> np.ndarray:
    """Add a band-limited copy so a bed can be lifted without touching the direct objects."""
    gain = 10.0 ** (float(gain_db) / 20.0)
    sos = butter(2, [float(lo), float(hi)], btype="band", fs=int(sr), output="sos")
    out = np.empty_like(rows, dtype=np.float64)
    for i in range(rows.shape[0]):
        band = sosfilt(sos, np.asarray(rows[i], dtype=np.float64))
        out[i] = rows[i] + (gain - 1.0) * band
    return out


def _high_shelf_rows(rows: np.ndarray, sr: int, hz: float, gain_db: float) -> np.ndarray:
    """Approximate high shelf: unity below ``hz``, ``gain_db`` well above it."""
    gain = 10.0 ** (float(gain_db) / 20.0)
    sos = butter(2, float(hz), btype="high", fs=int(sr), output="sos")
    out = np.empty_like(rows, dtype=np.float64)
    for i in range(rows.shape[0]):
        high = sosfilt(sos, np.asarray(rows[i], dtype=np.float64))
        out[i] = rows[i] + (gain - 1.0) * high
    return out

from . import CROSSOVER_HZ, SAMPLE_RATE
from .ambisonics import MagLSDecoder, encode_plane_wave, rotate_yaw
from .filters import BlockCrossover, delay_buffer
from .fixed_eq import apply_stereo, minphase_from_bands
from .hrtf import HrtfDataset, apply_equalization, interpolate_hrir, load_sofa
from .room import EarlyReflections, FDNReverb, RoomParams
from .strength import StrengthParams, map_strength
from .upmix import decorrelate, set_decorrelation_delays, streaming_upmix_file

# Process-wide caches (seeded / path-keyed) to keep the sample matrix tractable
_HRTF_CACHE: dict[tuple, HrtfDataset] = {}
_FDN_IR_CACHE: dict[tuple, np.ndarray] = {}
_MAGLS_CACHE: dict[tuple, MagLSDecoder] = {}


def _get_hrtf(path: Path, name: str, eq_mode: str, sr: int, directional_power: bool) -> HrtfDataset:
    key = (str(path.resolve()), eq_mode, bool(directional_power))
    if key not in _HRTF_CACHE:
        raw = load_sofa(path, name=name, target_sr=sr, n_taps=256)
        _HRTF_CACHE[key] = apply_equalization(
            raw, mode=eq_mode, directional_power=directional_power
        )
    return _HRTF_CACHE[key]


@dataclass
class RenderConfig:
    route: str  # "A" | "B" | "C"
    strength: float = 0.4
    preset: str = "room"
    hrtf_path: Path | None = None
    hrtf_name: str = "thk_ku100"
    eq_mode: str = "frontal"
    directional_power: bool = True
    # Ignored. diagnostic_timbre_guard() fits each file to its own dry
    # reference and must not run on the delivery path. Timbre correction is
    # the fixed FIR from the preset (HRTF, eq mode, preset, strength).
    timbre_guard: bool = False
    apply_fixed_eq: bool = True
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
        self.last_stats: dict = {}
        self._spatial_latency = 0
        self._hrir_delay = 0
        self._eq_fir: np.ndarray | None = None

    def prepare(self, sample_rate: int, max_block: int, config: RenderConfig) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"P0 prototype fixed at {SAMPLE_RATE} Hz, got {sample_rate}")
        self.cfg = config
        if config.hrtf_path is None:
            raise FileNotFoundError("hrtf_path required; no synthetic fallback")
        self.ds = _get_hrtf(
            config.hrtf_path,
            config.hrtf_name,
            config.eq_mode,
            sample_rate,
            config.directional_power,
        )
        self.params = map_strength(config.strength, config.preset, config.hrtf_name)
        if self.params.delays_ms:
            set_decorrelation_delays(self.params.delays_ms)
        self._eq_fir = None
        if (
            config.apply_fixed_eq
            and not self.params.bypass
            and self.params.eq_gain_db
            and self.params.eq_bands_hz
        ):
            self._eq_fir = minphase_from_bands(
                np.asarray(self.params.eq_bands_hz),
                np.asarray(self.params.eq_gain_db),
                sample_rate,
                taps=512,
                nfft=8192,
            )
        crossover = self.params.crossover_hz or config.crossover_hz
        self.xo = BlockCrossover(2, sample_rate, crossover)
        t60 = self.params.t60
        room = RoomParams(seed=config.seed, t60=t60)
        self.er = EarlyReflections(sample_rate, room)
        self.fdn = FDNReverb(sample_rate, room)
        fdn_key = (config.seed, t60, sample_rate, "bright12k")
        if fdn_key in _FDN_IR_CACHE:
            self.fdn.ir = _FDN_IR_CACHE[fdn_key]
            self.fdn._hist = np.zeros((2, self.fdn.ir.shape[1] - 1), dtype=np.float64)
        else:
            _FDN_IR_CACHE[fdn_key] = self.fdn.ir.copy()
        if config.route.upper() == "C":
            mkey = (str(Path(config.hrtf_path).resolve()), config.eq_mode, bool(config.directional_power))
            if mkey not in _MAGLS_CACHE:
                _MAGLS_CACHE[mkey] = MagLSDecoder(self.ds, order=3)
            self.magls = _MAGLS_CACHE[mkey]
        else:
            self.magls = None
        self._latency = 0
        self._tail = int(0.2 * sample_rate)
        self._hrir_cache.clear()
        self.last_stats = {}
        self._spatial_latency = 0
        hl, hr = interpolate_hrir(self.ds, 0.0, 0.0)
        self._hrir_delay = int(np.argmax(np.abs(hl) + np.abs(hr)))

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
        # Near-frontal HRIRs only. A common post-EQ cannot lift a dark
        # frontal mono-downmix without also lifting a bright lateral bed.
        shelf_db = float(getattr(self.params, "frontal_hf_shelf_db", 0.0))
        if abs(shelf_db) > 0.05 and abs(float(az)) <= float(getattr(self.params, "frontal_hf_shelf_az", 35.0)):
            stacked = _high_shelf_rows(np.stack([hl, hr]), self.cfg.sample_rate, self.params.frontal_hf_shelf_hz, shelf_db)
            hl, hr = stacked[0], stacked[1]
        n = mono.shape[0]
        yl = fftconvolve(mono, hl, mode="full")[:n]
        yr = fftconvolve(mono, hr, mode="full")[:n]
        return np.stack([yl, yr], axis=0)

    def _crossover_file(self, stereo: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
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
        return self.er.process(mono)

    def _side_feed(self, high: np.ndarray) -> np.ndarray:
        """Decorrelated L−R feed. Not a dry/wet mix — the dry pair is not blended."""
        assert self.params and self.cfg
        g = float(self.params.side_decor_gain)
        n = high.shape[1]
        if g <= 0.0:
            return np.zeros((2, n), dtype=np.float64)
        side = 0.5 * (high[0] - high[1])
        d1 = decorrelate(side, self.cfg.sample_rate, self.cfg.seed + 11)
        d2 = decorrelate(side, self.cfg.sample_rate, self.cfg.seed + 12)
        pair = np.stack([g * d1, g * d2], axis=0)
        # Raised-cosine fade, zero phase. A minimum-phase lowpass moves the
        # interaural delay back inside the ±1 ms IACC window and the mix
        # collapses. The cutoff is a preset constant.
        hz = float(self.params.side_lpf_hz)
        if hz > 1000.0:
            faded = []
            for row in pair:
                spec = np.fft.rfft(row)
                freqs = np.fft.rfftfreq(row.shape[0], 1.0 / self.cfg.sample_rate)
                weight = 1.0 - np.clip((freqs - (hz - 500.0)) / 1000.0, 0.0, 1.0)
                faded.append(np.fft.irfft(spec * weight, n=row.shape[0]))
            pair = np.stack(faded, axis=0)
        return pair

    def _run_upmix(self, high: np.ndarray) -> dict:
        assert self.cfg and self.params
        return streaming_upmix_file(
            high[0],
            high[1],
            self.cfg.sample_rate,
            block=self.cfg.block_size,
            width_deg=self.params.width_deg,
            ambient_mix=self.params.ambient_ratio,
            wide_deg=self.params.wide_deg,
            rear_deg=self.params.rear_deg,
            coherence_floor=self.params.coherence_floor,
            coherence_power=self.params.coherence_power,
            pan_gamma=self.params.pan_gamma,
            direct_bleed=self.params.direct_bleed,
            hf_pan_floor=self.params.hf_pan_floor,
            hf_pan_start_hz=self.params.hf_pan_start_hz,
            hf_pan_end_hz=self.params.hf_pan_end_hz,
            hf_direct_start_hz=self.params.hf_direct_start_hz,
            hf_direct_end_hz=self.params.hf_direct_end_hz,
        )

    def process_file(self, stereo: np.ndarray) -> np.ndarray:
        assert self.cfg and self.params and self.ds
        if self.params.bypass:
            self._latency = 0
            self._spatial_latency = 0
            self.last_stats = {"rear_bus_rms": 0.0, "room_bus_rms": 0.0, "bed_to_total": 0.0}
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
        # STFT delay plus the frontal HRIR peak. Without the peak, LR4's
        # low and high arms meet about 0.5 ms apart and cancel near 150 Hz.
        align = int(self._spatial_latency) + int(self._hrir_delay)
        low_out = delay_buffer(low_out, align)
        # The high image is already scaled by the DRR gain inside each route.
        # The bypassed bass has to take the same gain, or strength reads as a
        # bass shelf once the median level is removed.
        drr_lin = 10 ** (self.params.drr_offset_db / 20.0)
        mixed = spat + low_out * drr_lin
        # The delay drops the input tail and the unwindowed 1/3-octave
        # metric reads that new endpoint as a high-frequency error. Crossfade
        # the last few milliseconds back to the input at the same DRR gain.
        # Fixed length, not a timbre match.
        n_tail = min(128, mixed.shape[1] // 8)
        if n_tail > 8 and stereo.shape[1] == mixed.shape[1]:
            fade = np.linspace(0.0, 1.0, n_tail)
            tail = stereo[:, -n_tail:] * drr_lin
            mixed[:, -n_tail:] = mixed[:, -n_tail:] * (1.0 - fade) + tail * fade
        self._latency = align
        if self._eq_fir is not None:
            mixed = apply_stereo(mixed, self._eq_fir)
            self.last_stats["fixed_eq_max_db"] = float(np.max(np.abs(self.params.eq_gain_db)))
        total = float(np.sqrt(np.mean(mixed**2)) + 1e-12)
        rear = float(self.last_stats.get("rear_bus_rms", 0.0))
        room = float(self.last_stats.get("room_bus_rms", 0.0))
        self.last_stats["bed_to_total"] = (rear + room) / total
        return mixed

    def _room_from_mid(self, mid: np.ndarray) -> np.ndarray:
        assert self.params
        er_mono, directed = self._er_file(mid)
        room = np.zeros((2, mid.shape[0]), dtype=np.float64)
        if self.params.early_level > 0:
            for sig, az, el in directed[:6]:
                room += self.params.early_level * self._conv_mono(sig, az, el)
        late = np.zeros_like(room)
        if self.params.late_level > 0:
            feed = mid * 0.7 + er_mono * 0.3
            late = self.params.late_level * self._fdn_file(feed)
            room += late
        self.last_stats["room_bus_rms"] = float(np.sqrt(np.mean(room**2)))
        self.last_stats["late_bus_rms"] = float(np.sqrt(np.mean(late**2)))
        return room

    def _route_a(self, high: np.ndarray) -> np.ndarray:
        assert self.params
        self._spatial_latency = 0
        az = float(self.params.width_deg)
        direct = self._conv_mono(high[0], +az) + self._conv_mono(high[1], -az)
        direct += delay_buffer(self._side_feed(high), self._hrir_delay)
        mid = 0.5 * (high[0] + high[1])
        room = self._room_from_mid(mid)
        self.last_stats["rear_bus_rms"] = 0.0
        self.last_stats["coherence_mean"] = None
        drr_lin = 10 ** (self.params.drr_offset_db / 20.0)
        return direct * drr_lin + room

    def _spatial_upmix(self, high: np.ndarray, to_ambisonics: bool) -> tuple[np.ndarray, np.ndarray]:
        """Returns (direct HRTF image, rear/wide bed as binaural)."""
        assert self.params and self.cfg
        up = self._run_upmix(high)
        self._spatial_latency = int(up["latency"])
        self.last_stats["coherence_mean"] = float(up["coherence_mean"])
        sectors = np.asarray(up["sectors"])
        # Fixed shelf on the direct objects only. Incoherent beds stay put,
        # so a quiet coherent top octave can be lifted without brightening
        # an ambience bed that is already hot there.
        shelf_db = float(self.params.direct_hf_shelf_db)
        if abs(shelf_db) > 0.05:
            sectors = _high_shelf_rows(sectors, self.cfg.sample_rate, self.params.direct_hf_shelf_hz, shelf_db)
        azs = np.asarray(up["sector_azimuths"], dtype=np.float64)
        direct = np.zeros((2, high.shape[1]), dtype=np.float64)
        for s, az in enumerate(azs):
            direct += self._conv_mono(sectors[s], float(az), 0.0)
        wide_l = decorrelate(np.asarray(up["wide_l"]), self.cfg.sample_rate, self.cfg.seed + 3)
        wide_r = decorrelate(np.asarray(up["wide_r"]), self.cfg.sample_rate, self.cfg.seed + 4)
        amb_l = decorrelate(np.asarray(up["ambient_l"]), self.cfg.sample_rate, self.cfg.seed + 5)
        amb_r = decorrelate(np.asarray(up["ambient_r"]), self.cfg.sample_rate, self.cfg.seed + 6)
        wide_deg = float(up["wide_deg"])
        rear_deg = float(up["rear_deg"])
        if to_ambisonics:
            assert self.magls
            bed = encode_plane_wave(wide_l, wide_deg, 0.0, order=3)
            bed = bed + encode_plane_wave(wide_r, -wide_deg, 0.0, order=3)
            bed = bed + encode_plane_wave(amb_l, rear_deg, 0.0, order=3)
            bed = bed + encode_plane_wave(amb_r, -rear_deg, 0.0, order=3)
            rear_bi = self._decode_bed(bed)
            # Rear-only bus for the strength curve (ambient stems, not the wide pair).
            rear_bed = encode_plane_wave(amb_l, rear_deg, 0.0, order=3)
            rear_bed = rear_bed + encode_plane_wave(amb_r, -rear_deg, 0.0, order=3)
            rear_only = self._decode_bed(rear_bed)
        else:
            wide_bi = self._conv_mono(wide_l, wide_deg) + self._conv_mono(wide_r, -wide_deg)
            rear_only = self._conv_mono(amb_l, rear_deg) + self._conv_mono(amb_r, -rear_deg)
            rear_bi = wide_bi + rear_only
        self.last_stats["rear_bus_rms"] = float(np.sqrt(np.mean(rear_only**2)))
        peak_db = float(self.params.bed_peak_db)
        if abs(peak_db) > 0.05:
            rear_bi = _band_lift(
                rear_bi,
                self.cfg.sample_rate,
                self.params.bed_peak_lo_hz,
                self.params.bed_peak_hi_hz,
                peak_db,
            )
        image = direct + rear_bi + self._delay_side(high)
        return image, rear_only

    def _delay_side(self, high: np.ndarray) -> np.ndarray:
        return delay_buffer(self._side_feed(high), self._spatial_latency + int(self._hrir_delay))

    def _decode_bed(self, bed: np.ndarray) -> np.ndarray:
        assert self.cfg and self.magls
        if self.cfg.orbit_deg_per_sec == 0.0:
            return self.magls.decode(bed)
        n = bed.shape[1]
        bs = self.cfg.block_size
        out = np.zeros((2, n), dtype=np.float64)
        for start in range(0, n, bs):
            end = min(start + bs, n)
            t_c = (start + (end - start) * 0.5) / self.cfg.sample_rate
            yaw = self.cfg.orbit_deg_per_sec * t_c
            out[:, start:end] = self.magls.decode(rotate_yaw(bed[:, start:end], yaw, order=3))
        return out

    def _route_b(self, high: np.ndarray) -> np.ndarray:
        assert self.params
        image, _rear = self._spatial_upmix(high, to_ambisonics=False)
        mid = 0.5 * (high[0] + high[1])
        room = delay_buffer(self._room_from_mid(mid), self._spatial_latency)
        drr_lin = 10 ** (self.params.drr_offset_db / 20.0)
        return image * drr_lin + room

    def _route_c(self, high: np.ndarray) -> np.ndarray:
        assert self.params and self.cfg and self.magls
        # Direct + incoherent bed. Early reflections join the bed; late FDN stays binaural.
        up_image, _rear = self._spatial_upmix(high, to_ambisonics=True)
        mid = 0.5 * (high[0] + high[1])
        er_mono, directed = self._er_file(mid)
        bed = np.zeros((16, high.shape[1]), dtype=np.float64)
        if self.params.early_level > 0:
            for sig, az_e, el in directed[:6]:
                bed = bed + self.params.early_level * encode_plane_wave(sig, az_e, el, order=3)
        early_bi = self._decode_bed(bed) if self.params.early_level > 0 else np.zeros((2, high.shape[1]))
        early_bi = delay_buffer(early_bi, self._spatial_latency)
        late = np.zeros((2, high.shape[1]), dtype=np.float64)
        if self.params.late_level > 0:
            late = self.params.late_level * self._fdn_file(mid)
            late = delay_buffer(late, self._spatial_latency)
        room = early_bi + late
        self.last_stats["room_bus_rms"] = float(np.sqrt(np.mean(room**2)))
        drr_lin = 10 ** (self.params.drr_offset_db / 20.0)
        return up_image * drr_lin + room


def process_blocks_compare(stereo: np.ndarray, config: RenderConfig, blocks: list[int]) -> dict[int, np.ndarray]:
    outs = {}
    for b in blocks:
        cfg = RenderConfig(**{**config.__dict__, "block_size": b})
        p = Processor()
        p.prepare(cfg.sample_rate, b, cfg)
        outs[b] = p.process_file(stereo)
    return outs
