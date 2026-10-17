"""Route V: virtual speakers, a shoebox with a ceiling and a floor, and a height layer.

The coherent direct path (the voice body, once the upmix has separated it)
is rendered on a closer, slightly narrower front pair and is sent to the
room more quietly than the incoherent bed. Wide, surround, height and the
room carry the space, and they are what ``strength`` turns up.

``strength == 0`` is a bypass. There is no dry/wet crossfade of the input.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

from .coords import az_el_to_cart
from .filters import BlockCrossover, delay_buffer
from .fixed_eq import apply_stereo, minphase_from_bands
from .hrtf import HrtfDataset, interpolate_hrir
from .loudness import limit_true_peak, true_peak_dbtp
from .room import FDNReverb, RoomParams, shoebox_image_taps
from .strength import _eq_for
from .upmix import decorrelate, set_decorrelation_delays, streaming_upmix_file

PRESET_PATH = Path(__file__).resolve().parents[2] / "presets" / "route_v.toml"
_FDN_IR: dict[tuple, np.ndarray] = {}


def db(x: float) -> float:
    return float(10.0 ** (float(x) / 20.0))


def _f64_list(value) -> tuple[float, ...]:
    if not value:
        return ()
    return tuple(float(v) for v in value)


@lru_cache(maxsize=2)
def load_route_v_file(path: str | None = None) -> dict:
    preset = Path(path) if path else PRESET_PATH
    with preset.open("rb") as handle:
        return tomllib.load(handle)


def clear_route_v_cache() -> None:
    load_route_v_file.cache_clear()


def _resolved_row(data: dict, preset: str) -> dict:
    if preset not in data or not isinstance(data[preset], dict):
        raise KeyError(f"preset '{preset}' is not in {PRESET_PATH}")
    layout = data.get("layout")
    row: dict = dict(layout) if isinstance(layout, dict) else {}
    row.update(data[preset])
    return row


@dataclass
class RouteVParams:
    strength: float
    bypass: bool
    front_az: float
    wide_az: float
    surround_az: float
    wide_lin: float
    surround_lin: float
    height_lin: float
    early_lin: float
    late_lin: float
    height_el: float
    height_az: tuple[float, ...]
    height_direct_db: float
    center_height_db: float
    center_distance_m: float
    center_spread_deg: float
    direct_room_send_db: float
    surround_delay_ms: float
    height_rear_delay_ms: float
    direct_height_delay_ms: float
    t60: float
    ceiling_db: float
    floor_db: float
    crossover_hz: float
    coherence_floor: float
    coherence_power: float
    pan_gamma: float
    absorption: float
    early_order: int
    early_taps: int
    room_size: tuple[float, float, float]
    listener: tuple[float, float, float]
    source: tuple[float, float, float]
    hrtf_ref_m: float
    near_lf_hz: float
    near_lf_max_db: float
    near_ild_scale: float
    delays_ms: tuple[float, ...]
    eq_bands_hz: tuple[float, ...]
    eq_gain_db: tuple[float, ...]


def _num(row: dict, key: str, default: float) -> float:
    if key not in row:
        return float(default)
    return float(row[key])


def _vec3(row: dict, key: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    raw = row.get(key, default)
    vals = tuple(float(v) for v in raw)
    if len(vals) != 3:
        raise ValueError(f"{key} must have 3 numbers")
    return vals  # type: ignore[return-value]


def map_route_v(
    strength: float,
    preset: str = "V2",
    hrtf_name: str = "",
    path: str | None = None,
) -> RouteVParams:
    """Map strength in [0, 1] onto route V. Zero is a full bypass."""
    s = float(np.clip(strength, 0.0, 1.0))
    data = load_route_v_file(path)
    row = _resolved_row(data, preset)
    engine = data.get("engine") if isinstance(data.get("engine"), dict) else {}
    delays = _f64_list(row.get("decorrelator_delays_ms") or engine.get("decorrelator_delays_ms"))
    front_1 = float(np.clip(_num(row, "front_az", 40.0), 30.0, 60.0))
    bands, eq = _eq_for(row, hrtf_name, s if s > 0.0 else 1.0)
    if s <= 0.0:
        bands, eq = (), ()
    distance = _num(row, "center_distance_m", 0.9)
    far = _num(row, "center_far_m", 2.0)
    explicit = _num(row, "center_spread_deg", 0.0)
    front = 30.0 if s <= 0.0 else 30.0 + s * (front_1 - 30.0)
    if explicit > 0.0:
        spread = min(explicit, front if s > 0.0 else explicit)
    else:
        t = float(np.clip((distance - 0.7) / max(far - 0.7, 0.1), 0.0, 1.0))
        spread = front * (0.65 + 0.35 * t)
    height_az = _f64_list(row.get("height_az")) or (45.0, -45.0, 135.0, -135.0)
    bed = 0.0 if s <= 0.0 else s
    return RouteVParams(
        strength=s,
        bypass=s <= 0.0,
        front_az=front,
        wide_az=_num(row, "wide_az", 90.0),
        surround_az=_num(row, "surround_az", 130.0),
        wide_lin=bed * db(_num(row, "wide_db", -3.0)),
        surround_lin=bed * db(_num(row, "surround_db", -8.0)),
        height_lin=bed * db(_num(row, "height_db", -6.0)),
        early_lin=bed * db(_num(row, "early_db", -3.0)),
        late_lin=bed * db(_num(row, "room_db", -6.0)),
        height_el=_num(row, "height_el", 45.0),
        height_az=height_az,
        height_direct_db=_num(row, "height_direct_db", -14.0),
        center_height_db=_num(row, "center_height_db", -22.0),
        center_distance_m=distance,
        center_spread_deg=spread,
        direct_room_send_db=_num(row, "direct_room_send_db", -8.0),
        surround_delay_ms=_num(row, "surround_delay_ms", 18.0),
        height_rear_delay_ms=_num(row, "height_rear_delay_ms", 12.0),
        direct_height_delay_ms=_num(row, "direct_height_delay_ms", 9.0),
        t60=_num(row, "t60", 0.95),
        ceiling_db=_num(row, "ceiling_db", 4.0),
        floor_db=_num(row, "floor_db", 0.0),
        crossover_hz=_num(row, "crossover_hz", 150.0),
        coherence_floor=_num(row, "coherence_floor", 0.22),
        coherence_power=_num(row, "coherence_power", 2.0),
        pan_gamma=_num(row, "pan_gamma", 1.0),
        absorption=_num(row, "absorption", 0.22),
        early_order=int(_num(row, "early_order", 1)),
        early_taps=int(_num(row, "early_taps", 12)),
        room_size=_vec3(row, "room_size_m", (7.0, 4.6, 2.7)),
        listener=_vec3(row, "listener_m", (2.2, 2.3, 1.25)),
        source=_vec3(row, "source_m", (4.8, 2.3, 1.25)),
        hrtf_ref_m=_num(row, "hrtf_ref_m", 1.5),
        near_lf_hz=_num(row, "near_lf_hz", 280.0),
        near_lf_max_db=_num(row, "near_lf_max_db", 4.0),
        near_ild_scale=_num(row, "near_ild_scale", 1.0),
        delays_ms=delays,
        eq_bands_hz=bands,
        eq_gain_db=eq,
    )


def _cached_fdn(sr: int, t60: float, seed: int, ir_len: int) -> FDNReverb:
    key = (int(sr), round(float(t60), 4), int(seed), int(ir_len))
    params = RoomParams(t60=float(t60), seed=int(seed))
    if key not in _FDN_IR:
        baked = FDNReverb(sr, params, ir_len=ir_len)
        _FDN_IR[key] = baked.ir.copy()
    return FDNReverb(sr, params, ir=_FDN_IR[key])


def _low_shelf(h: np.ndarray, sr: int, hz: float, gain_db: float) -> np.ndarray:
    if abs(gain_db) < 0.05:
        return h
    gain = db(gain_db)
    sos = butter(2, float(hz), btype="low", fs=int(sr), output="sos")
    low = sosfilt(sos, np.asarray(h, dtype=np.float64))
    return h + (gain - 1.0) * low


def _nearfield_db(az: float, el: float, distance_m: float, ref_m: float, ild_scale: float, lf_max_db: float) -> tuple[float, float]:
    """Extra low-frequency gain at each ear for a source closer than the HRTF.

    The mean of the two ears is the proximity shelf. The difference is the
    parallax ILD, which the far-field HRTF does not carry.
    """
    src = az_el_to_cart(az, el, max(distance_m, 0.25))
    d_l = float(np.linalg.norm(src - np.array([0.0, 0.090, 0.0])))
    d_r = float(np.linalg.norm(src - np.array([0.0, -0.090, 0.0])))
    g_l = distance_m / max(d_l, 1e-3)
    g_r = distance_m / max(d_r, 1e-3)
    mean = float(np.sqrt(max(g_l * g_r, 1e-12)))
    ild_l = ild_scale * 20.0 * np.log10(g_l / mean)
    ild_r = ild_scale * 20.0 * np.log10(g_r / mean)
    lf = 0.0
    if distance_m < ref_m:
        lf = min(lf_max_db, 20.0 * np.log10(ref_m / distance_m))
    return float(lf + ild_l), float(lf + ild_r)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-20))


def _energy(x: np.ndarray) -> float:
    return float(np.sum(np.asarray(x, dtype=np.float64) ** 2))


def protect_true_peak(stereo: np.ndarray, sr: int, ceiling_dbtp: float = -1.0) -> np.ndarray:
    """Peak-shave, then a single scalar if downsampling still exceeds the ceiling."""
    tp = true_peak_dbtp(stereo, sr)
    if not np.isfinite(tp) or tp <= ceiling_dbtp:
        return stereo
    out = limit_true_peak(stereo, sr, ceiling_dbtp=ceiling_dbtp)
    tp2 = true_peak_dbtp(out, sr)
    if np.isfinite(tp2) and tp2 > ceiling_dbtp + 1e-3:
        out = out * db(ceiling_dbtp - tp2)
    return out


class RouteVProcessor:
    def __init__(self) -> None:
        self.params: RouteVParams | None = None
        self.ds: HrtfDataset | None = None
        self.sr = 48_000
        self.block = 512
        self.seed = 20261016
        self.latency = 0
        self.last_stats: dict = {}
        self._eq: np.ndarray | None = None
        self._hrir: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
        self._hrir_delay = 0
        self._taps: list[tuple[float, float, float, float]] = []
        self._fdn: FDNReverb | None = None
        self._xo: BlockCrossover | None = None

    def prepare(self, ds: HrtfDataset, config) -> None:
        self.ds = ds
        self.sr = int(config.sample_rate)
        self.block = int(config.block_size)
        self.seed = int(config.seed)
        self.params = map_route_v(config.strength, config.preset, config.hrtf_name)
        self._hrir.clear()
        self._eq = None
        if self.params.delays_ms:
            set_decorrelation_delays(self.params.delays_ms)
        p = self.params
        if (
            getattr(config, "apply_fixed_eq", True)
            and not p.bypass
            and p.eq_gain_db
            and p.eq_bands_hz
        ):
            self._eq = minphase_from_bands(
                np.asarray(p.eq_bands_hz),
                np.asarray(p.eq_gain_db),
                self.sr,
                taps=512,
                nfft=8192,
            )
        self._xo = BlockCrossover(2, self.sr, p.crossover_hz)
        hl, hr = self._hrir_at(0.0, 0.0)
        self._hrir_delay = int(np.argmax(np.abs(hl) + np.abs(hr)))
        self.latency = 0 if p.bypass else 1024 + self._hrir_delay
        self._taps = shoebox_image_taps(
            np.asarray(p.source),
            np.asarray(p.listener),
            np.asarray(p.room_size),
            max_order=p.early_order,
            absorption=p.absorption,
            n_keep=p.early_taps,
        )
        ir_len = int(min(max(p.t60 * 1.15, 0.35), 1.05) * self.sr)
        self._fdn = _cached_fdn(self.sr, p.t60, self.seed, ir_len)
        self.last_stats = {}

    def process_file(self, stereo: np.ndarray) -> np.ndarray:
        assert self.params is not None and self.ds is not None and self._xo is not None
        stereo = np.asarray(stereo, dtype=np.float64)
        if stereo.ndim != 2 or stereo.shape[0] != 2:
            raise ValueError(f"route V expects stereo shaped (2, N), got {stereo.shape}")
        p = self.params
        if p.bypass:
            self.latency = 0
            self.last_stats = {
                "bypass": True,
                "rear_bus_rms": 0.0,
                "room_bus_rms": 0.0,
                "bed_to_total": 0.0,
                "drr_center_db": None,
                "true_peak_dbtp": true_peak_dbtp(stereo, self.sr),
            }
            return stereo.copy()

        low, high = self._split(stereo)
        up = streaming_upmix_file(
            high[0],
            high[1],
            self.sr,
            block=self.block,
            width_deg=p.front_az,
            ambient_mix=1.0,
            wide_deg=p.wide_az,
            rear_deg=p.surround_az,
            coherence_floor=p.coherence_floor,
            coherence_power=p.coherence_power,
            pan_gamma=p.pan_gamma,
            direct_bleed=0.0,
            hf_pan_floor=1.0,
            hf_pan_start_hz=3000.0,
            hf_pan_end_hz=8000.0,
            hf_direct_start_hz=0.0,
            hf_direct_end_hz=0.0,
        )
        sectors = np.asarray(up["sectors"], dtype=np.float64)
        azs = np.asarray(up["sector_azimuths"], dtype=np.float64)
        center_i = int(np.argmin(np.abs(azs)))
        center = sectors[center_i]
        n = high.shape[1]
        direct = np.zeros((2, n), dtype=np.float64)
        spread = float(p.center_spread_deg)
        center_image = self._place(center, +spread, 0.0, near=True) + self._place(center, -spread, 0.0, near=True)
        direct += center_image
        lateral = np.zeros(n, dtype=np.float64)
        for i, az in enumerate(azs):
            if i == center_i:
                continue
            direct += self._place(sectors[i], float(az), 0.0, near=False)
            lateral += sectors[i]

        amb_l = np.asarray(up["ambient_l"], dtype=np.float64)
        amb_r = np.asarray(up["ambient_r"], dtype=np.float64)
        wide = self._place(decorrelate(amb_l, self.sr, self.seed + 1), +p.wide_az) 
        wide = wide + self._place(decorrelate(amb_r, self.sr, self.seed + 2), -p.wide_az)
        wide *= p.wide_lin

        sur_delay = int(round(p.surround_delay_ms * 1e-3 * self.sr))
        sur_l = decorrelate(delay_buffer(amb_l, sur_delay), self.sr, self.seed + 3)
        sur_r = decorrelate(delay_buffer(amb_r, sur_delay), self.sr, self.seed + 4)
        surround = self._place(sur_l, +p.surround_az) + self._place(sur_r, -p.surround_az)
        surround *= p.surround_lin

        height = self._height_bed(amb_l, amb_r, center, lateral)
        direct_mono = 0.5 * (np.asarray(up["direct_l"]) + np.asarray(up["direct_r"]))
        amb_mono = 0.5 * (amb_l + amb_r)
        send = db(p.direct_room_send_db)
        room_direct = self._room(direct_mono * send)
        room_amb = self._room(amb_mono)
        room = room_direct + room_amb

        align = int(up["latency"]) + int(self._hrir_delay)
        self.latency = align
        low_mono = 0.5 * (low[0] + low[1])
        low_out = delay_buffer(np.stack([low_mono, low_mono], axis=0), align)
        mixed = direct + wide + surround + height + room + low_out
        mixed = self._edge_fade(mixed, stereo)
        if self._eq is not None:
            mixed = apply_stereo(mixed, self._eq)
        mixed = protect_true_peak(mixed, self.sr, ceiling_dbtp=-1.0)

        room_center = self._room(center * send)
        drr_center = 10.0 * np.log10((_energy(center_image) + 1e-20) / (_energy(room_center) + 1e-20))
        drr_direct = 10.0 * np.log10((_energy(direct) + 1e-20) / (_energy(room_direct) + 1e-20))
        amb_image = wide + surround + height
        drr_ambient = 10.0 * np.log10((_energy(amb_image) + 1e-20) / (_energy(room_amb) + 1e-20))
        total = _rms(mixed)
        self.last_stats = {
            "bypass": False,
            "coherence_mean": float(up["coherence_mean"]),
            "front_az": p.front_az,
            "center_spread_deg": spread,
            "center_distance_m": p.center_distance_m,
            "direct_room_send_db": p.direct_room_send_db,
            "height_el": p.height_el,
            "taps": [
                {"delay_ms": round(t[0] * 1000.0, 2), "az": round(t[2], 1), "el": round(t[3], 1), "gain": round(t[1], 4)}
                for t in self._taps
            ],
            "drr_center_db": float(drr_center),
            "drr_direct_db": float(drr_direct),
            "drr_ambient_db": float(drr_ambient),
            "direct_bus_rms": _rms(direct),
            "wide_bus_rms": _rms(wide),
            "surround_bus_rms": _rms(surround),
            "height_bus_rms": _rms(height),
            "room_bus_rms": _rms(room),
            "room_direct_rms": _rms(room_direct),
            "rear_bus_rms": _rms(surround + height),
            "bed_to_total": (_rms(wide) + _rms(surround) + _rms(height) + _rms(room)) / (total + 1e-12),
            "true_peak_dbtp": true_peak_dbtp(mixed, self.sr),
            "fixed_eq": self._eq is not None,
        }
        return mixed

    def _split(self, stereo: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        assert self._xo is not None
        self._xo.reset()
        n = stereo.shape[1]
        low = np.zeros_like(stereo)
        high = np.zeros_like(stereo)
        for start in range(0, n, self.block):
            end = min(start + self.block, n)
            lo, hi = self._xo.process(stereo[:, start:end])
            low[:, start:end] = lo
            high[:, start:end] = hi
        return low, high

    def _hrir_at(self, az: float, el: float, near_db: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        assert self.ds is not None and self.params is not None
        nd = (0.0, 0.0) if near_db is None else (round(near_db[0], 2), round(near_db[1], 2))
        key = (round(float(az), 1), round(float(el), 1), nd)
        if key not in self._hrir:
            hl, hr = interpolate_hrir(self.ds, float(az), float(el))
            if abs(nd[0]) > 0.05 or abs(nd[1]) > 0.05:
                hz = self.params.near_lf_hz
                hl = _low_shelf(hl, self.sr, hz, nd[0])
                hr = _low_shelf(hr, self.sr, hz, nd[1])
            self._hrir[key] = (hl, hr)
        return self._hrir[key]

    def _place(self, mono: np.ndarray, az: float, el: float = 0.0, near: bool = False) -> np.ndarray:
        assert self.params is not None
        near_db = None
        if near:
            near_db = _nearfield_db(
                az,
                el,
                self.params.center_distance_m,
                self.params.hrtf_ref_m,
                self.params.near_ild_scale,
                self.params.near_lf_max_db,
            )
        hl, hr = self._hrir_at(az, el, near_db)
        n = mono.shape[0]
        yl = fftconvolve(mono, hl, mode="full")[:n]
        yr = fftconvolve(mono, hr, mode="full")[:n]
        return np.stack([yl, yr], axis=0)

    def _height_bed(self, amb_l: np.ndarray, amb_r: np.ndarray, center: np.ndarray, lateral: np.ndarray) -> np.ndarray:
        assert self.params is not None
        p = self.params
        n = amb_l.shape[0]
        feeds = (
            decorrelate(amb_l, self.sr, self.seed + 5),
            decorrelate(amb_r, self.sr, self.seed + 6),
            decorrelate(delay_buffer(amb_l, int(round(p.height_rear_delay_ms * 1e-3 * self.sr))), self.sr, self.seed + 7),
            decorrelate(delay_buffer(amb_r, int(round(p.height_rear_delay_ms * 1e-3 * self.sr))), self.sr, self.seed + 8),
        )
        out = np.zeros((2, n), dtype=np.float64)
        azs = list(p.height_az)
        if len(azs) < 4:
            azs = [45.0, -45.0, 135.0, -135.0]
        for sig, az in zip(feeds, azs[:4]):
            out += self._place(sig, float(az), p.height_el)
        out *= p.height_lin
        # A little direct energy on the front height pair only. The center
        # (the voice) uses center_height_db, which is much lower than the
        # lateral direct.
        delay_n = int(round(p.direct_height_delay_ms * 1e-3 * self.sr))
        lifted = delay_buffer(lateral, delay_n) * db(p.height_direct_db)
        lifted = lifted + delay_buffer(center, delay_n) * db(p.center_height_db)
        front = azs[:2]
        for az in front:
            out += p.height_lin * self._place(lifted, float(az), p.height_el)
        return out

    def _room(self, mono: np.ndarray) -> np.ndarray:
        assert self.params is not None and self._fdn is not None
        p = self.params
        n = mono.shape[0]
        early = np.zeros((2, n), dtype=np.float64)
        for delay_s, gain, az, el in self._taps:
            g = gain * p.early_lin
            if el >= 25.0:
                g *= db(p.ceiling_db)
            elif el <= -25.0:
                g *= db(p.floor_db)
            delayed = delay_buffer(mono, int(round(delay_s * self.sr)))
            early += self._place(delayed * g, az, el)
        self._fdn.reset()
        late = self._fdn.process(mono) * p.late_lin
        if late.shape[1] != n:
            trimmed = np.zeros((2, n), dtype=np.float64)
            m = min(n, late.shape[1])
            trimmed[:, :m] = late[:, :m]
            late = trimmed
        return early + late

    def _edge_fade(self, mixed: np.ndarray, stereo: np.ndarray) -> np.ndarray:
        """Same file-end crossfade as route B, so a delayed tail is not read as treble error."""
        n_tail = min(128, mixed.shape[1] // 8)
        if n_tail <= 8 or stereo.shape[1] != mixed.shape[1]:
            return mixed
        fade = np.linspace(0.0, 1.0, n_tail)
        rms_o = _rms(mixed)
        rms_i = _rms(stereo)
        tail = stereo[:, -n_tail:] * (rms_o / rms_i)
        mixed = mixed.copy()
        mixed[:, -n_tail:] = mixed[:, -n_tail:] * (1.0 - fade) + tail * fade
        return mixed
