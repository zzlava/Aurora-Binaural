# CLI 参考

源码环境使用 `uv run aurora`，安装环境直接使用 `aurora`。`aurora --help` 与每个子命令的 `--help` 显示完整参数。有限任务默认向 stdout 输出 JSON，日志和进度写 stderr；`--help` 输出帮助文本。`api`、`worker`、`gui` 是持续运行的服务，不输出单次转换 JSON。

## 子命令

| 命令 | 用途与参数 |
|---|---|
| `convert INPUT --output FILE` | 本地转换；指定 `--server URL` 则上传、创建任务、轮询、下载 |
| `batch INPUT_DIR --output-dir DIR` | 扫描本层 WAV/M4A/MP3/AAC 文件，逐个本地转换，独立记录失败；不递归、不支持 `--server` |
| `compare INPUT --output-dir DIR` | 生成等响度 dry/effect 文件与比较报告；本地处理 |
| `inspect INPUT [--json] [--strict-ffmpeg]` | 探测编码与精确解码样本数，测量 BS.1770 响度和真峰值 |
| `presets [--json]` | 从 TOML 列出公开预设、版本、默认强度和参数范围 |
| `gui [--port N] [--no-browser]` | 启动本地浏览器 GUI、API、Worker；端口默认 0（自动选择） |
| `job status JOB_ID --server URL [--json]` | 查询远程任务 |
| `job cancel JOB_ID --server URL [--json]` | 请求取消；运行中的任务在块边界停止 |
| `api [--host IP] [--port N]` | 启动 API；默认环境变量或 127.0.0.1:8000，需另启 Worker |
| `worker` | 处理共享 SQLite 队列；环境变量必须与 API 一致 |
| `schema [--output FILE]` | 输出 AuroraParams JSON Schema，可同时写文件 |
| `openapi [--output FILE]` | 输出 OpenAPI 3.1，可同时写文件 |
| `timeline-verify [--output FILE]` | 执行 WAV/M4A/MP3 的 44.1/48 kHz 时间线诊断，输出验证矩阵 |

`--json` 为兼容标志，省略仍输出 JSON。当前公开目录含 V1/V2/V3 和 `room`，默认 V2；已交付的 Rust B 核心请指定 `--preset room`。旧 surround/orbit 仅保留显式兼容入口。

### convert 参数

| 参数 | 默认／范围 |
|---|---|
| `--output`, `-o` | 必填，扩展名 `.wav` / `.m4a` / `.mp3` |
| `--preset`, `--preset-version` | V2、`1`；当前 Rust 用 `room` |
| `--strength` | 预设默认值；0–1，0 旁路空间 DSP |
| `--seed` | 20261016；0–4294967295 |
| `--format` | 从输出扩展名推断；显式值须匹配扩展名；不支持裸 `.aac` 输出 |
| `--sample-rate` | 48000；44100/48000/96000，MP3 不支持 96000 |
| `--keep-sample-rate` | 关闭；本地用输入采样率覆盖输出采样率，输入也须在支持范围 |
| `--bit-depth` | 24；WAV 16/24/32（32 为 float PCM） |
| `--bitrate` | 有损默认 256 kbps，64–512；WAV 不接受此值；实际编码器仍可能拒绝部分组合 |
| `--timing-mode` | 仅 `preserve` |
| `--tail` | `trim`；也可 `preserve` 追加混响尾巴 |
| `--fade-out-ms` | 10；0–500，trim 末尾短淡出 |
| `--block-size` | 1024 帧，本地引擎块大小 |
| `--simulated-latency`, `--simulated-tail` | 0，仅桩诊断用 |
| `--overwrite` | 关闭；允许覆盖输出及报告 |
| `--report FILE` | 可选，原子写报告 |
| `--server URL` | 不设置则本地；HTTP(S) 服务地址 |
| `--strict-ffmpeg` | 关闭；本地 FFmpeg <7 时拒绝 M4A/AAC |

远程模式传送预设、强度、seed、output、timing；`--block-size`、模拟参数、`--keep-sample-rate` 和 `--strict-ffmpeg` 是本地选项，不传给服务器。服务端控制其引擎、FFmpeg 策略和资源限制。

`compare` 接受 `--preset`、`--preset-version`、`--strength`、`--seed`、`--format`（默认 wav）、`--sample-rate`、`--bit-depth`、`--bitrate`、`--block-size`、`--overwrite`、`--strict-ffmpeg`。`batch` 接受 `--preset`、`--strength`、`--seed`、`--format`（默认 wav）、`--sample-rate`、`--block-size`、`--overwrite`、`--strict-ffmpeg`；没有 bit-depth、bitrate、tail 或 report 参数。同名 stem 会映射到同一目标名，应避免此类输入冲突。

远程 Key 从 `AURORA_API_KEY` 读取；未设置时读取 `AURORA_CONFIG_PATH` 指定 JSON 的 `api_key`。命令行没有 Key 参数。

```bash
uv run aurora batch ./inputs --preset room --format m4a --output-dir ./outputs/batch
uv run aurora job status job_example --server http://127.0.0.1:8000 --json
uv run aurora schema --output outputs/params.schema.json
uv run aurora timeline-verify --output outputs/timeline.json
```

## JSON 字段

`convert` 输出 ConversionReport（远程 `/report` 同一结构）：

| 字段 | 内容 |
|---|---|
| `ok`, `status`, `job_id`, `asset_id`, `preview` | 成功标志、succeeded；本地 ID 为 null；普通转换 preview 为 null |
| `input`, `output` | `path`, `sha256`, `format`, `container`, `codec`, `sample_rate`, `channels`, `channel_layout`, `samples`, `duration_seconds`, `bit_depth`, `size_bytes`, `initial_padding`, `trailing_padding`；samples 是每声道帧数 |
| `engine` | `name`, `version`, `git_commit`, `is_stub`, `block_size_frames`, `internal_sample_rate`, `hrtf`（name/version/sha256/license）；无 Git 元数据的安装可记录 unknown |
| `params` | 实际归一化的 api_version/mode/input/preset/strength/seed/output/timing/preview |
| `timeline` | mode/tail_policy、engine_latency_samples/engine_tail_samples、extra_tail_samples_kept、input_samples/expected_output_samples/decoded_readback_samples/raw_decoded_samples、sample_diff/raw_sample_diff、codec_priming_samples/codec_trailing_padding_samples、residual_priming_shift_samples、compensation_strategy/tolerance_samples/sync_within_tolerance/sync_warning |
| `loudness_before`, `loudness_after` | integrated_lufs/loudness_range_lu/true_peak_dbtp/is_silent/method；静音测量可能为 null |
| `elapsed_ms`, `attempts`, `created_at`, `expires_at` | 耗时、尝试次数、UTC ISO 时间；本地 expires_at 为 null |

`inspect` 返回 `ok/audio/loudness` 和可选 `sync_warning`；audio/loudness 同上。`presets` 返回 `ok/api_version/default_preset/presets/parameter_ranges`；每个 preset 为 id/name/version/default_strength/description/algorithm_summary/route/is_default。HTTP 的预设响应另有 engine。

`compare` 返回 ok/input/output_dir/dry_output/effect_output/preset/strength/target_lufs/dry_gain_db/effect_gain_db/loudness_diff_lu/dry_loudness_before/dry_loudness_after/effect_loudness_before_match/effect_loudness_after/report（完整 ConversionReport）。`batch` 返回 ok/total/succeeded/failed/results；每项为 input/output/ok，成功附 samples/sha256/sync_within_tolerance/sync_warning，失败附 error。`job` 返回的状态字段见 [API](api.md)。`schema`、`openapi` 直接输出 schema 文档；`timeline-verify` 直接输出诊断汇总与 verification_matrix，需检查内部的验证结果。

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
