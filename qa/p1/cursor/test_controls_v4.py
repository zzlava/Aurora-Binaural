#!/usr/bin/env python3
"""P1b-cursor-01 checks for the five route V macro controls.

Direction (prototype, SADIE II, preset V3, strength 1):
  width up lowers IACC, room up lowers DRR, voice distance up lowers the
  voice DRR, height up raises the height-layer energy.

Rust: default controls match a strength-only render sample for sample,
a mid-stream control change stays finite and under the true-peak ceiling,
and its per-channel first difference stays in the same range as the tone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "prototypes"))

from aurora_p0.engine import Processor, RenderConfig  # noqa: E402
from aurora_p0.loudness import true_peak_dbtp  # noqa: E402
from aurora_p0.metrics import iacc  # noqa: E402
from aurora_p0.route_v import map_route_v, resolve_controls  # noqa: E402

SOFA = ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa"
PACK = ROOT / "data/hrtf/sadie_d1_frontal.ahrp"
PRESET = ROOT / "presets/route_v.toml"
SR = 48_000
V3 = {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.25}


def _noise(kind: str, n: int = 24_000) -> np.ndarray:
    rng = np.random.default_rng(7)
    if kind == "voice":
        mono = rng.standard_normal(n) * 0.04
        return np.stack([mono, mono])
    bed = rng.standard_normal((2, n)) * 0.04
    if kind == "bed-tail":
        silence = np.zeros((2, int(1.2 * SR)), dtype=np.float64)
        return np.concatenate([bed, silence], axis=1)
    return bed


def _tail_drr(stereo: np.ndarray, burst: int) -> float:
    # Skip the delayed direct and the early taps; the rest is the late tail.
    direct = float(np.sum(stereo[:, :burst] ** 2))
    tail = float(np.sum(stereo[:, burst + 4096 :] ** 2))
    return 10.0 * np.log10((direct + 1e-20) / (tail + 1e-20))


def _render(audio: np.ndarray, controls: dict) -> tuple[np.ndarray, dict]:
    cfg = RenderConfig(
        route="V",
        strength=float(controls.get("strength", 1.0)),
        preset="V3",
        hrtf_path=SOFA,
        hrtf_name="sadie",
        eq_mode="frontal",
        directional_power=True,
        seed=20261016,
        block_size=256,
        sample_rate=SR,
        controls=controls,
    )
    proc = Processor()
    proc.prepare(SR, 256, cfg)
    out = proc.process_file(audio)
    return out, proc._route_v.last_stats


def _series(name: str, kind: str, key: str, read) -> list[float]:
    audio = _noise(kind)
    values = []
    print(f"\n{name}")
    for control in (0.0, 0.5, 1.0):
        controls = dict(V3)
        controls[key] = control
        _out, stats = _render(audio, controls)
        values.append(read(_out, stats))
        print(f"  {key}={control:.1f}  {values[-1]:.6f}")
    return values


def _decreasing(values: list[float], label: str) -> None:
    if not (values[0] > values[1] > values[2]):
        raise SystemExit(f"{label} did not fall: {values}")


def _increasing(values: list[float], label: str) -> None:
    if not (values[0] < values[1] < values[2]):
        raise SystemExit(f"{label} did not rise: {values}")


def prototype_directions() -> None:
    mapped = map_route_v(1.0, "V3", "sadie", controls=V3)
    plain = map_route_v(1.0, "V3", "sadie")
    for field in (
        "front_az",
        "wide_lin",
        "height_lin",
        "height_el",
        "ceiling_db",
        "late_lin",
        "t60",
        "center_distance_m",
        "direct_room_send_db",
        "center_spread_deg",
    ):
        if getattr(mapped, field) != getattr(plain, field):
            raise SystemExit(f"default expand moved {field}")
    defaults = resolve_controls("V3", 1.0)
    if defaults != V3:
        raise SystemExit(f"V3 defaults {defaults} != {V3}")

    iaccs = _series(
        "width → IACC",
        "bed",
        "width",
        lambda out, _stats: iacc(out, SR),
    )
    _decreasing(iaccs, "IACC")

    # Early taps sit on the preset and do not follow the room control.
    # DRR here is the burst against the reverberant tail after it ends.
    drrs = _series(
        "room → tail DRR",
        "bed-tail",
        "room",
        lambda out, _stats: _tail_drr(out, 24_000),
    )
    _decreasing(drrs, "tail DRR")

    voice = _series(
        "voice_distance → voice DRR",
        "voice",
        "voice_distance",
        lambda _out, stats: float(stats["drr_center_db"]),
    )
    _decreasing(voice, "center DRR")

    height = _series(
        "height → height energy",
        "bed",
        "height",
        lambda _out, stats: float(stats["height_bus_rms"]),
    )
    _increasing(height, "height bus")


def _channel_step(stereo: np.ndarray, start: int, end: int) -> float:
    peak = 0.0
    end = min(end, stereo.shape[1])
    for channel in stereo:
        prev = channel[start]
        for sample in channel[start + 1 : end]:
            peak = max(peak, abs(float(sample) - float(prev)))
            prev = sample
    return peak


def rust_checks() -> None:
    from aurora_core import Renderer

    n = 8192
    split = 4096
    tone = 0.12 * np.sin(2.0 * np.pi * 200.0 * np.arange(n) / SR)
    stereo = np.stack([tone, tone]).astype(np.float64)

    def render(controls: dict | None, block: int, audio: np.ndarray) -> np.ndarray:
        renderer = Renderer(
            str(PACK),
            str(SOFA),
            str(PRESET),
            strength=1.0,
            seed=20261016,
            preset="V3",
            limiter=True,
            route="V",
            hrtf_name="sadie",
        )

        class Config:
            input_channels = 2
            output_channels = 2

        renderer.prepare(SR, 4096, Config())
        if controls is not None:
            renderer.set_controls(controls)
        interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
        pieces = []
        for start in range(0, interleaved.shape[0], block):
            pieces.append(np.asarray(renderer.process(interleaved[start : start + block]), dtype=np.float64))
        return np.concatenate(pieces, axis=0).T

    plain = render(None, 256, stereo[:, :split])
    explicit = render(V3, 256, stereo[:, :split])
    err = float(np.max(np.abs(plain - explicit)))
    print(f"\ndefault controls vs strength-only max abs {err}")
    if err != 0.0:
        raise SystemExit(f"default controls changed the render: {err}")

    probe = Renderer(
        str(PACK), str(SOFA), str(PRESET),
        strength=1.0, seed=20261016, preset="V3", limiter=True, route="V", hrtf_name="sadie",
    )

    class Config:
        input_channels = 2
        output_channels = 2

    probe.prepare(SR, 256, Config())
    probe.set_controls(V3)
    params = probe.effective_params()
    print("effective", {k: params[k] for k in ("front_az", "height_el", "t60", "center_distance_m", "wide_db", "bypass")})
    if params["front_az"] != 45.0 or params["height_el"] != 55.0 or params["t60"] != 0.95:
        raise SystemExit(f"effective params are not V3: {params}")
    if params["controls"] != V3:
        raise SystemExit(f"reported controls {params['controls']}")

    renderer = Renderer(
        str(PACK), str(SOFA), str(PRESET),
        strength=1.0, seed=20261016, preset="V3", limiter=True, route="V", hrtf_name="sadie",
    )
    renderer.prepare(SR, 256, Config())
    block = 256
    interleaved = np.ascontiguousarray(stereo.T, dtype=np.float32)
    pieces = []
    for start in range(0, split, block):
        pieces.append(np.asarray(renderer.process(interleaved[start : start + block]), dtype=np.float64))
    renderer.set_controls({"strength": 0.35, "width": 1.0, "height": 0.0, "room": 1.0, "voice_distance": 1.0})
    for start in range(split, n, block):
        pieces.append(np.asarray(renderer.process(interleaved[start : start + block]), dtype=np.float64))
    out = np.concatenate(pieces, axis=0).T
    if not np.isfinite(out).all():
        raise SystemExit("mid-stream change produced NaN or Inf")
    peak = true_peak_dbtp(out, SR)
    before = _channel_step(out, 1024, split)
    after = _channel_step(out, split, n)
    print(f"mid-stream true peak {peak:.2f} dBTP, step before {before:.5f} after {after:.5f}")
    if peak > -1.0:
        raise SystemExit(f"true peak {peak} dBTP exceeds -1")
    if after > max(before, 0.02) * 8.0:
        raise SystemExit(f"step {after} vs steady {before}")

    ref = render(None, 256, stereo[:, :4096])
    for block in (64, 1024):
        got = render(None, block, stereo[:, :4096])
        db = 20.0 * np.log10(np.max(np.abs(ref - got)) + 1e-20)
        print(f"block {block} vs 256: {db:.1f} dBFS")
        if db > -100.0:
            raise SystemExit(f"block {block} diverged at {db} dBFS")


def main() -> None:
    prototype_directions()
    rust_checks()
    print("\nall control checks passed")


if __name__ == "__main__":
    main()
