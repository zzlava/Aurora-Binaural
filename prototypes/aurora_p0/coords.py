"""Coordinate conventions (plan §6.9).

+X front, +Y left, +Z up.
Azimuth: degrees, positive to the left (CCW from +X when viewed from above).
Elevation: degrees, positive upward.
Distance: metres.
"""

from __future__ import annotations

import numpy as np


def az_el_to_cart(azimuth_deg: float, elevation_deg: float, radius: float = 1.0) -> np.ndarray:
    az = np.deg2rad(azimuth_deg)
    el = np.deg2rad(elevation_deg)
    x = radius * np.cos(el) * np.cos(az)
    y = radius * np.cos(el) * np.sin(az)
    z = radius * np.sin(el)
    return np.array([x, y, z], dtype=np.float64)


def cart_to_az_el(xyz: np.ndarray) -> tuple[float, float, float]:
    x, y, z = np.asarray(xyz, dtype=np.float64)
    r = float(np.linalg.norm([x, y, z]))
    if r < 1e-12:
        return 0.0, 0.0, 0.0
    az = float(np.rad2deg(np.arctan2(y, x)))
    el = float(np.rad2deg(np.arcsin(np.clip(z / r, -1.0, 1.0))))
    return az, el, r


def angular_distance_deg(az1: float, el1: float, az2: float, el2: float) -> float:
    a = az_el_to_cart(az1, el1)
    b = az_el_to_cart(az2, el2)
    c = float(np.clip(np.dot(a, b), -1.0, 1.0))
    return float(np.rad2deg(np.arccos(c)))
