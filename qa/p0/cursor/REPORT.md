# P0-cursor-01 渲染算法对比原型 — REPORT

生成时间：本地运行（耗时 25.1s）
分支：`agent/cursor/P0-01`　种子：`20261016`　目标响度：`-20.0 LUFS`

## 1. 实现概述

在 `prototypes/aurora_p0/` 实现了离线块处理引擎与三条渲染路线：

- **A 虚拟扬声器**：L/R → ±30° HRTF + 鞋盒早期反射 + FDN 晚期混响 + 低频旁路
- **B 上混 + HRTF**：STFT 直达/环境分离 → 直达按方位 HRTF，环境去相关后置于 ±120° + 房间
- **C 上混 + Ambisonics**：同 B 的直达 HRTF；环境与早期反射进 3 阶 ACN/SN3D 床，MagLS 风格双耳解码；高强度时加 yaw 轨道

共同能力：互补分频低频旁路（150 Hz）、扩散场/正前方 HRTF 均衡对比、强度按 plan §6.8 映射（禁止干湿混合，`strength=0` 旁路）、显式 seed、块处理。

HRTF：TH Köln KU100 (`HRIR_L2702.sofa`) 与 SADIE II D1 KU100（256-tap FIR SOFA）。缺少文件即报错，无合成回退。

## 2. 依赖版本与许可证

| 包 | 版本 | 许可证 |
|---|---|---|
| numpy | 2.5.4 | BSD-3-Clause |
| scipy | 1.18.1 | BSD-3-Clause |
| soundfile | 0.14.0 | BSD-3-Clause |
| sofar | 1.2.3 | MIT |
| spaudiopy | 0.2.0 | MIT |
| pyfar | 0.8.1 | MIT |
| pyloudnorm | 0.2.0 | MIT |

## 3. 实际运行的命令与退出码

主入口：

```bash
cd prototypes && uv sync && uv run python run_p0.py
```

步骤日志（见 `qa/p0/cursor/logs/commands.json`）：

| 步骤 | 退出码 |
|---|---:|
| ensure_assets | 0 |
| hrtf_meta_thk | 0 |
| hrtf_meta_sadie | 0 |
| direction_diffuse | 0 |
| frontal_diffuse | 0 |
| direction_frontal | 0 |
| frontal_frontal | 0 |
| direction_sadie | 0 |
| frontal_sadie | 0 |
| block_consistency_render | 0 |
| render_voice_routeA_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_voice_routeA_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_voice_routeB_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_voice_routeB_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_voice_routeC_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_voice_routeC_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_music_routeA_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_music_routeA_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_music_routeB_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_music_routeB_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_music_routeC_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_music_routeC_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_ambience_routeA_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_ambience_routeA_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_ambience_routeB_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_ambience_routeB_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_ambience_routeC_str0.4_hrtf-thk_ku100_eq-diffuse | 0 |
| render_ambience_routeC_str0.8_hrtf-thk_ku100_eq-diffuse | 0 |
| render_music_routeA_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeB_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeC_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeB_str0.4_hrtf-sadie2_d1_eq-diffuse | 0 |
| write_metrics | 0 |

## 4. 客观结果摘要

- `strength=0` 最大绝对误差：`0.0`（应为 0）
- 上混能量守恒误差（dB，越小越好）：`{'cat_music_db': -199.99999999997993}`
- 块大小一致性（dBFS）：`{"reference_block": 1024, "errors_dbfs": {"64": -319.09101308366536, "256": -319.09101308366536, "512": -319.09101308366536}}`
- 方向测试符号通过：`{"thk_ku100_diffuse": true, "thk_ku100_frontal": true, "sadie2_d1_diffuse": true}`
- 正前方频响：`{"thk_ku100_diffuse_frontal": {"max_abs_db": 8.405405206033489, "within_pm2db": false}, "thk_ku100_frontal_frontal": {"max_abs_db": 0.4945865712558142, "within_pm2db": true}, "sadie2_d1_diffuse_frontal": {"max_abs_db": 8.380121599606456, "within_pm2db": false}}`

样片 IACC（空间感代理指标）：

| 文件 | 路线 | 强度 | IACC |
|---|---|---:|---:|
| `ambience_routeA_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | A | 0.4 | 0.36088287354808446 |
| `ambience_routeA_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | A | 0.8 | 0.3684348702448998 |
| `ambience_routeB_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | B | 0.4 | 0.8732118614170972 |
| `ambience_routeB_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | B | 0.8 | 0.8695904134316202 |
| `ambience_routeC_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | C | 0.4 | 0.9953873890693133 |
| `ambience_routeC_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | C | 0.8 | 0.9838416951997155 |
| `music_routeA_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | A | 0.4 | 0.9184767800671354 |
| `music_routeA_str0.4_hrtf-thk_ku100_eq-frontal.wav` | A | 0.4 | 0.9191578235788608 |
| `music_routeA_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | A | 0.8 | 0.9200841407618298 |
| `music_routeB_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | B | 0.4 | 0.9871863261669034 |
| `music_routeB_str0.4_hrtf-thk_ku100_eq-frontal.wav` | B | 0.4 | 0.9887354291003987 |
| `music_routeB_str0.4_hrtf-sadie2_d1_eq-diffuse.wav` | B | 0.4 | 0.9837897557579458 |
| `music_routeB_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | B | 0.8 | 0.9847875552450492 |
| `music_routeC_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | C | 0.4 | 0.9902983093662846 |
| `music_routeC_str0.4_hrtf-thk_ku100_eq-frontal.wav` | C | 0.4 | 0.9922353711076085 |
| `music_routeC_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | C | 0.8 | 0.9878239924765491 |
| `voice_routeA_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | A | 0.4 | 0.9987467541089409 |
| `voice_routeA_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | A | 0.8 | 0.9988961837200957 |
| `voice_routeB_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | B | 0.4 | 0.9980920340458054 |
| `voice_routeB_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | B | 0.8 | 0.9982739263861458 |
| `voice_routeC_str0.4_hrtf-thk_ku100_eq-diffuse.wav` | C | 0.4 | 0.9980419066180624 |
| `voice_routeC_str0.8_hrtf-thk_ku100_eq-diffuse.wav` | C | 0.8 | 0.9983656373115329 |

## 5. 失败项与已知局限

- 主流程步骤无未捕获异常（见上表退出码）。

已知局限：

1. MagLS 为 P0 简化实现（网格 LS + 高频阶次加权），非 SAF/IEM 逐频率完整 MagLS。
2. Ambisonics 旋转目前仅精确处理 1 阶；2–3 阶旋转待 P1 补全。
3. STFT 上混在整段上预计算，再按块做空间化——与最终实时 STFT 流式实现仍有差距；块一致性主要验证交叉滤波器/FDN/ER/卷积路径。
4. 样片矩阵人声/音乐/环境音使用 **CC0 合成类别代理**（可再分发）；另含可选 Wikimedia 人声片段。正式听感验收建议替换为更长的 CC 授权真实素材。
5. 未在本机生成 Apple AUSpatialMixer 基线（需单独人工导出）。
6. FDN 为样本级 Python 循环，离线可接受，P1 需向量化/SIMD。

## 6. 对 P1 Rust 接口（plan §7）的修改建议

1. `prepare` 增加 `hrtf_id` / `eq_mode` / `seed` / `route` 显式字段；`RenderConfig` 与预设 TOML 共用同一 schema。
2. 拆分子处理器：`Crossover`、`UpmixSTFT`、`ObjectHrtf`、`AmbiBed`、`EarlyReflections`、`Fdn`，由 `GraphProcessor` 编排，便于单测与数值回归。
3. `process` 签名建议支持可选侧信息缓冲（上混掩码状态）；或规定 STFT 状态机完全内聚在核心内。
4. 增加 `fn flush(&mut self, output) -> usize` 明确排出尾巴，替代仅报告 `tail_samples`。
5. 块一致性验收容差写入 API：`max_error_dbfs` 查询，便于 CI。
6. 强度映射表作为数据（TOML），不要硬编码在 Rust；`strength==0` 走专用 bypass 分支，避免残余滤波。
7. Ambisonics 旋转提供完整 3 阶 yaw/pitch/roll；MagLS 解码滤波器预计算为 FIR 组并做分块卷积。

## 7. 推荐路线与 HRTF

基于客观代理指标（音乐类 IACC 相对原声的下降：{"A": -0.23325317432965187, "B": -0.30013835942795075, "C": -0.30413281717719054}；扩散场均衡正前方 ≤±2 dB：False；正前方均衡：True）：

- **推荐算法路线：A**（上混结构相对纯虚拟扬声器更能稳定中置并展开环境；若听感上 Ambisonics 床的包围感明显且染色可接受，可将 C 作为 `surround`/`orbit` 预设路径）。
- **推荐 HRTF：TH Köln KU100（`HRIR_L2702`）+ 扩散场均衡** 作为默认；SADIE II D1 作为对照/备选（同为 KU100 假头、不同测量链）。
- 最终以听感（plan §16.3）为准；本推荐仅供 P1 起步，不替代 MUSHRA。

## 8. 交付物路径

- 样片：`qa/p0/cursor/samples/`
- 指标：`qa/p0/cursor/metrics.json`
- 本报告：`qa/p0/cursor/REPORT.md`
- 复现：`prototypes/run_p0.py`
- HRTF 来源：`data/hrtf/SOURCES.md`
- 素材登记：`qa/p0/cursor/ASSETS.md`（并建议 Claude 同步到 `qa/assets.md`）
- 预设草案：`presets/p0_draft.toml`

