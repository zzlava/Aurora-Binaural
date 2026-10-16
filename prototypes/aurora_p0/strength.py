"""Strength → perceptual parameter mapping. Numbers come from presets/p0_draft.toml.

s = 0 is a full bypass. Nonzero strengths interpolate the ``*_at_0`` floors
to the ``*_at_1`` endpoints. There is no dry/wet crossfade of the input.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PRESET_PATH = Path(__file__).resolve().parents[2] / "presets" / "p0_draft.toml"


@dataclass
class StrengthParams:
    strength: float
    width_deg: float
    ambient_ratio: float
    early_level: float
    late_level: float
    drr_offset_db: float
    side_decor_gain: float
    bypass: bool
    t60: float = 0.35
    crossover_hz: float = 150.0
    coherence_floor: float = 0.22
    coherence_power: float = 2.0
    pan_gamma: float = 1.0
    wide_deg: float = 90.0
    rear_deg: float = 130.0
    direct_bleed: float = 0.0
    hf_pan_floor: float = 1.0
    hf_pan_start_hz: float = 3000.0
    hf_pan_end_hz: float = 8000.0
    hf_direct_start_hz: float = 0.0
    hf_direct_end_hz: float = 0.0
    bed_peak_db: float = 0.0
    bed_peak_lo_hz: float = 1000.0
    bed_peak_hi_hz: float = 1600.0
    side_lpf_hz: float = 0.0
    direct_hf_shelf_db: float = 0.0
    direct_hf_shelf_hz: float = 5000.0
    delays_ms: tuple[float, ...] = ()
    eq_bands_hz: tuple[float, ...] = ()
    eq_gain_db: tuple[float, ...] = ()
    hrtf_key: str = ""


def _num(table: dict, key: str, default: float | None = None) -> float:
    if key not in table:
        if default is None:
            raise KeyError(key)
        return float(default)
    return float(table[key])


def _f64_list(value) -> tuple[float, ...]:
    if not value:
        return ()
    return tuple(float(v) for v in value)


@lru_cache(maxsize=4)
def load_preset_file(path: str | None = None) -> dict:
    preset = Path(path) if path else PRESET_PATH
    with preset.open("rb") as handle:
        return tomllib.load(handle)


def clear_preset_cache() -> None:
    load_preset_file.cache_clear()


def _row(preset: str, path: str | None) -> dict:
    data = load_preset_file(path)
    if preset not in data or not isinstance(data[preset], dict):
        raise KeyError(f"preset '{preset}' is not in {path or PRESET_PATH}")
    return data[preset]


def _delays(data: dict, row: dict) -> tuple[float, ...]:
    if "decorrelator_delays_ms" in row:
        return _f64_list(row["decorrelator_delays_ms"])
    engine = data.get("engine")
    if isinstance(engine, dict) and "decorrelator_delays_ms" in engine:
        return _f64_list(engine["decorrelator_delays_ms"])
    return ()


def _eq_for(row: dict, hrtf_name: str, strength: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Interpolate the fixed 1/3-octave correction for this HRTF and strength.

    The curve is designed offline (pink noise, several directions). It does
    not depend on the signal being rendered.
    """
    block = row.get("fixed_eq")
    if not isinstance(block, dict):
        return (), ()
    bands = _f64_list(block.get("bands_hz"))
    strengths = _f64_list(block.get("strengths"))
    if not bands or not strengths:
        return (), ()
    key = _hrtf_alias(hrtf_name)
    curve = block.get(key)
    if not isinstance(curve, dict):
        return (), ()
    matrix = curve.get("gain_db")
    if not matrix:
        return (), ()
    gains = [tuple(float(v) for v in row_g) for row_g in matrix]
    if len(gains) != len(strengths) or any(len(g) != len(bands) for g in gains):
        raise ValueError(f"fixed_eq.{key} shape does not match bands/strengths")
    return bands, _interp_gains(strengths, gains, strength)


def _hrtf_alias(name: str) -> str:
    aliases = {
        "ku100": "thk_ku100",
        "sadie": "sadie2_d1",
        "sadie_d1": "sadie2_d1",
    }
    return aliases.get(name, name)


def _interp_gains(
    strengths: tuple[float, ...], gains: list[tuple[float, ...]], strength: float
) -> tuple[float, ...]:
    s = float(strength)
    if s <= strengths[0]:
        return gains[0]
    if s >= strengths[-1]:
        return gains[-1]
    for i in range(len(strengths) - 1):
        s0, s1 = strengths[i], strengths[i + 1]
        if s0 <= s <= s1:
            span = s1 - s0
            t = 0.0 if span <= 0 else (s - s0) / span
            return tuple((1.0 - t) * a + t * b for a, b in zip(gains[i], gains[i + 1]))
    return gains[-1]


def map_strength(
    s: float,
    preset: str = "room",
    hrtf_name: str = "",
    path: str | None = None,
) -> StrengthParams:
    """Map strength ∈ [0, 1] to rendering parameters. s = 0 → full bypass."""
    s = float(np_clip(s, 0.0, 1.0))
    data = load_preset_file(path)
    row = _row(preset, path)
    delays = _delays(data, row)
    t60 = _num(row, "t60", 0.35)
    crossover = _num(row, "crossover_hz", 150.0)
    floor = _num(row, "coherence_floor", 0.22)
    power = _num(row, "coherence_power", 2.0)
    gamma = _num(row, "pan_gamma", 1.0)
    wide = _num(row, "wide_deg", 90.0)
    rear = _num(row, "rear_deg", 130.0)
    hf_floor = _num(row, "hf_pan_floor", 1.0)
    hf_start = _num(row, "hf_pan_start_hz", 3000.0)
    hf_end = _num(row, "hf_pan_end_hz", 8000.0)
    hf_direct_start = _num(row, "hf_direct_start_hz", 0.0)
    hf_direct_end = _num(row, "hf_direct_end_hz", 0.0)
    bed_db = _num(row, "bed_peak_db", 0.0)
    bed_lo = _num(row, "bed_peak_lo_hz", 1000.0)
    bed_hi = _num(row, "bed_peak_hi_hz", 1600.0)
    side_lpf = _num(row, "side_lpf_hz", 0.0)
    shelf_db = _num(row, "direct_hf_shelf_db", 0.0)
    shelf_hz = _num(row, "direct_hf_shelf_hz", 5000.0)
    if s <= 0.0:
        return StrengthParams(
            strength=0.0,
            width_deg=30.0,
            ambient_ratio=0.0,
            early_level=0.0,
            late_level=0.0,
            drr_offset_db=0.0,
            side_decor_gain=0.0,
            bypass=True,
            t60=t60,
            crossover_hz=crossover,
            coherence_floor=floor,
            coherence_power=power,
            pan_gamma=gamma,
            wide_deg=wide,
            rear_deg=rear,
            direct_bleed=0.0,
            hf_pan_floor=hf_floor,
            hf_pan_start_hz=hf_start,
            hf_pan_end_hz=hf_end,
            hf_direct_start_hz=hf_direct_start,
            hf_direct_end_hz=hf_direct_end,
            bed_peak_db=bed_db,
            bed_peak_lo_hz=bed_lo,
            bed_peak_hi_hz=bed_hi,
            side_lpf_hz=side_lpf,
            direct_hf_shelf_db=shelf_db,
            direct_hf_shelf_hz=shelf_hz,
            delays_ms=delays,
            hrtf_key=_hrtf_alias(hrtf_name),
        )
    width_0 = _num(row, "width_deg_at_0", 48.0)
    width_1 = _num(row, "width_deg_at_1")
    side_0 = _num(row, "side_decor_gain_at_0", 2.05)
    side_1 = _num(row, "side_decor_gain_at_1")
    bleed_1 = _num(row, "direct_bleed_at_1", 0.0)
    bands, eq = _eq_for(row, hrtf_name, s)
    return StrengthParams(
        strength=s,
        width_deg=width_0 + s * (width_1 - width_0),
        ambient_ratio=s * _num(row, "ambient_ratio_at_1"),
        early_level=s * _num(row, "early_level_at_1"),
        late_level=s * _num(row, "late_level_at_1"),
        drr_offset_db=s * _num(row, "drr_offset_db_at_1"),
        side_decor_gain=side_0 + s * (side_1 - side_0),
        bypass=False,
        t60=t60,
        crossover_hz=crossover,
        coherence_floor=floor,
        coherence_power=power,
        pan_gamma=gamma,
        wide_deg=wide,
        rear_deg=rear,
        direct_bleed=s * bleed_1,
        hf_pan_floor=hf_floor,
        hf_pan_start_hz=hf_start,
        hf_pan_end_hz=hf_end,
        hf_direct_start_hz=hf_direct_start,
        hf_direct_end_hz=hf_direct_end,
        bed_peak_db=bed_db,
        bed_peak_lo_hz=bed_lo,
        bed_peak_hi_hz=bed_hi,
        side_lpf_hz=side_lpf,
        direct_hf_shelf_db=shelf_db,
        direct_hf_shelf_hz=shelf_hz,
        delays_ms=delays,
        eq_bands_hz=bands,
        eq_gain_db=eq,
        hrtf_key=_hrtf_alias(hrtf_name),
    )


def np_clip(x: float, a: float, b: float) -> float:
    return a if x < a else b if x > b else x
