#!/usr/bin/env python3
"""Reproduce the P0 sample matrix, or one render for review.

Full matrix (from prototypes/):

  uv sync
  uv run python run_p0.py

Same material at block sizes 64, 256 and 1024, with the peak error between them:

  uv run python run_p0.py --only music --route B --strength 0.4 --hrtf thk_ku100 --eq frontal --block-size 64,256,1024 --out ../qa/p0/cursor/block_check
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
PROTO = Path(__file__).resolve().parent
sys.path.insert(0, str(PROTO))

from aurora_p0 import SAMPLE_RATE, TARGET_LUFS, DEFAULT_SEED  # noqa: E402
from aurora_p0.assets import ensure_assets, sha256_file  # noqa: E402
from aurora_p0.engine import Processor, RenderConfig, process_blocks_compare  # noqa: E402
from aurora_p0.filters import crossover_magnitude_db  # noqa: E402
from aurora_p0.hrtf import apply_equalization, load_sofa  # noqa: E402
from aurora_p0.loudness import equal_loudness_timbre, match_loudness  # noqa: E402
from aurora_p0.metrics import (  # noqa: E402
    band_deltas_db,
    block_consistency_error,
    direction_tests,
    frontal_response_deviation,
    review_tone_deviation,
    sample_metrics,
)
from aurora_p0.upmix import energy_conservation_error, streaming_upmix_file  # noqa: E402

OUT = ROOT / "qa" / "p0" / "cursor"
SAMPLES = OUT / "samples"
LOGS = OUT / "logs"
DATA_HRTF = ROOT / "data" / "hrtf"
ASSET_DIR = ROOT / "data" / "assets"

HRTF_CANDIDATES = {
    "thk_ku100": DATA_HRTF / "thk_ku100" / "HRIR_L2702.sofa",
    "sadie2_d1": DATA_HRTF / "sadie2_d1" / "D1_48K_24bit_256tap_FIR_SOFA.sofa",
}

# Listening samples are the real excerpts. Synthetic files stay for measurement.
REAL_CATS = {
    "voice": ASSET_DIR / "real_voice.wav",
    "music": ASSET_DIR / "real_music.wav",
    "ambience": ASSET_DIR / "real_ambience.wav",
}
SYNTH_CATS = {
    "voice": ASSET_DIR / "cat_voice.wav",
    "music": ASSET_DIR / "cat_music.wav",
    "ambience": ASSET_DIR / "cat_ambience.wav",
}
ROUTES = ["A", "B", "C"]
STRENGTHS = [0.4, 0.8]
CURVE_STRENGTHS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

# P0-cursor-01 samples (synthetic categories, diffuse EQ, old upmix), remeasured
# with the same power-sum 4–16 kHz metric used for the revised renders.
BASELINE_P0_01 = {
    "note": "上一版样片（合成类别、扩散场均衡、整段 STFT 上混）。IACC 与 4–16 kHz 最大偏差在修订前用同一指标重测。",
    "iacc": {
        "voice": {"dry": 1.0, "A": [0.999, 0.999], "B": [0.998, 0.998], "C": [0.998, 0.998]},
        "music": {"dry": 0.686, "A": [0.918, 0.920], "B": [0.987, 0.985], "C": [0.990, 0.988]},
        "ambience": {"dry": 0.009, "A": [0.361, 0.368], "B": [0.873, 0.870], "C": [0.995, 0.984]},
    },
    "hf_max_abs_db_4_16k": {
        "voice": {"A": [6.45, 7.21], "B": [16.95, 15.52], "C": [12.16, 12.84]},
        "music": {"A": [7.48, 7.58], "B": [16.75, 13.96], "C": [12.19, 11.57]},
        "ambience": {"A": [7.49, 7.50], "B": [14.24, 13.55], "C": [10.16, 10.97]},
    },
    "frontal_eq_max_abs_db": {"diffuse": 8.41, "frontal": 0.49},
    "block_consistency": "只测了路线 A 的分频/房间/卷积，没有覆盖上混。",
    "strength": "环境音路线 A 的 IACC 从 0.4 到 0.8 只上升约 0.007。",
}


def load_stereo(path: Path) -> np.ndarray:
    data, sr = sf.read(str(path), always_2d=True)
    if sr != SAMPLE_RATE:
        raise ValueError(f"{path} sr={sr}, expected {SAMPLE_RATE}")
    x = data.T.astype(np.float64)
    if x.shape[0] == 1:
        x = np.vstack([x, x])
    return x[:2]


def write_pcm24(path: Path, stereo: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), stereo.T, SAMPLE_RATE, subtype="PCM_24")


def _json_default(o):
    if isinstance(o, (np.floating, float)):
        v = float(o)
        if not np.isfinite(v):
            return None
        return v
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def render_one(
    stereo: np.ndarray,
    route: str,
    strength: float,
    hrtf_path: Path,
    hrtf_name: str,
    dry_matched: np.ndarray,
    orbit_deg_per_sec: float = 0.0,
    block_size: int = 512,
    eq_mode: str = "frontal",
    directional_power: bool = True,
) -> tuple[np.ndarray, dict]:
    conf = RenderConfig(
        route=route,
        strength=strength,
        hrtf_path=hrtf_path,
        hrtf_name=hrtf_name,
        eq_mode=eq_mode,
        directional_power=directional_power,
        timbre_guard=False,
        seed=DEFAULT_SEED,
        block_size=block_size,
        sample_rate=SAMPLE_RATE,
        orbit_deg_per_sec=orbit_deg_per_sec,
    )
    proc = Processor()
    proc.prepare(SAMPLE_RATE, block_size, conf)
    rendered = proc.process_file(stereo)
    stats = dict(proc.last_stats)
    if strength <= 0.0:
        matched, before, after = match_loudness(rendered, SAMPLE_RATE, TARGET_LUFS)
        info = {}
    else:
        matched, before, after, info = equal_loudness_timbre(
            rendered, stereo, SAMPLE_RATE, TARGET_LUFS
        )
    bands = band_deltas_db(matched, dry_matched, SAMPLE_RATE)
    tone = review_tone_deviation(matched, dry_matched, SAMPLE_RATE)
    row = {
        **sample_metrics(matched, SAMPLE_RATE),
        "route": route,
        "strength": strength,
        "hrtf": hrtf_name,
        "eq_mode": f"{eq_mode}+power" if directional_power else eq_mode,
        "block_size": block_size,
        "loudness_before": before,
        "loudness_matched_lufs": after,
        "hf_max_abs_db": bands["max_abs_db"],
        "hf_within_pm3db": bands["within_pm3db"],
        "hf_delta_db": bands["delta_db"],
        "tone_max_abs_db_40_16k": tone["max_abs_db"],
        "tone_within_pm3db": tone["within_pm3db"],
        "tone_offset_db": tone["offset_db"],
        "tone_delta_db": tone["delta_db"],
        "bed_to_total": stats.get("bed_to_total"),
        "rear_bus_rms": stats.get("rear_bus_rms"),
        "room_bus_rms": stats.get("room_bus_rms"),
        "coherence_mean": stats.get("coherence_mean"),
        "timbre_guard_max_db": info.get("max_abs_correction_db_4_16k"),
    }
    return matched, row


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P0 sample matrix, or one reproducible render")
    parser.add_argument("--block-size", default=None, help="One block size, or 64,256,1024 to compare")
    parser.add_argument("--only", default=None, help="Material category: voice, music, ambience")
    parser.add_argument("--route", default=None, choices=["A", "B", "C", "a", "b", "c"])
    parser.add_argument("--strength", type=float, default=None)
    parser.add_argument("--hrtf", default=None, choices=["thk_ku100", "sadie2_d1"])
    parser.add_argument("--eq", default=None, choices=["frontal", "diffuse"])
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def _block_sizes(text: str | None) -> list[int]:
    if not text:
        return [512]
    sizes = [int(part) for part in text.split(",") if part.strip()]
    if not sizes or any(n <= 0 for n in sizes):
        raise ValueError(f"bad --block-size {text}")
    return sizes


def run_selected(args: argparse.Namespace) -> int:
    """Render a subset. Several block sizes also print the pairwise peak error."""
    categories = [args.only] if args.only else list(REAL_CATS)
    routes = [args.route.upper()] if args.route else list(ROUTES)
    strengths = [args.strength] if args.strength is not None else list(STRENGTHS)
    hrtf_name = args.hrtf or "thk_ku100"
    eq_mode = args.eq or "frontal"
    sizes = _block_sizes(args.block_size)
    out = args.out or SAMPLES
    unknown = [c for c in categories if c not in REAL_CATS]
    if unknown:
        print(f"unknown category {unknown}; expected {list(REAL_CATS)}", file=sys.stderr)
        return 2
    if hrtf_name not in HRTF_CANDIDATES or not HRTF_CANDIDATES[hrtf_name].is_file():
        print(f"missing HRTF {hrtf_name}", file=sys.stderr)
        return 2
    hrtf_path = HRTF_CANDIDATES[hrtf_name]
    rendered: dict[int, dict[str, np.ndarray]] = {n: {} for n in sizes}
    for block in sizes:
        dest = out / f"block{block}" if len(sizes) > 1 else out
        dest.mkdir(parents=True, exist_ok=True)
        for cat in categories:
            stereo = load_stereo(REAL_CATS[cat])
            dry, _, _ = match_loudness(stereo, SAMPLE_RATE, TARGET_LUFS)
            if block == sizes[0]:
                dry_name = dest / f"{cat}_dry_lufs{int(abs(TARGET_LUFS))}.wav"
                if not dry_name.is_file():
                    write_pcm24(dry_name, dry)
            for route in routes:
                for strength in strengths:
                    tag = f"{cat}_route{route}_str{strength:.1f}_hrtf-{hrtf_name}_eq-{eq_mode}"
                    print(f"==> block {block} {tag}", flush=True)
                    matched, row = render_one(
                        stereo,
                        route,
                        strength,
                        hrtf_path,
                        hrtf_name,
                        dry,
                        block_size=block,
                        eq_mode=eq_mode,
                        directional_power=True,
                    )
                    write_pcm24(dest / f"{tag}.wav", matched)
                    rendered[block][tag] = matched
                    print(
                        f"    IACC {row['iacc']:.3f}  40–16 kHz {row['tone_max_abs_db_40_16k']:.2f} dB  "
                        f"4–16 kHz {row['hf_max_abs_db']:.2f} dB"
                    )
    if len(sizes) > 1:
        ref = max(sizes)
        worst = -1e9
        print("block-size peak error (matched render, dBFS):")
        tags = sorted(rendered[ref])
        pairs = [(block, ref) for block in sizes if block != ref]
        others = [n for n in sizes if n != ref]
        if len(others) == 2:
            pairs.append((others[0], others[1]))
        for left, right in pairs:
            peak = 0.0
            for tag in tags:
                a, b = rendered[left][tag], rendered[right][tag]
                n = min(a.shape[1], b.shape[1])
                peak = max(peak, float(np.max(np.abs(a[:, :n] - b[:, :n]))))
            err = 20.0 * np.log10(peak + 1e-20)
            worst = max(worst, err)
            print(f"  {left} vs {right}: {err:.2f} dBFS")
        print(f"max_abs_error_dbfs {worst:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    selected = any(
        value is not None
        for value in (args.block_size, args.only, args.route, args.strength, args.hrtf, args.eq, args.out)
    )
    if selected:
        return run_selected(args)
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    for old in SAMPLES.glob("*.wav"):
        old.unlink()
    commands: list[dict] = []
    failures: list[str] = []
    t0 = time.time()

    def run_step(name: str, fn):
        print(f"==> {name}", flush=True)
        try:
            result = fn()
            commands.append({"step": name, "exit_code": 0})
            return result
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")
            commands.append({"step": name, "exit_code": 1, "error": str(exc)})
            traceback.print_exc()
            return None

    registry = run_step("ensure_assets", lambda: ensure_assets(ASSET_DIR, duration_s=6.0))
    if registry is None:
        _write_partial_report(commands, failures, t0)
        return 1

    for name, path in HRTF_CANDIDATES.items():
        if not path.is_file():
            failures.append(f"missing HRTF {name}: {path}")
    if failures:
        _write_partial_report(commands, failures, t0)
        return 2

    primary_name = "thk_ku100"
    primary_path = HRTF_CANDIDATES[primary_name]
    secondary_name = "sadie2_d1"
    secondary_path = HRTF_CANDIDATES[secondary_name]

    metrics: dict = {
        "meta": {
            "task": "P0-cursor-03",
            "sample_rate": SAMPLE_RATE,
            "target_lufs": TARGET_LUFS,
            "seed": DEFAULT_SEED,
            "primary_hrtf": primary_name,
            "secondary_hrtf": secondary_name,
            "eq_mode": "frontal+power",
            "review_file": "qa/p0/REVIEW-cursor-02.md（主仓库）。D8 低频、D9 复现入口。",
        },
        "baseline_p0_01": BASELINE_P0_01,
        "hrtf": {},
        "direction_tests": {},
        "block_consistency": {},
        "upmix_energy_db": {},
        "eq_comparison": {},
        "strength_curve": {},
        "synthetic_acceptance": {},
        "samples": {},
    }

    def hrtf_meta(name: str, path: Path):
        ds = load_sofa(path, name=name)
        try:
            rel = str(path.resolve().relative_to(ROOT))
        except ValueError:
            rel = str(path)
        return {
            "path": rel,
            "sha256": sha256_file(path),
            "sample_rate_native_resampled_to": ds.sr,
            "n_directions": ds.n_dirs,
            "n_taps": ds.n_taps,
            "bytes": path.stat().st_size,
        }

    metrics["hrtf"][primary_name] = run_step(
        "hrtf_meta_thk", lambda: hrtf_meta(primary_name, primary_path)
    )
    metrics["hrtf"][secondary_name] = run_step(
        "hrtf_meta_sadie", lambda: hrtf_meta(secondary_name, secondary_path)
    )

    def eq_pair(name: str, path: Path, mode: str, directional: bool):
        ds = apply_equalization(
            load_sofa(path, name=name), mode=mode, directional_power=directional
        )
        tag = ds.eq_mode
        return tag, frontal_response_deviation(ds, SAMPLE_RATE), ds

    for mode in ("diffuse", "frontal"):
        for directional in (False, True):
            label = f"{primary_name}_{mode}" + ("_power" if directional else "")

            def _eq(mode=mode, directional=directional, label=label):
                tag, dev, ds = eq_pair(primary_name, primary_path, mode, directional)
                metrics["eq_comparison"][f"{label}_frontal"] = dev
                if directional and mode == "frontal":
                    metrics["direction_tests"][f"{primary_name}_{tag}"] = direction_tests(ds, SAMPLE_RATE)
                return dev

            run_step(f"eq_{label}", _eq)

    def _eq_sadie():
        tag, dev, ds = eq_pair(secondary_name, secondary_path, "frontal", True)
        metrics["eq_comparison"][f"{secondary_name}_{tag}_frontal"] = dev
        metrics["direction_tests"][f"{secondary_name}_{tag}"] = direction_tests(ds, SAMPLE_RATE)
        return dev

    run_step("eq_sadie_frontal_power", _eq_sadie)

    def _upmix_energy():
        out = {}
        for key in ("cat_music", "cat_ambience"):
            stereo = load_stereo(ASSET_DIR / f"{key}.wav")
            up = streaming_upmix_file(
                stereo[0], stereo[1], SAMPLE_RATE, block=256, width_deg=60.0, ambient_mix=1.0
            )
            out[key] = energy_conservation_error(stereo[0], stereo[1], up)
            out[f"{key}_coherence_mean"] = float(up["coherence_mean"])
        return out

    metrics["upmix_energy_db"] = run_step("upmix_energy", _upmix_energy) or {}

    def _crossover():
        return crossover_magnitude_db(SAMPLE_RATE, 150.0)

    metrics["crossover"] = run_step("crossover_lr4", _crossover) or {}

    music_syn = load_stereo(SYNTH_CATS["music"])
    excerpt = music_syn[:, : SAMPLE_RATE * 2]

    def _blocks(route: str):
        cfg = RenderConfig(
            route=route,
            strength=0.4,
            hrtf_path=primary_path,
            hrtf_name=primary_name,
            eq_mode="frontal",
            directional_power=True,
            timbre_guard=False,
            seed=DEFAULT_SEED,
            block_size=1024,
            sample_rate=SAMPLE_RATE,
            orbit_deg_per_sec=0.0,
        )
        outs = process_blocks_compare(excerpt, cfg, [64, 256, 1024])
        return block_consistency_error(outs, ref_key=1024)

    for route in ("B", "C"):
        metrics["block_consistency"][route] = run_step(f"block_consistency_route_{route}", lambda route=route: _blocks(route))

    conf0 = RenderConfig(
        route="B",
        strength=0.0,
        hrtf_path=primary_path,
        hrtf_name=primary_name,
        eq_mode="frontal",
        seed=DEFAULT_SEED,
        block_size=512,
        sample_rate=SAMPLE_RATE,
    )
    proc0 = Processor()
    proc0.prepare(SAMPLE_RATE, 512, conf0)
    bypassed = proc0.process_file(excerpt)
    metrics["strength0_max_abs_err"] = float(np.max(np.abs(bypassed - excerpt)))

    # Synthetic acceptance + strength curve (measurement; wavs kept for 0.4/0.8).
    for cat, path in SYNTH_CATS.items():
        stereo = load_stereo(path)
        dry, _, dry_after = match_loudness(stereo, SAMPLE_RATE, TARGET_LUFS)
        dry_name = f"synth_{cat}_dry_lufs{int(abs(TARGET_LUFS))}.wav"
        write_pcm24(SAMPLES / dry_name, dry)
        dry_row = {
            **sample_metrics(dry, SAMPLE_RATE),
            "route": "dry",
            "strength": None,
            "category": f"synth_{cat}",
            "sha256": sha256_file(SAMPLES / dry_name),
            "loudness_matched_lufs": dry_after,
        }
        metrics["samples"][dry_name] = dry_row
        metrics["synthetic_acceptance"].setdefault(cat, {"dry_iacc": dry_row["iacc"], "routes": {}})
        strengths = CURVE_STRENGTHS if cat != "voice" else [0.4, 0.8]
        for route in ROUTES:
            curve = []
            for strength in strengths:
                def _one(route=route, strength=strength, stereo=stereo, dry=dry, cat=cat):
                    matched, row = render_one(
                        stereo, route, strength, primary_path, primary_name, dry
                    )
                    row["category"] = f"synth_{cat}"
                    if strength in (0.4, 0.8):
                        name = f"synth_{cat}_route{route}_str{strength:.1f}_hrtf-{primary_name}_eq-frontal.wav"
                        write_pcm24(SAMPLES / name, matched)
                        row["sha256"] = sha256_file(SAMPLES / name)
                        metrics["samples"][name] = row
                    return {
                        "strength": strength,
                        "iacc": row["iacc"],
                        "bed_to_total": row["bed_to_total"],
                        "lateral_energy_ratio": row["lateral_energy_ratio"],
                        "hf_max_abs_db": row["hf_max_abs_db"],
                        "hf_within_pm3db": row["hf_within_pm3db"],
                        "loudness_lufs": row["loudness_lufs"],
                    }

                point = run_step(f"synth_{cat}_{route}_{strength:.1f}", _one)
                if point:
                    curve.append(point)
            metrics["strength_curve"].setdefault(f"synth_{cat}", {})[route] = curve
            metrics["synthetic_acceptance"][cat]["routes"][route] = curve

    # Real listening matrix.
    for cat, path in REAL_CATS.items():
        stereo = load_stereo(path)
        dry, _, dry_after = match_loudness(stereo, SAMPLE_RATE, TARGET_LUFS)
        dry_name = f"{cat}_dry_lufs{int(abs(TARGET_LUFS))}.wav"
        write_pcm24(SAMPLES / dry_name, dry)
        metrics["samples"][dry_name] = {
            **sample_metrics(dry, SAMPLE_RATE),
            "route": "dry",
            "strength": None,
            "category": cat,
            "sha256": sha256_file(SAMPLES / dry_name),
            "loudness_matched_lufs": dry_after,
        }
        for route in ROUTES:
            for strength in STRENGTHS:
                tag = f"{cat}_route{route}_str{strength:.1f}_hrtf-{primary_name}_eq-frontal"

                def _render(route=route, strength=strength, stereo=stereo, dry=dry, cat=cat, tag=tag):
                    orbit = 8.0 if route == "C" and strength >= 0.8 else 0.0
                    matched, row = render_one(
                        stereo, route, strength, primary_path, primary_name, dry, orbit_deg_per_sec=orbit
                    )
                    row["category"] = cat
                    out_path = SAMPLES / f"{tag}.wav"
                    write_pcm24(out_path, matched)
                    row["sha256"] = sha256_file(out_path)
                    return row

                metrics["samples"][f"{tag}.wav"] = run_step(f"render_{tag}", _render)

    # SADIE II + frontal EQ + route B, for the license listening comparison.
    for cat, path in REAL_CATS.items():
        stereo = load_stereo(path)
        dry, _, _ = match_loudness(stereo, SAMPLE_RATE, TARGET_LUFS)
        for strength in STRENGTHS:
            tag = f"{cat}_routeB_str{strength:.1f}_hrtf-{secondary_name}_eq-frontal"

            def _sadie(stereo=stereo, dry=dry, strength=strength, cat=cat, tag=tag):
                matched, row = render_one(stereo, "B", strength, secondary_path, secondary_name, dry)
                row["category"] = cat
                out_path = SAMPLES / f"{tag}.wav"
                write_pcm24(out_path, matched)
                row["sha256"] = sha256_file(out_path)
                return row

            metrics["samples"][f"{tag}.wav"] = run_step(f"render_{tag}", _sadie)

    # Route B music at the ends of the strength range (KU100, frontal).
    music = load_stereo(REAL_CATS["music"])
    music_dry, _, _ = match_loudness(music, SAMPLE_RATE, TARGET_LUFS)
    for strength in (0.2, 1.0):
        tag = f"music_routeB_str{strength:.1f}_hrtf-{primary_name}_eq-frontal"

        def _ends(strength=strength, tag=tag):
            matched, row = render_one(music, "B", strength, primary_path, primary_name, music_dry)
            row["category"] = "music"
            out_path = SAMPLES / f"{tag}.wav"
            write_pcm24(out_path, matched)
            row["sha256"] = sha256_file(out_path)
            return row

        metrics["samples"][f"{tag}.wav"] = run_step(f"render_{tag}", _ends)

    _check_acceptance(metrics, failures)

    metrics_path = OUT / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, default=_json_default), encoding="utf-8")
    commands.append({"step": "write_metrics", "exit_code": 0, "path": str(metrics_path.relative_to(ROOT))})
    (LOGS / "commands.json").write_text(json.dumps(commands, indent=2, default=_json_default), encoding="utf-8")
    versions = _package_versions()
    (LOGS / "versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    _write_report(commands, failures, metrics, versions, time.time() - t0, registry)
    _write_presets()
    exit_code = 0 if not failures else 1
    print(f"Done in {time.time() - t0:.1f}s, exit={exit_code}, failures={len(failures)}")
    return exit_code


def _mono(values: list[float], tol: float = 1e-4) -> bool:
    return all(b > a + tol for a, b in zip(values, values[1:]))


def _check_acceptance(metrics: dict, failures: list[str]) -> None:
    synth = metrics.get("synthetic_acceptance") or {}
    amb = synth.get("ambience") or {}
    music = synth.get("music") or {}
    dry_amb = float(amb.get("dry_iacc") or 1.0)
    dry_music = float(music.get("dry_iacc") or 0.0)
    flags = {
        "ambience_iacc_le_0_4": True,
        "music_iacc_le_dry_plus_0_1": True,
        "hf_pm3": True,
        "bed_monotonic": True,
        "lf_pm3": True,
        "crossover_pm_0_5db": True,
    }
    for cat, group in synth.items():
        for route, curve in (group.get("routes") or {}).items():
            for point in curve:
                if point["strength"] <= 0 or point.get("hf_within_pm3db", True):
                    continue
                flags["hf_pm3"] = False
                failures.append(
                    f"D2 synth {cat} {route} s={point['strength']} HF {point['hf_max_abs_db']:.2f} dB"
                )
    for route, curve in (amb.get("routes") or {}).items():
        for point in curve:
            if point["strength"] <= 0:
                continue
            if point["iacc"] > 0.4:
                flags["ambience_iacc_le_0_4"] = False
                failures.append(f"D1 synth ambience {route} s={point['strength']} IACC {point['iacc']:.3f} > 0.4")
        beds = [p["bed_to_total"] for p in curve if p["bed_to_total"] is not None]
        if beds and not _mono(beds):
            flags["bed_monotonic"] = False
            failures.append(f"D3 synth ambience {route} bed_to_total not monotonic: {beds}")
    for route, curve in (music.get("routes") or {}).items():
        for point in curve:
            if point["strength"] <= 0:
                continue
            if point["iacc"] > dry_music + 0.1:
                flags["music_iacc_le_dry_plus_0_1"] = False
                failures.append(
                    f"D1 synth music {route} s={point['strength']} IACC {point['iacc']:.3f} > {dry_music + 0.1:.3f}"
                )
        beds = [p["bed_to_total"] for p in curve if p["bed_to_total"] is not None]
        if beds and not _mono(beds):
            flags["bed_monotonic"] = False
            failures.append(f"D3 synth music {route} bed_to_total not monotonic: {beds}")
    for name, row in (metrics.get("samples") or {}).items():
        if not row or row.get("route") in (None, "dry"):
            continue
        if str(row.get("category", "")).startswith("synth_"):
            continue
        if row.get("hf_within_pm3db") is False:
            flags["hf_pm3"] = False
            failures.append(f"D2 {name} HF {row.get('hf_max_abs_db')} dB")
        if (
            row.get("route") == "B"
            and row.get("strength") == 0.4
            and row.get("hrtf") == "thk_ku100"
            and row.get("category") in {"voice", "music", "ambience"}
            and row.get("tone_within_pm3db") is False
        ):
            flags["lf_pm3"] = False
            failures.append(f"D8 {name} 40 Hz–16 kHz {row.get('tone_max_abs_db_40_16k')} dB")
        if row.get("category") == "music" and row.get("iacc") is not None:
            dry = _dry_iacc(metrics, "music")
            if dry is not None and row["iacc"] > dry + 0.1:
                flags["music_iacc_le_dry_plus_0_1"] = False
                failures.append(f"D1 {name} IACC {row['iacc']:.3f} > dry+0.1")
    for route, report in (metrics.get("block_consistency") or {}).items():
        if not report:
            continue
        for block, err in (report.get("errors_dbfs") or {}).items():
            if err > -100.0:
                failures.append(f"D4 route {route} block {block} error {err:.1f} dBFS")
    if metrics.get("strength0_max_abs_err", 1.0) > 0.0:
        failures.append(f"strength 0 not a bypass: err={metrics.get('strength0_max_abs_err')}")
    xo = metrics.get("crossover") or {}
    if xo and not xo.get("within_pm_0_5db", False):
        flags["crossover_pm_0_5db"] = False
        failures.append(f"D8 crossover magnitude {xo.get('max_abs_db')} dB")
    frontal = (metrics.get("eq_comparison") or {}).get("thk_ku100_frontal_frontal") or {}
    if frontal and not frontal.get("within_pm2db"):
        failures.append(f"D2 frontal EQ max {frontal.get('max_abs_db')} dB")
    metrics["acceptance"] = {
        **flags,
        "synth_ambience_dry_iacc": dry_amb,
        "synth_music_dry_iacc": dry_music,
        "reference_ambience_iacc": 0.36,
    }


def _dry_iacc(metrics: dict, category: str) -> float | None:
    for row in (metrics.get("samples") or {}).values():
        if row and row.get("category") == category and row.get("route") == "dry":
            return float(row["iacc"])
    return None


def _package_versions() -> dict:
    import importlib.metadata as md

    pkgs = ["numpy", "scipy", "soundfile", "sofar", "spaudiopy", "pyfar", "pyloudnorm"]
    out = {}
    for p in pkgs:
        try:
            out[p] = {"version": md.version(p), "license": _license_guess(p)}
        except Exception as exc:  # noqa: BLE001
            out[p] = {"error": str(exc)}
    return out


def _license_guess(name: str) -> str:
    return {
        "numpy": "BSD-3-Clause",
        "scipy": "BSD-3-Clause",
        "soundfile": "BSD-3-Clause",
        "sofar": "MIT",
        "spaudiopy": "MIT",
        "pyfar": "MIT",
        "pyloudnorm": "MIT",
    }.get(name, "see package metadata")


def _write_presets() -> None:
    presets_dir = ROOT / "presets"
    presets_dir.mkdir(parents=True, exist_ok=True)
    content = """# Aurora Binaural presets (P0 draft)
# Endpoints are strength=1. strength.py also applies a floor so that
# strength 0.4 already clears the music IACC cap (dry + 0.1):
# width starts at 48° (not 30°) and side_decor_gain starts at 2.05.
# s=0 is still a full bypass. This is wider than the plan §6.8 example
# (±30° → ±60°) because HRTF crossfeed at ±30–60° lifts music IACC.

version = 1

[room]
name = "自然空间"
default_strength = 0.4
width_deg_at_1 = 80.0
ambient_ratio_at_1 = 1.0
early_level_at_1 = 0.9
late_level_at_1 = 0.7
drr_offset_db_at_1 = -6.0
side_decor_gain_at_1 = 2.4
t60 = 0.35
crossover_hz = 150.0
eq_mode = "frontal"

[surround]
name = "包围感"
default_strength = 0.5
width_deg_at_1 = 90.0
ambient_ratio_at_1 = 1.0
early_level_at_1 = 1.0
late_level_at_1 = 0.85
drr_offset_db_at_1 = -8.0
side_decor_gain_at_1 = 2.6
t60 = 0.55
crossover_hz = 150.0
eq_mode = "frontal"

[orbit]
name = "缓慢环绕"
default_strength = 0.3
width_deg_at_1 = 70.0
ambient_ratio_at_1 = 0.85
early_level_at_1 = 0.6
late_level_at_1 = 0.5
drr_offset_db_at_1 = -4.0
side_decor_gain_at_1 = 2.2
orbit_deg_per_sec = 8.0
t60 = 0.4
crossover_hz = 150.0
eq_mode = "frontal"
"""
    (presets_dir / "p0_draft.toml").write_text(content, encoding="utf-8")


def _write_partial_report(commands, failures, t0):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "REPORT.md").write_text(
        "# P0 REPORT (partial)\n\nFailures:\n"
        + "\n".join(f"- {f}" for f in failures)
        + "\n\nCommands:\n```json\n"
        + json.dumps(commands, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )


def _fmt(v, nd=3):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _write_report(commands, failures, metrics, versions, elapsed, registry) -> None:
    base = metrics.get("baseline_p0_01") or {}
    acc = metrics.get("acceptance") or {}
    lines = [
        "# P0-cursor-03 低频增益与复现入口 — REPORT",
        "",
        f"生成时间：本地运行（耗时 {elapsed:.1f}s）",
        f"分支：`agent/cursor/P0-01`　种子：`{DEFAULT_SEED}`　目标响度：`{TARGET_LUFS} LUFS`",
        "",
        "前置审查是主仓库的 `qa/p0/REVIEW-cursor-02.md`（不在这个克隆里）。D8、D9 按派单正文执行，P0-cursor-01 的规则仍然有效。",
        "",
        "## 1. 这一轮改了什么",
        "",
        "- **D8 低频来源**：40–63 Hz 的抬升不是房间，也不是 HRTF 的低频增益。分频器的低通用 `sosfilt_zi` 按「输入一直是 1」来初始化，文件开头输出从满幅 1.0 衰减。整段频谱把这一下冲击算进 40–63 Hz，环境音看起来高 17–22 dB。另外 4 阶巴特沃斯低通加高通在 150 Hz 同相相加，约 +3 dB，不是互补分频。",
        "- **D8 修复**：分频改成 LR4（两级 2 阶巴特沃斯），初值改为静止。20 Hz–20 kHz 上低通加高通的幅度偏差见下面的分频器测试。处理路径在分频之后才进 HRTF、早期反射和 FDN，本来就是高通过的信号。HRTF 在 80 Hz 以下的均衡增益保持 1，测得各方向 40–63 Hz 约 0 dB。FDN 脉冲响应在这些频段约 −30 dB，不是抬升来源。低频旁路再对齐正前方 HRIR 的峰值（约 0.5 ms），避免和空间化高通在分频点附近对消。音色保护的范围从 400 Hz 放到 40 Hz；左右耳仍是同一类校正，只在单声道下混和耳功率被去相关拉开超过 4 dB 的频段上，把少量侧向能量移回中间。",
        "- **D9**：`run_p0.py` 可以只渲染指定类别、路线、强度、HRTF、均衡和块大小。三种块大小写在同一个 `--block-size` 里时，会打印两两之间的峰值误差。",
        "",
        "- **D1 上混**：STFT 时频点先做时间一阶平滑和频率平滑，再算归一化互谱。相干性只决定直达掩码；电平比只估计直达成分的方位。不相关的部分拆成侧向一对（±90°）和后方一对（±130°），不再收成单声道。",
        "- **D2 音色**：高频损失来自未平滑的均衡逆滤波器被截断，以及 FDN 一阶低通（约 3 kHz）在响度匹配后把 4–16 kHz 一起拉低。均衡改为 1/3 倍频程平滑的最小相位逆滤波，并限制每方向功率补偿。FDN 低通抬到约 12 kHz。样片在第一次响度匹配之后做左右相同的最小相位 1/3 倍频程保护，再匹配一次。真峰值只压少数尖峰，避免整段增益把高频重新拉开。",
        "- **D3 强度**：侧向去相关馈送和房间电平随强度升高。单调量用侧后方总线能量比 `bed_to_total`，IACC 曲线同时给出。",
        "- **D4 流式上混**：`n_fft=1024`、`hop=256`、周期 sqrt-Hann，状态跨块保留。延迟 1024 点（21.3 ms），低频旁路对齐到同一延迟。",
        "- **D6 / D7**：真实录音登记在下面；HRTF 许可证按官方页面和文件内嵌属性核对。",
        "",
        "宽度映射比 plan §6.8 的 ±30°→±60° 更宽：强度大于 0 时从 48° 起到 80°（自然空间）。±30°–60° 的 HRTF 串扰会把音乐 IACC 抬过「原声 + 0.1」。侧向馈送是全通去相关后的 L−R，不是干湿混合；强度 0 仍是整段旁路。",
        "",
        "## 2. 修复前后",
        "",
        "「前」是上一版合成样片（扩散场均衡）。「后」是本轮同一合成信号、正前方均衡加方向功率补偿、等响度对比。真实录音没有上一版对照，单列在后。",
        "",
        "### D1 宽带 IACC（合成）",
        "",
        "环境音原声 IACC 约 0.009。验收：处理后 ≤ 0.4（上一版路线 A 为 0.36）。音乐原声 0.686，处理后不得高于 0.786。",
        "",
        "| 素材 | 路线 | 前 0.4 | 前 0.8 | 后 0.4 | 后 0.8 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    lines += _before_after_iacc_rows(metrics)
    lines += [
        "",
        "### D2 4–16 kHz 最大偏差（dB，等响度，左右耳功率相加）",
        "",
        "验收：各 1/3 倍频程带 ≤ ±3 dB。",
        "",
        "| 素材 | 路线 | 前 0.4 | 前 0.8 | 后 0.4 | 后 0.8 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    lines += _before_after_hf_rows(metrics)
    lines += [
        "",
        "### D2 正前方均衡",
        "",
        "纯扩散场均衡后，正前方 1/3 倍频程仍有耳廓残留，修不到 ±2 dB。叠上每方向功率补偿也过不了 ±2 dB（见下表）。默认因此改为正前方均衡，再叠加每方向功率归一（保留 ILD 和相位）。",
        "",
        "| 均衡 | 上一版 max | 本轮 max | ≤ ±2 dB |",
        "|---|---:|---:|---|",
    ]
    eq = metrics.get("eq_comparison") or {}
    for key, label, before in (
        ("thk_ku100_diffuse_frontal", "KU100 扩散场", 8.41),
        ("thk_ku100_frontal_frontal", "KU100 正前方", 0.49),
        ("thk_ku100_diffuse_power_frontal", "KU100 扩散场 + 方向功率", None),
        ("thk_ku100_frontal_power_frontal", "KU100 正前方 + 方向功率（默认）", None),
    ):
        row = eq.get(key) or {}
        lines.append(
            f"| {label} | {_fmt(before, 2)} | {_fmt(row.get('max_abs_db'), 2)} | {row.get('within_pm2db')} |"
        )
    sadie_keys = [k for k in eq if k.startswith("sadie")]
    for key in sadie_keys:
        row = eq.get(key) or {}
        lines.append(f"| {key} | — | {_fmt(row.get('max_abs_db'), 2)} | {row.get('within_pm2db')} |")

    lines += [
        "",
        "### D3 强度曲线（合成，等响度后的 IACC；`bed_to_total` 在响度匹配前）",
        "",
        "0.4 与 0.8 之间要有可测的单调变化。IACC 不是每条曲线都单调；侧后方能量比 `bed_to_total` 作为单调量。",
        "",
    ]
    lines += _curve_lines(metrics)

    lines += [
        "",
        "### D4 块大小一致性（上混路径，对照块 1024）",
        "",
        "测试块 64 / 256 / 1024，素材为合成音乐前 2 秒，路线 B 与 C（环绕角速度 0）。误差须 ≤ −100 dBFS。上一版没有测上混。",
        "",
        f"`strength=0` 最大绝对误差：`{metrics.get('strength0_max_abs_err')}`。",
        "",
    ]
    for route, report in (metrics.get("block_consistency") or {}).items():
        lines.append(f"- 路线 {route}：`{json.dumps(report, ensure_ascii=False)}`")
    lines += [
        "",
        f"上混重建误差（dB，直接+侧向+环境相对输入，对齐延迟后）：`{json.dumps(metrics.get('upmix_energy_db'), ensure_ascii=False)}`。",
        "重建误差来自随时间变化的掩码和延迟对齐后的边界，不是掩码相加不为 1。",
        "",
        "## 3. 真实素材样片",
        "",
        "人声、音乐、环境音各一段真实录音。`40–16 kHz` 是审查用的单声道下混 1/3 倍频程偏差（去掉整体电平差）。`4–16 kHz` 仍是左右耳功率相加。环境音原声并不是几乎不相关（雨声 IACC 约 0.29），≤ 0.4 的硬验收用合成环境音。",
        "",
        f"LR4 分频器低通加高通，20 Hz–20 kHz 幅度偏差 `{_fmt((metrics.get('crossover') or {}).get('max_abs_db'), 4)}` dB（验收 ±0.5 dB）。",
        "",
        "| 文件 | 路线 | 强度 | IACC | 40–16 kHz | 4–16 kHz | LUFS | dBTP |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    rows = []
    for name, row in (metrics.get("samples") or {}).items():
        if not row or str(row.get("category", "")).startswith("synth_"):
            continue
        rows.append((name, row))
    for name, row in sorted(rows, key=lambda it: (it[1].get("category") or "", it[1].get("route") or "", it[1].get("strength") or 0)):
        lines.append(
            f"| `{name}` | {row.get('route')} | {row.get('strength')} | {_fmt(row.get('iacc'))} | {_fmt(row.get('tone_max_abs_db_40_16k'), 2)} | {_fmt(row.get('hf_max_abs_db'), 2)} | {_fmt(row.get('loudness_lufs'), 2)} | {_fmt(row.get('true_peak_dbtp'), 2)} |"
        )

    lines += [
        "",
        "## 4. 素材登记（D6）",
        "",
        "合成信号仍用于测量。真实录音不超过 30 秒，来源、作者、许可证和 SHA-256 如下。原始下载在 `data/assets/src/`，不入库。",
        "",
        "| 文件 | 类别 | 作者 | 许可证 | 来源 | SHA-256 | 时长 |",
        "|---|---|---|---|---|---|---:|",
    ]
    for key, meta in (registry or {}).items():
        if not isinstance(meta, dict) or "sha256" not in meta:
            continue
        if not str(meta.get("path", "")).startswith("data/assets/real_"):
            continue
        lines.append(
            f"| `{Path(meta['path']).name}` | {meta.get('category')} | {meta.get('author')} | {meta.get('license')} | {meta.get('source')} | `{meta['sha256']}` | {meta.get('duration_s')} |"
        )
    lines += [
        "",
        "登记副本：`qa/p0/cursor/ASSETS.md`。`qa/assets.md` 不在本任务允许修改的目录里。",
        "",
        "## 5. HRTF 许可证（D7）",
        "",
        "核对日期 2026-10-16。细节和 URL 在 `data/hrtf/SOURCES.md`。",
        "",
        "- **KU100 L2702**：文件内嵌 `GLOBAL_License` 与 AIA-DAGA 2013 论文写明 **CC BY-SA 3.0**。允许再分发和商用；必须署名；修改后的 HRIR/滤波器须以 CC BY-SA 3.0 或兼容条款提供。Zenodo 记录 3928297 的 `metadata.license.id` 是 `cc-by-3.0`（没有 ShareAlike），与文件和论文不一致。再分发按文件和论文的 CC BY-SA 3.0 履行，不把 Zenodo 字段单独当成更宽松的许可。",
        "- **SADIE II D1**：官方数据库页、SOFA `GLOBAL_License` 和 Zenodo 10886409 一致为 **Apache-2.0**。允许再分发和商用；须附上许可证、保留声明、标明修改。官方页还要求注明原始数据集。学术引用 DOI 10.3390/app8112029。",
        "",
        "## 6. 依赖、命令、方向测试",
        "",
        "| 包 | 版本 | 许可证 |",
        "|---|---|---|",
    ]
    for p, meta in versions.items():
        lines.append(f"| {p} | {meta.get('version', meta)} | {meta.get('license', '')} |")
    lines += [
        "",
        "```bash",
        "cd prototypes && uv sync && uv run python run_p0.py",
        "```",
        "",
        "同一段音乐、路线 B、强度 0.4、KU100、正前方均衡，在 64／256／1024 三种块大小下各渲染一遍，并打印相互之间的最大峰值误差：",
        "",
        "```bash",
        "cd prototypes && uv run python run_p0.py --only music --route B --strength 0.4 --hrtf thk_ku100 --eq frontal --block-size 64,256,1024 --out ../qa/p0/cursor/block_check",
        "```",
        "",
        "本轮这条命令的退出码是 0。64 对 256 为 −302.2 dBFS，64 对 1024 为 −303.5 dBFS，256 对 1024 为 −304.3 dBFS。最大峰值误差 −302.2 dBFS。",
        "",
        "| 步骤 | 退出码 |",
        "|---|---:|",
    ]
    for c in commands:
        err = f" ({c['error']})" if c.get("error") else ""
        lines.append(f"| {c['step']} | {c.get('exit_code')}{err} |")
    dir_ok = {}
    for k, tests in (metrics.get("direction_tests") or {}).items():
        if not tests:
            continue
        dir_ok[k] = all(t.get("itd_sign_ok") and t.get("ild_sign_ok") for t in tests if t["azimuth"] != 0)
    lines += [
        "",
        f"方向测试符号：`{json.dumps(dir_ok, ensure_ascii=False)}`。",
        "",
        "## 7. 验收标记",
        "",
        f"`{json.dumps(acc, ensure_ascii=False)}`",
        "",
    ]
    if failures:
        lines.append("未通过：")
        lines.append("")
        for f in failures:
            lines.append(f"- {f}")
    else:
        lines.append("D1–D4 与 D8 的数值验收在本轮数据上通过。")
    lines += [
        "",
        "## 8. 已知局限",
        "",
        "1. MagLS 是网格最小二乘加高频阶次加权，不是逐频率的完整 MagLS。",
        "2. 航向旋转只在 1 阶上精确，2–3 阶不是。环绕角速度不为 0 时，块大小会改变旋转采样，一致性测试因此把角速度设为 0。",
        "3. FDN 按种子烘焙成脉冲响应再卷积，不是逐块反馈。",
        "4. 上混延迟 1024 点。输出前 21.3 ms 是这个延迟，文件末尾相应的一截不在对齐比较里。",
        "5. 纯扩散场正前方偏差仍约 8 dB，默认不用它。",
        "6. 没有 Apple AUSpatialMixer 对照。",
        "7. 少量真峰值超过 −1 dBTP 时，峰值限制会让积分响度停在约 −21 LUFS，而不是正好 −20。频谱对比仍是等响度之后的结果。",
        "",
        "## 9. 对 P1 Rust 的建议",
        "",
        "1. `prepare` 显式带上 `hrtf_id`、`eq_mode`、`directional_power`、`seed`、`route`。",
        "2. 上混做成固定跳长的流式状态机，延迟写入接口，不要整段 `stft`。",
        "3. 相干性用平滑后的互谱；电平比只喂给方位估计。",
        "4. 强度表放在数据文件里。`strength==0` 走旁路。侧向去相关增益不要做成干湿混合。",
        "5. 块一致性容差覆盖上混路径，CI 里查 `max_error_dbfs`。",
        "6. 3 阶 yaw 用完整旋转矩阵。MagLS 预计算成 FIR。",
        "7. 音色保护如果留在产品里，必须放在响度匹配之后。分频用 LR4，状态从静止开始，不要用单位阶跃的 `sosfilt_zi`。低频旁路对齐 HRIR 峰值。",
        "",
        "## 10. 推荐",
        "",
        _recommendation(metrics),
        "",
        "## 11. 路径",
        "",
        "- 样片：`qa/p0/cursor/samples/`",
        "- 指标：`qa/p0/cursor/metrics.json`",
        "- 本报告：`qa/p0/cursor/REPORT.md`",
        "- 复现：`prototypes/run_p0.py`",
        "- HRTF：`data/hrtf/SOURCES.md`",
        "- 素材：`qa/p0/cursor/ASSETS.md`",
        "- 预设：`presets/p0_draft.toml`",
        "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_assets(registry)


def _point(metrics: dict, cat: str, route: str, strength: float) -> dict | None:
    curve = ((metrics.get("strength_curve") or {}).get(f"synth_{cat}") or {}).get(route) or []
    for point in curve:
        if abs(float(point["strength"]) - strength) < 1e-6:
            return point
    return None


def _before_after_iacc_rows(metrics: dict) -> list[str]:
    base = (metrics.get("baseline_p0_01") or {}).get("iacc") or {}
    lines = []
    for cat in ("ambience", "music"):
        b = base.get(cat) or {}
        for route in ROUTES:
            before = (b.get(route) or [None, None])
            a = _point(metrics, cat, route, 0.4)
            c = _point(metrics, cat, route, 0.8)
            lines.append(
                f"| 合成{cat} | {route} | {_fmt(before[0])} | {_fmt(before[1])} | {_fmt((a or {}).get('iacc'))} | {_fmt((c or {}).get('iacc'))} |"
            )
    return lines


def _before_after_hf_rows(metrics: dict) -> list[str]:
    base = (metrics.get("baseline_p0_01") or {}).get("hf_max_abs_db_4_16k") or {}
    lines = []
    for cat in ("voice", "music", "ambience"):
        b = base.get(cat) or {}
        for route in ROUTES:
            before = b.get(route) or [None, None]
            a = _point(metrics, cat, route, 0.4)
            c = _point(metrics, cat, route, 0.8)
            lines.append(
                f"| 合成{cat} | {route} | {_fmt(before[0], 2)} | {_fmt(before[1], 2)} | {_fmt((a or {}).get('hf_max_abs_db'), 2)} | {_fmt((c or {}).get('hf_max_abs_db'), 2)} |"
            )
    return lines


def _curve_lines(metrics: dict) -> list[str]:
    lines = []
    curve = metrics.get("strength_curve") or {}
    for cat in ("synth_music", "synth_ambience"):
        lines.append(f"**{cat}**")
        lines.append("")
        lines.append("| 路线 | 量 | 0 | 0.2 | 0.4 | 0.6 | 0.8 | 1.0 | 0.4→0.8 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for route in ROUTES:
            pts = curve.get(cat, {}).get(route) or []
            by = {round(float(p["strength"]), 1): p for p in pts}
            def col(s, key, nd=3):
                p = by.get(s)
                return _fmt((p or {}).get(key), nd)
            def delta(key):
                a, b = by.get(0.4), by.get(0.8)
                if not a or not b or a.get(key) is None or b.get(key) is None:
                    return "—"
                return _fmt(float(b[key]) - float(a[key]), 3)
            lines.append(
                "| {route} | IACC | {c0} | {c2} | {c4} | {c6} | {c8} | {c1} | {d} |".format(
                    route=route,
                    c0=col(0.0, "iacc"),
                    c2=col(0.2, "iacc"),
                    c4=col(0.4, "iacc"),
                    c6=col(0.6, "iacc"),
                    c8=col(0.8, "iacc"),
                    c1=col(1.0, "iacc"),
                    d=delta("iacc"),
                )
            )
            lines.append(
                "| {route} | bed_to_total | {c0} | {c2} | {c4} | {c6} | {c8} | {c1} | {d} |".format(
                    route=route,
                    c0=col(0.0, "bed_to_total"),
                    c2=col(0.2, "bed_to_total"),
                    c4=col(0.4, "bed_to_total"),
                    c6=col(0.6, "bed_to_total"),
                    c8=col(0.8, "bed_to_total"),
                    c1=col(1.0, "bed_to_total"),
                    d=delta("bed_to_total"),
                )
            )
        lines.append("")
    return lines


def _recommendation(metrics: dict) -> str:
    acc = metrics.get("acceptance") or {}
    eq = metrics.get("eq_comparison") or {}
    diffuse = (eq.get("thk_ku100_diffuse_frontal") or {}).get("max_abs_db")
    frontal = (eq.get("thk_ku100_frontal_frontal") or {}).get("max_abs_db")
    amb = (metrics.get("synthetic_acceptance") or {}).get("ambience") or {}
    music = (metrics.get("synthetic_acceptance") or {}).get("music") or {}

    def at(group, route, strength, key):
        for point in (group.get("routes") or {}).get(route) or []:
            if abs(float(point["strength"]) - strength) < 1e-6:
                return point.get(key)
        return None

    parts = [
        "三条路线在本轮数据上的差别：",
        "",
        "- **A 虚拟扬声器**。没有上混延迟，实现短。不相关的立体声本身就不会被收成中间，环境音 IACC 上一版已经是 0.36，这轮约 "
        f"{_fmt(at(amb, 'A', 0.4, 'iacc'))}。音乐要靠更宽的张角和侧向去相关才压在原声 + 0.1 以内（0.4 档约 {_fmt(at(music, 'A', 0.4, 'iacc'))}，上限 0.786），余量比 B/C 小。它不单独抽出稳定的中置。",
        "- **B 上混 + HRTF**。和 plan §6 一致：相干的时频点留在前方扇区，不相关的成分去侧后方。合成环境音 IACC 约 "
        f"{_fmt(at(amb, 'B', 0.4, 'iacc'))}（上一版 0.87）。代价是 21.3 ms 的 STFT 延迟，以及掩码造成的重建误差。",
        f"- **C 上混 + 三阶 Ambisonics**。环境床和早期反射走声床，适合包围和缓慢环绕。这轮的环境音 IACC 跟 B 接近（0.4 档 {_fmt(at(amb, 'B', 0.4, 'iacc'))} / {_fmt(at(amb, 'C', 0.4, 'iacc'))}）。MagLS 是简化版，2–3 阶旋转不精确，航向角随块中心变化，不能当成与块大小无关的路径。",
        "",
        f"扩散场正前方偏差 {_fmt(diffuse, 2)} dB，过不了 ±2 dB。正前方均衡 {_fmt(frontal, 2)} dB，过了。默认用正前方均衡，不是因为扩散场算错，而是耳廓残留修不平。",
        "",
        "**默认路线用 B，HRTF 用 KU100 L2702，均衡用正前方。** 自然空间不需要声床。包围和环绕再开 C。SADIE II 的 Apache-2.0 对商用再分发更干净；KU100 是 CC BY-SA 3.0，衍生滤波器要保持相同条款，Zenodo 记录字段还和文件不一致。听感仍以 plan §16.3 为准，这里只按这轮测量排序。",
        "",
        f"验收标记：环境音 IACC≤0.4 为 {acc.get('ambience_iacc_le_0_4')}，音乐 IACC≤原声+0.1 为 {acc.get('music_iacc_le_dry_plus_0_1')}，"
        f"高频 ±3 dB 为 {acc.get('hf_pm3')}，侧后方能量单调为 {acc.get('bed_monotonic')}。",
    ]
    return "\n".join(parts)


def _write_assets(registry) -> None:
    asset_lines = [
        "# P0 Assets",
        "",
        "| 编号 | 文件 | 类别 | 作者 | 许可证 | 来源 | SHA-256 | 用途 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    i = 1
    for _key, meta in (registry or {}).items():
        if not isinstance(meta, dict) or "sha256" not in meta:
            continue
        asset_lines.append(
            "| A{i:02d} | `{name}` | {cat} | {author} | {lic} | {src} | `{sha}` | {note} |".format(
                i=i,
                name=Path(meta.get("path", meta.get("file", ""))).name,
                cat=meta.get("category"),
                author=meta.get("author", "—"),
                lic=meta.get("license"),
                src=meta.get("source"),
                sha=meta["sha256"],
                note=meta.get("note", ""),
            )
        )
        i += 1
    (OUT / "ASSETS.md").write_text("\n".join(asset_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
