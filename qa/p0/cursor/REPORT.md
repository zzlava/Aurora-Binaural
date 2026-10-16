# P0-cursor-03 低频增益与复现入口 — REPORT

生成时间：本地运行（耗时 208.0s）
分支：`agent/cursor/P0-01`　种子：`20261016`　目标响度：`-20.0 LUFS`

前置审查是主仓库的 `qa/p0/REVIEW-cursor-02.md`（不在这个克隆里）。D8、D9 按派单正文执行，P0-cursor-01 的规则仍然有效。

## 1. 这一轮改了什么

- **D8 低频来源**：40–63 Hz 的抬升不是房间，也不是 HRTF 的低频增益。分频器的低通用 `sosfilt_zi` 按「输入一直是 1」来初始化，文件开头输出从满幅 1.0 衰减。整段频谱把这一下冲击算进 40–63 Hz，环境音看起来高 17–22 dB。另外 4 阶巴特沃斯低通加高通在 150 Hz 同相相加，约 +3 dB，不是互补分频。
- **D8 修复**：分频改成 LR4（两级 2 阶巴特沃斯），初值改为静止。20 Hz–20 kHz 上低通加高通的幅度偏差见下面的分频器测试。处理路径在分频之后才进 HRTF、早期反射和 FDN，本来就是高通过的信号。HRTF 在 80 Hz 以下的均衡增益保持 1，测得各方向 40–63 Hz 约 0 dB。FDN 脉冲响应在这些频段约 −30 dB，不是抬升来源。低频旁路再对齐正前方 HRIR 的峰值（约 0.5 ms），避免和空间化高通在分频点附近对消。音色保护的范围从 400 Hz 放到 40 Hz；左右耳仍是同一类校正，只在单声道下混和耳功率被去相关拉开超过 4 dB 的频段上，把少量侧向能量移回中间。
- **D9**：`run_p0.py` 可以只渲染指定类别、路线、强度、HRTF、均衡和块大小。三种块大小写在同一个 `--block-size` 里时，会打印两两之间的峰值误差。

- **D1 上混**：STFT 时频点先做时间一阶平滑和频率平滑，再算归一化互谱。相干性只决定直达掩码；电平比只估计直达成分的方位。不相关的部分拆成侧向一对（±90°）和后方一对（±130°），不再收成单声道。
- **D2 音色**：高频损失来自未平滑的均衡逆滤波器被截断，以及 FDN 一阶低通（约 3 kHz）在响度匹配后把 4–16 kHz 一起拉低。均衡改为 1/3 倍频程平滑的最小相位逆滤波，并限制每方向功率补偿。FDN 低通抬到约 12 kHz。样片在第一次响度匹配之后做左右相同的最小相位 1/3 倍频程保护，再匹配一次。真峰值只压少数尖峰，避免整段增益把高频重新拉开。
- **D3 强度**：侧向去相关馈送和房间电平随强度升高。单调量用侧后方总线能量比 `bed_to_total`，IACC 曲线同时给出。
- **D4 流式上混**：`n_fft=1024`、`hop=256`、周期 sqrt-Hann，状态跨块保留。延迟 1024 点（21.3 ms），低频旁路对齐到同一延迟。
- **D6 / D7**：真实录音登记在下面；HRTF 许可证按官方页面和文件内嵌属性核对。

宽度映射比 plan §6.8 的 ±30°→±60° 更宽：强度大于 0 时从 48° 起到 80°（自然空间）。±30°–60° 的 HRTF 串扰会把音乐 IACC 抬过「原声 + 0.1」。侧向馈送是全通去相关后的 L−R，不是干湿混合；强度 0 仍是整段旁路。

## 2. 修复前后

「前」是上一版合成样片（扩散场均衡）。「后」是本轮同一合成信号、正前方均衡加方向功率补偿、等响度对比。真实录音没有上一版对照，单列在后。

### D1 宽带 IACC（合成）

环境音原声 IACC 约 0.009。验收：处理后 ≤ 0.4（上一版路线 A 为 0.36）。音乐原声 0.686，处理后不得高于 0.786。

| 素材 | 路线 | 前 0.4 | 前 0.8 | 后 0.4 | 后 0.8 |
|---|---|---:|---:|---:|---:|
| 合成ambience | A | 0.361 | 0.368 | 0.118 | 0.129 |
| 合成ambience | B | 0.873 | 0.870 | 0.155 | 0.160 |
| 合成ambience | C | 0.995 | 0.984 | 0.162 | 0.153 |
| 合成music | A | 0.918 | 0.920 | 0.711 | 0.687 |
| 合成music | B | 0.987 | 0.985 | 0.686 | 0.658 |
| 合成music | C | 0.990 | 0.988 | 0.685 | 0.658 |

### D2 4–16 kHz 最大偏差（dB，等响度，左右耳功率相加）

验收：各 1/3 倍频程带 ≤ ±3 dB。

| 素材 | 路线 | 前 0.4 | 前 0.8 | 后 0.4 | 后 0.8 |
|---|---|---:|---:|---:|---:|
| 合成voice | A | 6.45 | 7.21 | 0.50 | 0.80 |
| 合成voice | B | 16.95 | 15.52 | 0.29 | 0.23 |
| 合成voice | C | 12.16 | 12.84 | 0.29 | 0.22 |
| 合成music | A | 7.48 | 7.58 | 0.12 | 0.17 |
| 合成music | B | 16.75 | 13.96 | 0.22 | 0.27 |
| 合成music | C | 12.19 | 11.57 | 0.25 | 0.31 |
| 合成ambience | A | 7.49 | 7.50 | 0.19 | 0.18 |
| 合成ambience | B | 14.24 | 13.55 | 0.46 | 0.51 |
| 合成ambience | C | 10.16 | 10.97 | 0.51 | 0.49 |

### D2 正前方均衡

纯扩散场均衡后，正前方 1/3 倍频程仍有耳廓残留，修不到 ±2 dB。叠上每方向功率补偿也过不了 ±2 dB（见下表）。默认因此改为正前方均衡，再叠加每方向功率归一（保留 ILD 和相位）。

| 均衡 | 上一版 max | 本轮 max | ≤ ±2 dB |
|---|---:|---:|---|
| KU100 扩散场 | 8.41 | 8.82 | False |
| KU100 正前方 | 0.49 | 1.08 | True |
| KU100 扩散场 + 方向功率 | — | 3.98 | False |
| KU100 正前方 + 方向功率（默认） | — | 0.68 | True |
| sadie2_d1_frontal+power_frontal | — | 0.49 | True |

### D3 强度曲线（合成，等响度后的 IACC；`bed_to_total` 在响度匹配前）

0.4 与 0.8 之间要有可测的单调变化。IACC 不是每条曲线都单调；侧后方能量比 `bed_to_total` 作为单调量。

**synth_music**

| 路线 | 量 | 0 | 0.2 | 0.4 | 0.6 | 0.8 | 1.0 | 0.4→0.8 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | IACC | 0.686 | 0.723 | 0.711 | 0.698 | 0.687 | 0.681 | -0.024 |
| A | bed_to_total | 0.000 | 0.006 | 0.014 | 0.024 | 0.036 | 0.051 | 0.022 |
| B | IACC | 0.686 | 0.701 | 0.686 | 0.672 | 0.658 | 0.648 | -0.028 |
| B | bed_to_total | 0.000 | 0.048 | 0.109 | 0.184 | 0.276 | 0.384 | 0.168 |
| C | IACC | 0.686 | 0.698 | 0.685 | 0.672 | 0.658 | 0.647 | -0.027 |
| C | bed_to_total | 0.000 | 0.039 | 0.089 | 0.151 | 0.227 | 0.317 | 0.138 |

**synth_ambience**

| 路线 | 量 | 0 | 0.2 | 0.4 | 0.6 | 0.8 | 1.0 | 0.4→0.8 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | IACC | 0.009 | 0.111 | 0.118 | 0.121 | 0.129 | 0.141 | 0.011 |
| A | bed_to_total | 0.000 | 0.004 | 0.009 | 0.016 | 0.024 | 0.034 | 0.015 |
| B | IACC | 0.009 | 0.161 | 0.155 | 0.157 | 0.160 | 0.160 | 0.005 |
| B | bed_to_total | 0.000 | 0.126 | 0.297 | 0.509 | 0.754 | 1.025 | 0.458 |
| C | IACC | 0.009 | 0.177 | 0.162 | 0.154 | 0.153 | 0.151 | -0.009 |
| C | bed_to_total | 0.000 | 0.112 | 0.271 | 0.475 | 0.715 | 0.978 | 0.444 |


### D4 块大小一致性（上混路径，对照块 1024）

测试块 64 / 256 / 1024，素材为合成音乐前 2 秒，路线 B 与 C（环绕角速度 0）。误差须 ≤ −100 dBFS。上一版没有测上混。

`strength=0` 最大绝对误差：`0.0`。

- 路线 B：`{"reference_block": 1024, "errors_dbfs": {"64": -319.09101308366536, "256": -319.09101308366536}}`
- 路线 C：`{"reference_block": 1024, "errors_dbfs": {"64": -319.09101308366536, "256": -319.09101308366536}}`

上混重建误差（dB，直接+侧向+环境相对输入，对齐延迟后）：`{"cat_music": -62.60737179800895, "cat_music_coherence_mean": 0.17463248919951402, "cat_ambience": -76.03455266971277, "cat_ambience_coherence_mean": 0.16174513790286946}`。
重建误差来自随时间变化的掩码和延迟对齐后的边界，不是掩码相加不为 1。

## 3. 真实素材样片

人声、音乐、环境音各一段真实录音。`40–16 kHz` 是审查用的单声道下混 1/3 倍频程偏差（去掉整体电平差）。`4–16 kHz` 仍是左右耳功率相加。环境音原声并不是几乎不相关（雨声 IACC 约 0.29），≤ 0.4 的硬验收用合成环境音。

LR4 分频器低通加高通，20 Hz–20 kHz 幅度偏差 `0.0000` dB（验收 ±0.5 dB）。

| 文件 | 路线 | 强度 | IACC | 40–16 kHz | 4–16 kHz | LUFS | dBTP |
|---|---|---:|---:|---:|---:|---:|---:|
| `ambience_routeA_str0.4_hrtf-thk_ku100_eq-frontal.wav` | A | 0.4 | 0.107 | 0.96 | 0.67 | -20.00 | -6.76 |
| `ambience_routeA_str0.8_hrtf-thk_ku100_eq-frontal.wav` | A | 0.8 | 0.096 | 0.78 | 0.82 | -20.00 | -6.83 |
| `ambience_routeB_str0.4_hrtf-thk_ku100_eq-frontal.wav` | B | 0.4 | 0.168 | 0.87 | 1.21 | -20.00 | -8.75 |
| `ambience_routeB_str0.4_hrtf-sadie2_d1_eq-frontal.wav` | B | 0.4 | 0.148 | 0.90 | 1.33 | -20.00 | -9.14 |
| `ambience_routeB_str0.8_hrtf-thk_ku100_eq-frontal.wav` | B | 0.8 | 0.143 | 0.84 | 1.09 | -20.00 | -8.82 |
| `ambience_routeB_str0.8_hrtf-sadie2_d1_eq-frontal.wav` | B | 0.8 | 0.131 | 0.81 | 1.18 | -20.00 | -8.78 |
| `ambience_routeC_str0.4_hrtf-thk_ku100_eq-frontal.wav` | C | 0.4 | 0.155 | 0.87 | 1.30 | -20.00 | -8.67 |
| `ambience_routeC_str0.8_hrtf-thk_ku100_eq-frontal.wav` | C | 0.8 | 0.125 | 0.87 | 1.33 | -20.00 | -7.82 |
| `ambience_dry_lufs20.wav` | dry | None | 0.288 | — | — | -20.00 | -3.62 |
| `music_routeA_str0.4_hrtf-thk_ku100_eq-frontal.wav` | A | 0.4 | 0.346 | 1.84 | 1.65 | -20.29 | -1.00 |
| `music_routeA_str0.8_hrtf-thk_ku100_eq-frontal.wav` | A | 0.8 | 0.283 | 2.04 | 1.85 | -20.47 | -1.00 |
| `music_routeB_str0.2_hrtf-thk_ku100_eq-frontal.wav` | B | 0.2 | 0.567 | 1.67 | 2.03 | -20.00 | -1.20 |
| `music_routeB_str0.4_hrtf-thk_ku100_eq-frontal.wav` | B | 0.4 | 0.548 | 1.73 | 2.13 | -20.00 | -1.40 |
| `music_routeB_str0.4_hrtf-sadie2_d1_eq-frontal.wav` | B | 0.4 | 0.483 | 1.69 | 1.88 | -20.00 | -1.17 |
| `music_routeB_str0.8_hrtf-thk_ku100_eq-frontal.wav` | B | 0.8 | 0.495 | 2.08 | 2.19 | -20.00 | -1.04 |
| `music_routeB_str0.8_hrtf-sadie2_d1_eq-frontal.wav` | B | 0.8 | 0.439 | 1.87 | 2.03 | -20.00 | -1.05 |
| `music_routeB_str1.0_hrtf-thk_ku100_eq-frontal.wav` | B | 1.0 | 0.473 | 2.41 | 2.30 | -20.06 | -1.00 |
| `music_routeC_str0.4_hrtf-thk_ku100_eq-frontal.wav` | C | 0.4 | 0.538 | 1.76 | 2.04 | -20.00 | -1.41 |
| `music_routeC_str0.8_hrtf-thk_ku100_eq-frontal.wav` | C | 0.8 | 0.493 | 2.40 | 1.97 | -20.33 | -1.00 |
| `music_dry_lufs20.wav` | dry | None | 0.594 | — | — | -20.00 | -2.12 |
| `voice_routeA_str0.4_hrtf-thk_ku100_eq-frontal.wav` | A | 0.4 | 0.932 | 0.37 | 0.92 | -20.00 | -1.42 |
| `voice_routeA_str0.8_hrtf-thk_ku100_eq-frontal.wav` | A | 0.8 | 0.913 | 1.09 | 0.85 | -20.00 | -1.71 |
| `voice_routeB_str0.4_hrtf-thk_ku100_eq-frontal.wav` | B | 0.4 | 0.922 | 0.78 | 0.97 | -20.00 | -1.35 |
| `voice_routeB_str0.4_hrtf-sadie2_d1_eq-frontal.wav` | B | 0.4 | 0.903 | 0.87 | 1.02 | -20.05 | -1.00 |
| `voice_routeB_str0.8_hrtf-thk_ku100_eq-frontal.wav` | B | 0.8 | 0.921 | 1.38 | 0.90 | -20.00 | -1.40 |
| `voice_routeB_str0.8_hrtf-sadie2_d1_eq-frontal.wav` | B | 0.8 | 0.904 | 1.58 | 0.91 | -20.11 | -1.00 |
| `voice_routeC_str0.4_hrtf-thk_ku100_eq-frontal.wav` | C | 0.4 | 0.923 | 0.76 | 0.96 | -20.00 | -1.36 |
| `voice_routeC_str0.8_hrtf-thk_ku100_eq-frontal.wav` | C | 0.8 | 0.923 | 1.33 | 0.82 | -20.00 | -1.33 |
| `voice_dry_lufs20.wav` | dry | None | 0.970 | — | — | -20.00 | -1.92 |

## 4. 素材登记（D6）

合成信号仍用于测量。真实录音不超过 30 秒，来源、作者、许可证和 SHA-256 如下。原始下载在 `data/assets/src/`，不入库。

| 文件 | 类别 | 作者 | 许可证 | 来源 | SHA-256 | 时长 |
|---|---|---|---|---|---|---:|
| `real_voice.wav` | voice | Animyth Productions, Brittany Lauda | CC BY 3.0 | https://upload.wikimedia.org/wikipedia/commons/2/27/Brittany_Lauda_Sample.ogg | `41d733d17b632f943cfdc883ed6c150ceb33de667b7946a94c022e2b4f69a3a1` | 9.079 |
| `real_music.wav` | music | Kevin MacLeod (incompetech.com) | CC BY 3.0 | https://upload.wikimedia.org/wikipedia/commons/2/2e/Luminous_Rain_%28ISRC_USUAN1100169%29.mp3 | `daa390ce27ef10fc5d828f0a1573336bc55900ba1c25516b56c14922d545af41` | 20.0 |
| `real_ambience.wav` | ambience | PB (Wikimedia Commons user Buddpaul) | CC0-1.0 | https://upload.wikimedia.org/wikipedia/commons/8/81/Rain_clip.theora.ogv | `5651e59f0ad0fd0f0f4288feb13cc60dc5832e459cbbb67eccf69f99d5afa94d` | 8.684 |

登记副本：`qa/p0/cursor/ASSETS.md`。`qa/assets.md` 不在本任务允许修改的目录里。

## 5. HRTF 许可证（D7）

核对日期 2026-10-16。细节和 URL 在 `data/hrtf/SOURCES.md`。

- **KU100 L2702**：文件内嵌 `GLOBAL_License` 与 AIA-DAGA 2013 论文写明 **CC BY-SA 3.0**。允许再分发和商用；必须署名；修改后的 HRIR/滤波器须以 CC BY-SA 3.0 或兼容条款提供。Zenodo 记录 3928297 的 `metadata.license.id` 是 `cc-by-3.0`（没有 ShareAlike），与文件和论文不一致。再分发按文件和论文的 CC BY-SA 3.0 履行，不把 Zenodo 字段单独当成更宽松的许可。
- **SADIE II D1**：官方数据库页、SOFA `GLOBAL_License` 和 Zenodo 10886409 一致为 **Apache-2.0**。允许再分发和商用；须附上许可证、保留声明、标明修改。官方页还要求注明原始数据集。学术引用 DOI 10.3390/app8112029。

## 6. 依赖、命令、方向测试

| 包 | 版本 | 许可证 |
|---|---|---|
| numpy | 2.5.4 | BSD-3-Clause |
| scipy | 1.18.1 | BSD-3-Clause |
| soundfile | 0.14.0 | BSD-3-Clause |
| sofar | 1.2.3 | MIT |
| spaudiopy | 0.2.0 | MIT |
| pyfar | 0.8.1 | MIT |
| pyloudnorm | 0.2.0 | MIT |

```bash
cd prototypes && uv sync && uv run python run_p0.py
```

同一段音乐、路线 B、强度 0.4、KU100、正前方均衡，在 64／256／1024 三种块大小下各渲染一遍，并打印相互之间的最大峰值误差：

```bash
cd prototypes && uv run python run_p0.py --only music --route B --strength 0.4 --hrtf thk_ku100 --eq frontal --block-size 64,256,1024 --out ../qa/p0/cursor/block_check
```

本轮这条命令的退出码是 0。64 对 256 为 −302.2 dBFS，64 对 1024 为 −303.5 dBFS，256 对 1024 为 −304.3 dBFS。最大峰值误差 −302.2 dBFS。

| 步骤 | 退出码 |
|---|---:|
| ensure_assets | 0 |
| hrtf_meta_thk | 0 |
| hrtf_meta_sadie | 0 |
| eq_thk_ku100_diffuse | 0 |
| eq_thk_ku100_diffuse_power | 0 |
| eq_thk_ku100_frontal | 0 |
| eq_thk_ku100_frontal_power | 0 |
| eq_sadie_frontal_power | 0 |
| upmix_energy | 0 |
| crossover_lr4 | 0 |
| block_consistency_route_B | 0 |
| block_consistency_route_C | 0 |
| synth_voice_A_0.4 | 0 |
| synth_voice_A_0.8 | 0 |
| synth_voice_B_0.4 | 0 |
| synth_voice_B_0.8 | 0 |
| synth_voice_C_0.4 | 0 |
| synth_voice_C_0.8 | 0 |
| synth_music_A_0.0 | 0 |
| synth_music_A_0.2 | 0 |
| synth_music_A_0.4 | 0 |
| synth_music_A_0.6 | 0 |
| synth_music_A_0.8 | 0 |
| synth_music_A_1.0 | 0 |
| synth_music_B_0.0 | 0 |
| synth_music_B_0.2 | 0 |
| synth_music_B_0.4 | 0 |
| synth_music_B_0.6 | 0 |
| synth_music_B_0.8 | 0 |
| synth_music_B_1.0 | 0 |
| synth_music_C_0.0 | 0 |
| synth_music_C_0.2 | 0 |
| synth_music_C_0.4 | 0 |
| synth_music_C_0.6 | 0 |
| synth_music_C_0.8 | 0 |
| synth_music_C_1.0 | 0 |
| synth_ambience_A_0.0 | 0 |
| synth_ambience_A_0.2 | 0 |
| synth_ambience_A_0.4 | 0 |
| synth_ambience_A_0.6 | 0 |
| synth_ambience_A_0.8 | 0 |
| synth_ambience_A_1.0 | 0 |
| synth_ambience_B_0.0 | 0 |
| synth_ambience_B_0.2 | 0 |
| synth_ambience_B_0.4 | 0 |
| synth_ambience_B_0.6 | 0 |
| synth_ambience_B_0.8 | 0 |
| synth_ambience_B_1.0 | 0 |
| synth_ambience_C_0.0 | 0 |
| synth_ambience_C_0.2 | 0 |
| synth_ambience_C_0.4 | 0 |
| synth_ambience_C_0.6 | 0 |
| synth_ambience_C_0.8 | 0 |
| synth_ambience_C_1.0 | 0 |
| render_voice_routeA_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_voice_routeA_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_voice_routeB_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_voice_routeB_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_voice_routeC_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_voice_routeC_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeA_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeA_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeB_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeB_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeC_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeC_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_ambience_routeA_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_ambience_routeA_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_ambience_routeB_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_ambience_routeB_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_ambience_routeC_str0.4_hrtf-thk_ku100_eq-frontal | 0 |
| render_ambience_routeC_str0.8_hrtf-thk_ku100_eq-frontal | 0 |
| render_voice_routeB_str0.4_hrtf-sadie2_d1_eq-frontal | 0 |
| render_voice_routeB_str0.8_hrtf-sadie2_d1_eq-frontal | 0 |
| render_music_routeB_str0.4_hrtf-sadie2_d1_eq-frontal | 0 |
| render_music_routeB_str0.8_hrtf-sadie2_d1_eq-frontal | 0 |
| render_ambience_routeB_str0.4_hrtf-sadie2_d1_eq-frontal | 0 |
| render_ambience_routeB_str0.8_hrtf-sadie2_d1_eq-frontal | 0 |
| render_music_routeB_str0.2_hrtf-thk_ku100_eq-frontal | 0 |
| render_music_routeB_str1.0_hrtf-thk_ku100_eq-frontal | 0 |
| write_metrics | 0 |

方向测试符号：`{"thk_ku100_frontal+power": true, "sadie2_d1_frontal+power": true}`。

## 7. 验收标记

`{"ambience_iacc_le_0_4": true, "music_iacc_le_dry_plus_0_1": true, "hf_pm3": true, "bed_monotonic": true, "lf_pm3": true, "crossover_pm_0_5db": true, "synth_ambience_dry_iacc": 0.009497817612559249, "synth_music_dry_iacc": 0.6859864071396236, "reference_ambience_iacc": 0.36}`

D1–D4 与 D8 的数值验收在本轮数据上通过。

## 8. 已知局限

1. MagLS 是网格最小二乘加高频阶次加权，不是逐频率的完整 MagLS。
2. 航向旋转只在 1 阶上精确，2–3 阶不是。环绕角速度不为 0 时，块大小会改变旋转采样，一致性测试因此把角速度设为 0。
3. FDN 按种子烘焙成脉冲响应再卷积，不是逐块反馈。
4. 上混延迟 1024 点。输出前 21.3 ms 是这个延迟，文件末尾相应的一截不在对齐比较里。
5. 纯扩散场正前方偏差仍约 8 dB，默认不用它。
6. 没有 Apple AUSpatialMixer 对照。
7. 少量真峰值超过 −1 dBTP 时，峰值限制会让积分响度停在约 −21 LUFS，而不是正好 −20。频谱对比仍是等响度之后的结果。

## 9. 对 P1 Rust 的建议

1. `prepare` 显式带上 `hrtf_id`、`eq_mode`、`directional_power`、`seed`、`route`。
2. 上混做成固定跳长的流式状态机，延迟写入接口，不要整段 `stft`。
3. 相干性用平滑后的互谱；电平比只喂给方位估计。
4. 强度表放在数据文件里。`strength==0` 走旁路。侧向去相关增益不要做成干湿混合。
5. 块一致性容差覆盖上混路径，CI 里查 `max_error_dbfs`。
6. 3 阶 yaw 用完整旋转矩阵。MagLS 预计算成 FIR。
7. 音色保护如果留在产品里，必须放在响度匹配之后。分频用 LR4，状态从静止开始，不要用单位阶跃的 `sosfilt_zi`。低频旁路对齐 HRIR 峰值。

## 10. 推荐

三条路线在本轮数据上的差别：

- **A 虚拟扬声器**。没有上混延迟，实现短。不相关的立体声本身就不会被收成中间，环境音 IACC 上一版已经是 0.36，这轮约 0.118。音乐要靠更宽的张角和侧向去相关才压在原声 + 0.1 以内（0.4 档约 0.711，上限 0.786），余量比 B/C 小。它不单独抽出稳定的中置。
- **B 上混 + HRTF**。和 plan §6 一致：相干的时频点留在前方扇区，不相关的成分去侧后方。合成环境音 IACC 约 0.155（上一版 0.87）。代价是 21.3 ms 的 STFT 延迟，以及掩码造成的重建误差。
- **C 上混 + 三阶 Ambisonics**。环境床和早期反射走声床，适合包围和缓慢环绕。这轮的环境音 IACC 跟 B 接近（0.4 档 0.155 / 0.162）。MagLS 是简化版，2–3 阶旋转不精确，航向角随块中心变化，不能当成与块大小无关的路径。

扩散场正前方偏差 8.82 dB，过不了 ±2 dB。正前方均衡 1.08 dB，过了。默认用正前方均衡，不是因为扩散场算错，而是耳廓残留修不平。

**默认路线用 B，HRTF 用 KU100 L2702，均衡用正前方。** 自然空间不需要声床。包围和环绕再开 C。SADIE II 的 Apache-2.0 对商用再分发更干净；KU100 是 CC BY-SA 3.0，衍生滤波器要保持相同条款，Zenodo 记录字段还和文件不一致。听感仍以 plan §16.3 为准，这里只按这轮测量排序。

验收标记：环境音 IACC≤0.4 为 True，音乐 IACC≤原声+0.1 为 True，高频 ±3 dB 为 True，侧后方能量单调为 True。

## 11. 路径

- 样片：`qa/p0/cursor/samples/`
- 指标：`qa/p0/cursor/metrics.json`
- 本报告：`qa/p0/cursor/REPORT.md`
- 复现：`prototypes/run_p0.py`
- HRTF：`data/hrtf/SOURCES.md`
- 素材：`qa/p0/cursor/ASSETS.md`
- 预设：`presets/p0_draft.toml`

