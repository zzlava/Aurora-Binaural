# CLI 参考

源码环境使用 `uv run aurora`，安装环境直接使用 `aurora`。`aurora --help` 与每个子命令的 `--help` 显示完整参数。有限任务默认向 stdout 输出 JSON，日志和进度写 stderr；`--help` 输出帮助文本。`api`、`worker`、`gui` 是持续运行的服务，不输出单次转换 JSON。

## 子命令

| 命令 | 用途与参数 |
|---|---|
| `convert INPUT --output FILE` | 本地转换；指定 `--server URL` 则上传、创建任务、轮询、下载 |
| `preview INPUT --output-dir DIR` | 本地生成最多 20 秒的等响度原音/效果 WAV 及报告 |
| `batch INPUT_DIR --output-dir DIR` | 扫描本层 FLAC/WAV/ALAC/AIFF/M4A/MP3/AAC 文件，逐个本地转换，独立记录失败；不递归、不支持 `--server` |
| `compare INPUT --output-dir DIR` | 生成等响度 dry/effect 文件与比较报告；本地处理 |
| `inspect INPUT [--json] [--strict-ffmpeg]` | 探测编码与精确解码样本数，测量 BS.1770 响度和真峰值 |
| `presets [--json]` | 从 TOML 列出公开预设、版本、默认强度、`default_controls` 和参数范围 |
| `gui [--port N] [--no-browser]` | 启动本地浏览器 GUI、API、Worker；端口默认 0（自动选择） |
| `job status JOB_ID --server URL [--json]` | 查询远程任务 |
| `job cancel JOB_ID --server URL [--json]` | 请求取消；运行中的任务在块边界停止 |
| `api [--host IP] [--port N]` | 启动 API；默认环境变量或 127.0.0.1:8000，需另启 Worker |
| `worker` | 处理共享 SQLite 队列；环境变量必须与 API 一致 |
| `schema [--output FILE]` | 输出 AuroraParams JSON Schema，可同时写文件 |
| `openapi [--output FILE]` | 输出 OpenAPI 3.1，可同时写文件 |
| `timeline-verify [--output FILE]` | 执行 WAV/M4A/MP3 的 44.1/48 kHz 时间线诊断，输出验证矩阵 |

`--json` 为兼容标志，省略仍输出 JSON。当前公开目录含 V1/V2/V3 和 `room`，默认真实 Rust 路线 V 与 SADIE II D1，默认预设读取 route_v.toml 的 default_preset（字段缺失时警告并选择该文件第一个预设）；`--preset room` 选择经典路线 B。旧 surround/orbit 仅保留显式兼容入口。

### convert 参数

| 参数 | 默认／范围 |
|---|---|
| `--output`, `-o` | 必填，扩展名 `.wav` / `.flac` / `.m4a` / `.aiff` / `.mp3` |
| `--preset`, `--preset-version` | TOML 的 default_preset、`1`；`room` 为经典路线 B |
| `--strength` | 预设默认值；0–1，0 旁路空间 DSP |
| `--width` | 声场左右展开；0–1 或 `0%`–`100%`，默认取基础预设 TOML |
| `--height` | 上方扬声器层（头顶感）；0–1 或百分比，默认取基础预设 TOML |
| `--room` | 混响量与长度（房间感）；0–1 或百分比，默认取基础预设 TOML |
| `--voice-distance` | 人声远近，0 最近；0–1 或百分比，默认取基础预设 TOML |
| `--user-preset` | 选用本机保存的预设名称或 ID，恢复其基础预设版本及五个控件 |
| `--seed` | 20261016；0–4294967295 |
| `--format` | `wav/flac/alac/aiff/m4a/mp3`；从扩展名推断；ALAC 须显式 --format alac，扩展名 .m4a；不支持裸 .aac 输出 |
| `--sample-rate` | `source`（与源文件相同）；`source/44100/48000/96000`，MP3 最高 48 kHz，AAC 最高 96 kHz |
| `--keep-sample-rate` | `--sample-rate source` 的兼容别名（本地选项） |
| `--bit-depth` | 24；无损格式 16/24，WAV 另支持 `32f`（兼容旧 `32`）；16 bit 加 TPDF 抖动 |
| `--bitrate` | 有损默认 256 kbps，64–512；无损格式不接受此值；实际编码器仍可能拒绝部分组合 |
| `--timing-mode` | 仅 `preserve` |
| `--tail` | `trim`；也可 `preserve` 追加混响尾巴 |
| `--fade-out-ms` | 10；0–500，空间渲染 trim 末尾短淡出；桩与强度 0 旁路不淡出 |
| `--block-size` | 1024 帧，本地引擎块大小 |
| `--simulated-latency`, `--simulated-tail` | 0，仅桩诊断用 |
| `--overwrite` | 关闭；允许覆盖输出及报告 |
| `--report FILE` | 可选，原子写报告 |
| `--server URL` | 不设置则本地；HTTP(S) 服务地址 |
| `--strict-ffmpeg` | 关闭；本地 FFmpeg <7 时拒绝 M4A/AAC |

五个控件分别是空间强度（整体空间感）、宽度（声场左右展开）、头顶感（上方扬声器层）、房间感（混响量与长度）及人声距离（人声远近）。四个新参数可写 `0.8` 或 `'80%'`；`--strength` 保持原有归一化数值语法。省略时读取 `[Vx.controls]`，不是统一的 50%。有限越界值截断到 `[0,1]` 并在 stderr 和报告 `params.control_clipping` 记录；NaN/Inf 拒绝。route B 只应用 strength，其余四项忽略；桩忽略全部五项，报告会说明。

`--user-preset` 从本地 GUI 同一本机 SQLite 读取；默认 `~/Library/Caches/AuroraBinaural/aurora.db`，可用 `AURORA_DATA_DIR` / `AURORA_DB_PATH` 指定。它覆盖 `--preset` 的基础选择；显式给出的强度或其他控件再覆盖保存值。管理和 JSON 备份/导入在 GUI 的“我的预设”中完成。

远程模式传送基础预设、归一化 `controls`、seed、output、timing；选用我的预设时在本机展开后发送。`--block-size`、模拟参数、`--keep-sample-rate` 和 `--strict-ffmpeg` 是本地选项，不传给服务器。服务端控制其引擎、FFmpeg 策略和资源限制。

### preview 参数

`preview` 接受 `--preset`、`--preset-version`、`--strength`、`--width`、`--height`、`--room`、`--voice-distance`、`--user-preset`、`--seed` 和 `--overwrite`，取值规则与 convert 一致。另需 `--output-dir DIR`；`--start` 默认为 0 秒，`--duration` 默认为 10 秒、须大于 0 且不超过 20 秒。处理从原音频起点推进，保留原时间位置的渲染状态；末尾不足时截短。

固定输出 48 kHz 双声道 float32 `original.wav`（等响度原音）、`effect.wav`（效果）、`report.json`；stdout 同样输出完整报告。本地处理，不接受 `--server` 或格式选项；远程试听使用 `POST /v1/previews`。默认不覆盖已有输出。

```bash
uv run aurora convert input.wav --output outputs/wide.wav \
  --strength 0.8 --width '80%' --height 0.7 --room '30%' --voice-distance 0.1
uv run aurora preview input.wav --output-dir outputs/ab --start 5 --duration 10 \
  --user-preset '夜听空间'
uv run aurora batch ./inputs --output-dir outputs/batch --user-preset '夜听空间'
```

### compare 与 batch

`compare` 接受 `--preset`、`--preset-version`、`--strength`、`--seed`、`--format`（默认 wav）、`--sample-rate`、`--bit-depth`、`--bitrate`、`--block-size`、`--overwrite`、`--strict-ffmpeg`。`batch` 接受 `--preset`、`--user-preset`、`--strength`、`--seed`、`--format`（默认 wav）、`--sample-rate`、`--bit-depth`、`--block-size`、`--overwrite`、`--strict-ffmpeg`；没有 bitrate、tail 或 report 文件参数，每项成功结果的 `report` 包含完整报告。同名 stem 会映射到同一目标名，应避免此类输入冲突。

远程 Key 从 `AURORA_API_KEY` 读取；未设置时读取 `AURORA_CONFIG_PATH` 指定 JSON 的 `api_key`。命令行没有 Key 参数。

```bash
uv run aurora batch ./inputs --format m4a --output-dir ./outputs/batch
uv run aurora job status job_example --server http://127.0.0.1:8000 --json
uv run aurora schema --output outputs/params.schema.json
uv run aurora timeline-verify --output outputs/timeline.json
```

内部按 48 kHz 处理，超过 24 kHz 的内容不做空间处理。高采样率经过 soxr VHQ 的降采样／升采样，详见 [格式和采样率说明](quickstart.md#格式采样率与位深)。用 `AURORA_FFMPEG`／`AURORA_FFPROBE` 可选择二进制；缺少 soxr 时重采样返回 `SOXR_UNAVAILABLE`，不降级。

## JSON 字段

`convert` 输出 ConversionReport（远程 `/report` 同一结构）：

| 字段 | 内容 |
|---|---|
| `ok`, `status`, `job_id`, `asset_id`, `preview` | 成功标志、succeeded；本地 ID 为 null；普通转换 preview 为 null |
| `input`, `output` | `path`, `sha256`, `format`, `container`, `codec`, `sample_rate`, `channels`, `channel_layout`, `samples`, `duration_seconds`, `bit_depth`, `size_bytes`, `initial_padding`, `trailing_padding`；samples 是每声道帧数 |
| `engine` | `name`, `version`, `git_commit`, `is_stub`, `route`, `block_size_frames`, `internal_sample_rate`, `hrtf`（name/version/sha256/license）；原生核心版本来自已安装 aurora_core，git_commit 是运行时应用仓库提交号，无 Git 元数据的安装可记录 unknown |
| `params` | 归一化请求；params.output.sample_rate 可保留 source，实际数值读 output.sample_rate。含 api_version/mode/input/preset、五个控件、controls、control_clipping、seed/output/timing/preview |
| `controls`, `effective_params` | 实际生效的控件和核心展开参数；route V 来自原生核心读回，B 仅记录 strength，桩 controls 为空；不支持展开时 effective_params 为 null |
| `ignored_controls`, `controls_note` | 被引擎忽略的控件列表和说明，便于区分请求值与实际应用值 |
| `timeline` | mode/tail_policy、engine_latency_samples/engine_tail_samples、extra_tail_samples_kept、input_samples/processing_input_samples/processing_output_samples/resampler/duration_rounding_adjustment_samples/expected_output_samples/decoded_readback_samples/raw_decoded_samples、sample_diff/raw_sample_diff、codec_priming_samples/codec_trailing_padding_samples、residual_priming_shift_samples、compensation_strategy/tolerance_samples/sync_within_tolerance/sync_warning |
| `loudness_before`, `loudness_after` | integrated_lufs/loudness_range_lu/true_peak_dbtp/is_silent/method；静音测量可能为 null |
| `elapsed_ms`, `attempts`, `created_at`, `expires_at` | 耗时、尝试次数、UTC ISO 时间；本地 expires_at 为 null |

源时长按 round(源样本数 × 输出率 / 源率) 锚定；重采样后的取整误差通过末端补零／裁切对齐，处理帧数及 duration_rounding_adjustment_samples 写入报告。`timeline.sync_within_tolerance` 仅核对回读样本数；冲激峰值的位置需另行测量，当前路线 V 的已知偏移见 [故障排查](troubleshooting.md)。

`inspect` 返回 `ok/audio/loudness` 和可选 `sync_warning`；audio/loudness 同上。`presets` 返回 `ok/api_version/default_preset/presets/parameter_ranges`；每个 preset 为 id/name/version/default_strength/default_controls/description/algorithm_summary/route/is_default。路线 V 的 default_controls 含全部五项，经典 B 仅含 strength。HTTP 的预设响应另有 engine。

`compare` 返回 ok/input/output_dir/dry_output/effect_output/preset/strength/target_lufs/dry_gain_db/effect_gain_db/loudness_diff_lu/dry_loudness_before/dry_loudness_after/effect_loudness_before_match/effect_loudness_after/report（完整 ConversionReport）。`batch` 返回 ok/total/succeeded/failed/results；每项为 input/output/ok，成功附 samples/sha256/sync_within_tolerance/sync_warning/report，失败附 error。`job` 返回的状态字段见 [API](api.md)。`schema`、`openapi` 直接输出 schema 文档；`timeline-verify` 直接输出诊断汇总与 verification_matrix，需检查内部的验证结果。

## 退出码与错误

| 码 | 含义 |
|---|---|
| 0 | 命令完成；仍需检查报告中的同步与质量字段 |
| 2 | 参数或配置格式错误 |
| 3 | 输入不存在、不支持、输出已存在等输入问题 |
| 4 | 渲染、解码、编码或内部失败；batch 任意项目失败 |
| 5 | 远程请求／远程任务失败 |
| 130 | 取消或 Ctrl-C |

错误 stdout 为 `{"ok":false,"error":{"code":"CORE_NOT_INSTALLED","message":"…","exit_code":4,"retryable":false,"details":{}}}`。脚本应以稳定的 `error.code` 和 `retryable` 判断，不匹配 message 文本。
