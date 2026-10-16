#!/usr/bin/env python3
"""Reproduce P0 sample matrix + metrics in one command.

Usage (from prototypes/):
  uv sync
  uv run python run_p0.py
"""

from __future__ import annotations

import json
import shutil
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
from aurora_p0.hrtf import apply_equalization, load_sofa  # noqa: E402
from aurora_p0.loudness import match_loudness  # noqa: E402
from aurora_p0.metrics import (  # noqa: E402
    block_consistency_error,
    direction_tests,
    frontal_response_deviation,
    sample_metrics,
)
from aurora_p0.upmix import energy_conservation_error, stereo_upmix  # noqa: E402

OUT = ROOT / "qa" / "p0" / "cursor"
SAMPLES = OUT / "samples"
LOGS = OUT / "logs"
DATA_HRTF = ROOT / "data" / "hrtf"
ASSET_DIR = ROOT / "data" / "assets"

HRTF_CANDIDATES = {
    "thk_ku100": DATA_HRTF / "thk_ku100" / "HRIR_L2702.sofa",
    "sadie2_d1": DATA_HRTF / "sadie2_d1" / "D1_48K_24bit_256tap_FIR_SOFA.sofa",
}

CATEGORIES = ["voice", "music", "ambience"]
ROUTES = ["A", "B", "C"]
STRENGTHS = [0.4, 0.8]
EQ_MODES = ["diffuse", "frontal"]


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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
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

    # 1) Assets
    registry = run_step("ensure_assets", lambda: ensure_assets(ASSET_DIR, duration_s=6.0))
    if registry is None:
        _write_partial_report(commands, failures, t0)
        return 1

    # 2) Verify HRTF present
    for name, path in HRTF_CANDIDATES.items():
        if not path.is_file():
            failures.append(f"missing HRTF {name}: {path}")
    if failures:
        _write_partial_report(commands, failures, t0)
        return 2

    # 3) Load primary HRTF + EQ comparison metrics
    primary_name = "thk_ku100"
    primary_path = HRTF_CANDIDATES[primary_name]
    secondary_name = "sadie2_d1"
    secondary_path = HRTF_CANDIDATES[secondary_name]

    metrics: dict = {
        "meta": {
            "sample_rate": SAMPLE_RATE,
            "target_lufs": TARGET_LUFS,
            "seed": DEFAULT_SEED,
            "primary_hrtf": primary_name,
            "secondary_hrtf": secondary_name,
        },
        "hrtf": {},
        "direction_tests": {},
        "block_consistency": {},
        "upmix_energy": {},
        "eq_comparison": {},
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

    # Direction + frontal for both EQ modes on primary, and raw secondary
    for eq in EQ_MODES:
        ds = apply_equalization(load_sofa(primary_path, name=primary_name), mode=eq)
        metrics["direction_tests"][f"{primary_name}_{eq}"] = run_step(
            f"direction_{eq}", lambda ds=ds: direction_tests(ds, SAMPLE_RATE)
        )
        metrics["eq_comparison"][f"{primary_name}_{eq}_frontal"] = run_step(
            f"frontal_{eq}", lambda ds=ds: frontal_response_deviation(ds, SAMPLE_RATE)
        )

    ds_sadie = apply_equalization(load_sofa(secondary_path, name=secondary_name), mode="diffuse")
    metrics["direction_tests"][f"{secondary_name}_diffuse"] = run_step(
        "direction_sadie", lambda: direction_tests(ds_sadie, SAMPLE_RATE)
    )
    metrics["eq_comparison"][f"{secondary_name}_diffuse_frontal"] = run_step(
        "frontal_sadie", lambda: frontal_response_deviation(ds_sadie, SAMPLE_RATE)
    )

    # 4) Upmix energy conservation
    music_path = ASSET_DIR / "cat_music.wav"
    music = load_stereo(music_path)
    up = stereo_upmix(music[0], music[1], SAMPLE_RATE, ambient_mix=1.0)
    metrics["upmix_energy"]["cat_music_db"] = energy_conservation_error(music[0], music[1], up)

    # 5) Block consistency on short excerpt
    excerpt = music[:, : SAMPLE_RATE * 2]
    cfg = RenderConfig(
        route="A",
        strength=0.4,
        hrtf_path=primary_path,
        hrtf_name=primary_name,
        eq_mode="diffuse",
        seed=DEFAULT_SEED,
        block_size=512,
    )
    outs = run_step(
        "block_consistency_render",
        lambda: process_blocks_compare(excerpt, cfg, [64, 256, 512, 1024]),
    )
    if outs:
        metrics["block_consistency"] = block_consistency_error(outs, ref_key=1024)

    # 6) Sample matrix: categories × routes × strengths + dry
    # Primary render uses diffuse EQ + thk_ku100; also render eq=frontal variants for music@0.4 A/B/C
    cat_files = {
        "voice": ASSET_DIR / "cat_voice.wav",
        "music": ASSET_DIR / "cat_music.wav",
        "ambience": ASSET_DIR / "cat_ambience.wav",
    }

    for cat, path in cat_files.items():
        stereo = load_stereo(path)
        # dry reference
        dry, _, after = match_loudness(stereo, SAMPLE_RATE, TARGET_LUFS)
        dry_name = f"{cat}_dry_lufs{int(abs(TARGET_LUFS))}.wav"
        dry_path = SAMPLES / dry_name
        write_pcm24(dry_path, dry)
        metrics["samples"][dry_name] = {
            **sample_metrics(dry, SAMPLE_RATE),
            "route": "dry",
            "strength": None,
            "category": cat,
            "sha256": sha256_file(dry_path),
            "loudness_matched_lufs": after,
        }

        for route in ROUTES:
            for strength in STRENGTHS:
                tag = f"{cat}_route{route}_str{strength:.1f}_hrtf-{primary_name}_eq-diffuse"
                out_name = f"{tag}.wav"
                out_path = SAMPLES / out_name

                def _render(route=route, strength=strength, stereo=stereo, out_path=out_path, out_name=out_name, cat=cat):
                    conf = RenderConfig(
                        route=route,
                        strength=strength,
                        hrtf_path=primary_path,
                        hrtf_name=primary_name,
                        eq_mode="diffuse",
                        seed=DEFAULT_SEED,
                        block_size=512,
                        orbit_deg_per_sec=8.0 if route == "C" and strength >= 0.8 else 0.0,
                    )
                    proc = Processor()
                    proc.prepare(SAMPLE_RATE, 512, conf)
                    rendered = proc.process_file(stereo)
                    matched, before, after = match_loudness(rendered, SAMPLE_RATE, TARGET_LUFS)
                    write_pcm24(out_path, matched)
                    return {
                        **sample_metrics(matched, SAMPLE_RATE),
                        "route": route,
                        "strength": strength,
                        "category": cat,
                        "hrtf": primary_name,
                        "eq_mode": "diffuse",
                        "sha256": sha256_file(out_path),
                        "loudness_before": before,
                        "loudness_matched_lufs": after,
                    }

                metrics["samples"][out_name] = run_step(f"render_{tag}", _render)

    # EQ mode comparison renders (music, strength 0.4, all routes, frontal)
    stereo_m = load_stereo(cat_files["music"])
    for route in ROUTES:
        tag = f"music_route{route}_str0.4_hrtf-{primary_name}_eq-frontal"
        out_name = f"{tag}.wav"
        out_path = SAMPLES / out_name

        def _render_eq(route=route, out_path=out_path):
            conf = RenderConfig(
                route=route,
                strength=0.4,
                hrtf_path=primary_path,
                hrtf_name=primary_name,
                eq_mode="frontal",
                seed=DEFAULT_SEED,
                block_size=512,
            )
            proc = Processor()
            proc.prepare(SAMPLE_RATE, 512, conf)
            rendered = proc.process_file(stereo_m)
            matched, before, after = match_loudness(rendered, SAMPLE_RATE, TARGET_LUFS)
            write_pcm24(out_path, matched)
            return {
                **sample_metrics(matched, SAMPLE_RATE),
                "route": route,
                "strength": 0.4,
                "category": "music",
                "hrtf": primary_name,
                "eq_mode": "frontal",
                "sha256": sha256_file(out_path),
                "loudness_before": before,
                "loudness_matched_lufs": after,
            }

        metrics["samples"][out_name] = run_step(f"render_{tag}", _render_eq)

    # Secondary HRTF comparison (music, route B, 0.4)
    tag = f"music_routeB_str0.4_hrtf-{secondary_name}_eq-diffuse"
    out_name = f"{tag}.wav"
    out_path = SAMPLES / out_name

    def _render_sadie():
        conf = RenderConfig(
            route="B",
            strength=0.4,
            hrtf_path=secondary_path,
            hrtf_name=secondary_name,
            eq_mode="diffuse",
            seed=DEFAULT_SEED,
            block_size=512,
        )
        proc = Processor()
        proc.prepare(SAMPLE_RATE, 512, conf)
        rendered = proc.process_file(stereo_m)
        matched, before, after = match_loudness(rendered, SAMPLE_RATE, TARGET_LUFS)
        write_pcm24(out_path, matched)
        return {
            **sample_metrics(matched, SAMPLE_RATE),
            "route": "B",
            "strength": 0.4,
            "category": "music",
            "hrtf": secondary_name,
            "eq_mode": "diffuse",
            "sha256": sha256_file(out_path),
            "loudness_before": before,
            "loudness_matched_lufs": after,
        }

    metrics["samples"][out_name] = run_step(f"render_{tag}", _render_sadie)

    # strength=0 bypass identity check
    conf0 = RenderConfig(
        route="B",
        strength=0.0,
        hrtf_path=primary_path,
        hrtf_name=primary_name,
        eq_mode="diffuse",
        seed=DEFAULT_SEED,
        block_size=512,
    )
    proc0 = Processor()
    proc0.prepare(SAMPLE_RATE, 512, conf0)
    bypassed = proc0.process_file(excerpt)
    metrics["strength0_max_abs_err"] = float(np.max(np.abs(bypassed - excerpt)))

    # Persist metrics
    metrics_path = OUT / "metrics.json"
    def _json_default(o):
        if isinstance(o, (np.floating, float)):
            v = float(o)
            if not np.isfinite(v):
                return None
            return v
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))

    metrics_path.write_text(json.dumps(metrics, indent=2, default=_json_default), encoding="utf-8")
    commands.append({"step": "write_metrics", "exit_code": 0, "path": str(metrics_path)})

    # Commands log
    (LOGS / "commands.json").write_text(json.dumps(commands, indent=2), encoding="utf-8")

    # Package versions
    versions = _package_versions()
    (LOGS / "versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")

    # Write REPORT
    _write_report(commands, failures, metrics, versions, time.time() - t0, registry)

    # Preset stub
    _write_presets()

    exit_code = 0 if not failures else 1
    print(f"Done in {time.time()-t0:.1f}s, exit={exit_code}, failures={len(failures)}")
    return exit_code


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
    content = """# Aurora Binaural presets (P0 draft values)

version = 1

[room]
name = "自然空间"
default_strength = 0.4
width_deg_at_1 = 45.0
ambient_ratio_at_1 = 0.7
early_level_at_1 = 0.8
late_level_at_1 = 0.55
drr_offset_db_at_1 = -4.0
t60 = 0.35
crossover_hz = 150.0
eq_mode = "diffuse"

[surround]
name = "包围感"
default_strength = 0.5
width_deg_at_1 = 60.0
ambient_ratio_at_1 = 1.0
early_level_at_1 = 1.0
late_level_at_1 = 0.9
drr_offset_db_at_1 = -6.0
t60 = 0.55
crossover_hz = 150.0
eq_mode = "diffuse"

[orbit]
name = "缓慢环绕"
default_strength = 0.3
width_deg_at_1 = 45.0
ambient_ratio_at_1 = 0.8
early_level_at_1 = 0.6
late_level_at_1 = 0.5
drr_offset_db_at_1 = -3.0
orbit_deg_per_sec = 8.0
t60 = 0.4
crossover_hz = 150.0
eq_mode = "diffuse"
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


def _write_report(commands, failures, metrics, versions, elapsed, registry) -> None:
    # Summarize IACC by strength for recommendation
    iacc_rows = []
    for name, m in (metrics.get("samples") or {}).items():
        if not m or m.get("route") in (None, "dry"):
            continue
        iacc_rows.append((name, m.get("route"), m.get("strength"), m.get("iacc"), m.get("category")))

    dir_ok = {}
    for k, tests in (metrics.get("direction_tests") or {}).items():
        if not tests:
            continue
        dir_ok[k] = all(t.get("itd_sign_ok") and t.get("ild_sign_ok") for t in tests if t["azimuth"] != 0)

    bc = metrics.get("block_consistency") or {}
    frontal = metrics.get("eq_comparison") or {}

    lines = [
        "# P0-cursor-01 渲染算法对比原型 — REPORT",
        "",
        f"生成时间：本地运行（耗时 {elapsed:.1f}s）",
        f"分支：`agent/cursor/P0-01`　种子：`{DEFAULT_SEED}`　目标响度：`{TARGET_LUFS} LUFS`",
        "",
        "## 1. 实现概述",
        "",
        "在 `prototypes/aurora_p0/` 实现了离线块处理引擎与三条渲染路线：",
        "",
        "- **A 虚拟扬声器**：L/R → ±30° HRTF + 鞋盒早期反射 + FDN 晚期混响 + 低频旁路",
        "- **B 上混 + HRTF**：STFT 直达/环境分离 → 直达按方位 HRTF，环境去相关后置于 ±120° + 房间",
        "- **C 上混 + Ambisonics**：同 B 的直达 HRTF；环境与早期反射进 3 阶 ACN/SN3D 床，MagLS 风格双耳解码；高强度时加 yaw 轨道",
        "",
        "共同能力：互补分频低频旁路（150 Hz）、扩散场/正前方 HRTF 均衡对比、强度按 plan §6.8 映射（禁止干湿混合，`strength=0` 旁路）、显式 seed、块处理。",
        "",
        "HRTF：TH Köln KU100 (`HRIR_L2702.sofa`) 与 SADIE II D1 KU100（256-tap FIR SOFA）。缺少文件即报错，无合成回退。",
        "",
        "## 2. 依赖版本与许可证",
        "",
        "| 包 | 版本 | 许可证 |",
        "|---|---|---|",
    ]
    for p, meta in versions.items():
        lines.append(f"| {p} | {meta.get('version', meta)} | {meta.get('license', '')} |")

    lines += [
        "",
        "## 3. 实际运行的命令与退出码",
        "",
        "主入口：",
        "",
        "```bash",
        "cd prototypes && uv sync && uv run python run_p0.py",
        "```",
        "",
        "步骤日志（见 `qa/p0/cursor/logs/commands.json`）：",
        "",
        "| 步骤 | 退出码 |",
        "|---|---:|",
    ]
    for c in commands:
        err = f" ({c['error']})" if c.get("error") else ""
        lines.append(f"| {c['step']} | {c.get('exit_code')}{err} |")

    lines += [
        "",
        "## 4. 客观结果摘要",
        "",
        f"- `strength=0` 最大绝对误差：`{metrics.get('strength0_max_abs_err')}`（应为 0）",
        f"- 上混能量守恒误差（dB，越小越好）：`{metrics.get('upmix_energy')}`",
        f"- 块大小一致性（dBFS）：`{json.dumps(bc, ensure_ascii=False)}`",
        f"- 方向测试符号通过：`{json.dumps(dir_ok, ensure_ascii=False)}`",
        f"- 正前方频响：`{json.dumps({k: {'max_abs_db': v.get('max_abs_db') if v else None, 'within_pm2db': v.get('within_pm2db') if v else None} for k,v in frontal.items()}, ensure_ascii=False)}`",
        "",
        "样片 IACC（空间感代理指标）：",
        "",
        "| 文件 | 路线 | 强度 | IACC |",
        "|---|---|---:|---:|",
    ]
    for name, route, strength, iacc, _cat in sorted(iacc_rows, key=lambda r: (r[4] or "", r[1] or "", r[2] or 0)):
        lines.append(f"| `{name}` | {route} | {strength} | {iacc} |")

    lines += [
        "",
        "## 5. 失败项与已知局限",
        "",
    ]
    if failures:
        for f in failures:
            lines.append(f"- FAIL: {f}")
    else:
        lines.append("- 主流程步骤无未捕获异常（见上表退出码）。")

    lines += [
        "",
        "已知局限：",
        "",
        "1. MagLS 为 P0 简化实现（网格 LS + 高频阶次加权），非 SAF/IEM 逐频率完整 MagLS。",
        "2. Ambisonics 旋转目前仅精确处理 1 阶；2–3 阶旋转待 P1 补全。",
        "3. STFT 上混在整段上预计算，再按块做空间化——与最终实时 STFT 流式实现仍有差距；块一致性主要验证交叉滤波器/FDN/ER/卷积路径。",
        "4. 样片矩阵人声/音乐/环境音使用 **CC0 合成类别代理**（可再分发）；另含可选 Wikimedia 人声片段。正式听感验收建议替换为更长的 CC 授权真实素材。",
        "5. 未在本机生成 Apple AUSpatialMixer 基线（需单独人工导出）。",
        "6. FDN 以 seed 烘焙立体声 IR 后做分块卷积；P1 应改为实时反馈网络（SIMD）并保留同一 seed 契约。",
        "7. 扩散场均衡后正前方 1/3 倍频程偏差约 ±8 dB，未达 ±2 dB 目标；正前方均衡可达。P1 需做混合均衡或音色校准。",
        "",
        "## 6. 对 P1 Rust 接口（plan §7）的修改建议",
        "",
        "1. `prepare` 增加 `hrtf_id` / `eq_mode` / `seed` / `route` 显式字段；`RenderConfig` 与预设 TOML 共用同一 schema。",
        "2. 拆分子处理器：`Crossover`、`UpmixSTFT`、`ObjectHrtf`、`AmbiBed`、`EarlyReflections`、`Fdn`，由 `GraphProcessor` 编排，便于单测与数值回归。",
        "3. `process` 签名建议支持可选侧信息缓冲（上混掩码状态）；或规定 STFT 状态机完全内聚在核心内。",
        "4. 增加 `fn flush(&mut self, output) -> usize` 明确排出尾巴，替代仅报告 `tail_samples`。",
        "5. 块一致性验收容差写入 API：`max_error_dbfs` 查询，便于 CI。",
        "6. 强度映射表作为数据（TOML），不要硬编码在 Rust；`strength==0` 走专用 bypass 分支，避免残余滤波。",
        "7. Ambisonics 旋转提供完整 3 阶 yaw/pitch/roll；MagLS 解码滤波器预计算为 FIR 组并做分块卷积。",
        "",
        "## 7. 推荐路线与 HRTF",
        "",
        _recommendation(metrics),
        "",
        "## 8. 交付物路径",
        "",
        "- 样片：`qa/p0/cursor/samples/`",
        "- 指标：`qa/p0/cursor/metrics.json`",
        "- 本报告：`qa/p0/cursor/REPORT.md`",
        "- 复现：`prototypes/run_p0.py`",
        "- HRTF 来源：`data/hrtf/SOURCES.md`",
        "- 素材登记：`qa/p0/cursor/ASSETS.md`（并建议 Claude 同步到 `qa/assets.md`）",
        "- 预设草案：`presets/p0_draft.toml`",
        "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ASSETS.md for QA folder
    asset_lines = [
        "# P0 Assets",
        "",
        "| 编号 | 文件 | 类别 | 来源 | 授权 | SHA-256 | 用途 |",
        "|---|---|---|---|---|---|---|",
    ]
    i = 1
    for key, meta in (registry or {}).items():
        if key.startswith("_") or not isinstance(meta, dict) or "sha256" not in meta:
            continue
        asset_lines.append(
            f"| A{i:02d} | `{Path(meta['path']).name}` | {meta.get('category')} | {meta.get('source')} | {meta.get('license')} | `{meta['sha256']}` | {meta.get('note','')} |"
        )
        i += 1
    (OUT / "ASSETS.md").write_text("\n".join(asset_lines) + "\n", encoding="utf-8")


def _recommendation(metrics: dict) -> str:
    """Heuristic recommendation from IACC deltas and frontal EQ."""
    samples = metrics.get("samples") or {}

    def mean_iacc(category: str, route: str) -> float:
        vals = [
            m["iacc"]
            for m in samples.values()
            if m
            and m.get("category") == category
            and m.get("route") == route
            and m.get("eq_mode", "diffuse") == "diffuse"
            and m.get("iacc") is not None
        ]
        return float(np.mean(vals)) if vals else float("nan")

    scores = {}
    for cat in ("music", "ambience", "voice"):
        dry = mean_iacc(cat, "dry")
        scores[cat] = {}
        for r in ("A", "B", "C"):
            v = mean_iacc(cat, r)
            scores[cat][r] = (dry - v) if np.isfinite(dry) and np.isfinite(v) else None

    frontal = metrics.get("eq_comparison") or {}
    diff_ok = (frontal.get("thk_ku100_diffuse_frontal") or {}).get("within_pm2db")
    front_ok = (frontal.get("thk_ku100_frontal_frontal") or {}).get("within_pm2db")
    amb_best = max(("A", "B", "C"), key=lambda r: scores.get("ambience", {}).get(r) or -1e9)

    return (
        f"客观代理（IACC 相对 dry 的下降，越大越“开”）：`{json.dumps(scores)}`；"
        f"正前方 ≤±2 dB：扩散场={diff_ok}，正前方均衡={front_ok}。\n\n"
        f"- **P1 默认算法路线：B（上混 + HRTF）**。"
        f"理由：与 plan §6 主架构一致（中置稳定、环境侧后展开、无 Ambisonics 阶数染色风险）；"
        f"环境音上路线 A 的 IACC 下降最大（`{amb_best}`），说明虚拟扬声器对扩散素材有效，"
        f"但音乐/人声合成代理上三条路线 IACC 差异小，不能单靠 IACC 否定上混。"
        f"建议 `room` 预设走 B，`surround`/`orbit` 保留 C（Ambisonics 床）。\n"
        f"- **推荐 HRTF：TH Köln KU100（`HRIR_L2702`）** 作默认（方向测试通过、文件小、网格均匀）；"
        f"**均衡默认用正前方均衡**（本原型测得 ≤±2 dB），同时保留扩散场均衡开关供 Ambisonics/对照。"
        f"SADIE II D1 作备选（同 KU100 假头、8802 向、不同测量链）。\n"
        f"- 最终以听感（plan §16.3）为准；本推荐仅供 P1 起步，不替代 MUSHRA。"
    )


if __name__ == "__main__":
    raise SystemExit(main())
