"""Strength → perceptual parameter mapping (plan §6.8). NO dry/wet mix."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrengthParams:
    strength: float
    width_deg: float
    ambient_ratio: float
    early_level: float
    late_level: float
    drr_offset_db: float
    bypass: bool


def map_strength(s: float, preset: str = "room") -> StrengthParams:
    """Map strength ∈ [0,1] to rendering parameters. s=0 → full bypass."""
    s = float(np_clip(s, 0.0, 1.0))
    if s <= 0.0:
        return StrengthParams(
            strength=0.0,
            width_deg=30.0,
            ambient_ratio=0.0,
            early_level=0.0,
            late_level=0.0,
            drr_offset_db=0.0,
            bypass=True,
        )
    if preset == "surround":
        width_1, amb_1, early_1, late_1, drr_1 = 60.0, 1.0, 1.0, 0.9, -6.0
    elif preset == "orbit":
        width_1, amb_1, early_1, late_1, drr_1 = 45.0, 0.8, 0.6, 0.5, -3.0
    else:  # room
        width_1, amb_1, early_1, late_1, drr_1 = 45.0, 0.7, 0.8, 0.55, -4.0
    return StrengthParams(
        strength=s,
        width_deg=30.0 + s * (width_1 - 30.0),
        ambient_ratio=s * amb_1,
        early_level=s * early_1,
        late_level=s * late_1,
        drr_offset_db=s * drr_1,
        bypass=False,
    )


def np_clip(x: float, a: float, b: float) -> float:
    return a if x < a else b if x > b else x
