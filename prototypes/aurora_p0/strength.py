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
    side_decor_gain: float
    bypass: bool


def map_strength(s: float, preset: str = "room") -> StrengthParams:
    """Map strength ∈ [0,1] to rendering parameters. s=0 → full bypass.

    ``side_decor_gain`` scales an allpass-decorrelated side feed (L−R). It is
    not a dry/wet crossfade: the dry stereo is never mixed back in.
    """
    s = float(np_clip(s, 0.0, 1.0))
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
        )
    if preset == "surround":
        width_1, amb_1, early_1, late_1, drr_1, side_1 = 90.0, 1.0, 1.0, 0.85, -8.0, 2.6
    elif preset == "orbit":
        width_1, amb_1, early_1, late_1, drr_1, side_1 = 70.0, 0.85, 0.6, 0.5, -4.0, 2.2
    else:  # room
        width_1, amb_1, early_1, late_1, drr_1, side_1 = 80.0, 1.0, 0.9, 0.7, -6.0, 2.4
    # Floor is already high: HRTF crossfeed at moderate widths lifts music
    # IACC, and the cap is dry+0.1 even at strength 0.4. Gain still grows with s.
    side = 2.05 + s * (side_1 - 2.05)
    return StrengthParams(
        strength=s,
        width_deg=48.0 + s * (width_1 - 48.0),
        ambient_ratio=s * amb_1,
        early_level=s * early_1,
        late_level=s * late_1,
        drr_offset_db=s * drr_1,
        side_decor_gain=side,
        bypass=False,
    )


def np_clip(x: float, a: float, b: float) -> float:
    return a if x < a else b if x > b else x
