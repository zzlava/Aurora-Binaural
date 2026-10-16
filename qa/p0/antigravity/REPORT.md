# P0-antigravity-01：应用层骨架与编解码时间线验证报告

- **任务编号**：`P0-antigravity-01`
- **执行代理**：Antigravity (`agy`)
- **工作分支**：`agent/antigravity/P0-01`（独立克隆 `.agents/antigravity/`）
- **执行日期**：2026-10-16
- **本机环境**：macOS ARM64 (`Darwin arm64`, macOS 26.7.1)，Python `3.12.13`（由 `uv 0.12.13` 管理），Homebrew `ffmpeg 9.0.2`，OrbStack Docker `28.5.2`

---

## 1. 交付产物与文件清单

本次 P0 严格限制在允许修改的目录范围内（未触碰 `core/`、`prototypes/` 及克隆外任何目录，未删除任何已有文件）：

| 路径 | 说明 |
|---|---|
| `pyproject.toml` / `uv.lock` | Python 包 `aurora_binaural` 配置（Python ≥ 3.12，CLI 入口 `aurora`，集成 Ruff、mypy `strict=true`、pytest） |
| `cli/aurora_binaural/__init__.py` | 包导出入口与版本定义 |
| `cli/aurora_binaural/exit_codes.py` | 按方案第 9 节定义退出码（`0`/`2`/`3`/`4`/`5`/`130`）与结构化异常体系 |
| `cli/aurora_binaural/params.py` | 按方案第 9、10 节用 Pydantic v2 实现的统一参数模型（`preset`、`strength`、`seed`、`output`、`timing`）与 JSON Schema 导出 |
| `cli/aurora_binaural/report.py` | 探测信息、BS.1770 响度／真峰值、时间线核验、完整转换报告的 Pydantic v2 模型 |
| `cli/aurora_binaural/stub_core.py` | 遵循方案第 7 节 `Processor` 接口的直通桩核心（`prepare`/`set_params`/`process`/`latency_samples`/`tail_samples`/`reset`）与分块离线执行器 |
| `cli/aurora_binaural/media.py` | `ffmpeg`/`ffprobe` 封装：探测、解码为 `float32le`、BS.1770 `ebur128` 响度与真峰值测量、临时文件原子写入 |
| `cli/aurora_binaural/pipeline.py` | `aurora convert` 端到端链路：探测 → `float32le` 解码 → 响度测量 → 桩核心分块渲染 → 临时文件编码 → 回读解码核验 → 原子替换 → 报告生成 |
| `cli/aurora_binaural/timeline.py` | WAV／M4A（AAC）／MP3 在 44.1 kHz 与 48 kHz 下的时间线、priming 与 padding 实测套件 |
| `cli/aurora_binaural/cli.py` | `aurora` CLI 实现（`convert`、`inspect`、`presets`、`schema`、`timeline-verify`、`batch`、`job`），保证 `stdout` 仅输出 JSON、日志写到 `stderr` |
| `cli/tests/test_aurora_p0.py` | 16 项自动化单元与集成测试（覆盖参数校验、块大小一致性、延时补偿、尾巴策略、回读样本对齐、防覆盖与全部退出码） |
| `api/__init__.py` / `worker/__init__.py` | P0 应用层 API 与 Worker 包骨架（含 `py.typed`） |
| `deploy/Dockerfile` | Linux 容器路线草案（固定基础镜像 `python:3.12.9-slim-bookworm`，安装 `ffmpeg`，非特权用户 `aurora`） |
| `qa/p0/antigravity/params.schema.json` | 由统一 Pydantic v2 模型导出的 JSON Schema（Draft 2020-12） |
| `qa/p0/antigravity/timeline.json` | 44.1 kHz 与 48 kHz 下 WAV／M4A／MP3 编解码时间线、priming 与 padding 实测数据 |
| `qa/p0/antigravity/REPORT.md` | 本报告 |

---

## 2. 实际运行过的命令与退出码

以下所有命令均在本机实际执行并记录真实退出码：

| 序号 | 实际运行命令 | 退出码 | 结果摘要 |
|---|---|---:|---|
| 1 | `uv sync --python 3.12` | `0` | 创建 `.venv`（CPython 3.12.13），安装 `aurora_binaural` 及 `pydantic`、`pytest`、`ruff`、`mypy` |
| 2 | `uv run aurora presets --json` | `0` | `stdout` 输出包含 `room`（默认强度 0.4）、`surround`（0.5）、`orbit`（0.3）及参数范围的 JSON |
| 3 | `uv run aurora schema --output qa/p0/antigravity/params.schema.json` | `0` | 导出 Draft 2020-12 JSON Schema 到 `qa/p0/antigravity/params.schema.json` |
| 4 | `uv run aurora timeline-verify --output qa/p0/antigravity/timeline.json` | `0` | 在系统临时目录中生成测试音频，实测 44.1 kHz／48 kHz 下 WAV、M4A、MP3 的样本数、priming 与 padding，写入 `timeline.json` |
| 5 | `uv run ruff check` | `0` | `All checks passed!` |
| 6 | `uv run mypy` | `0` | `Success: no issues found in 12 source files`（`strict = true` + `pydantic.mypy`） |
| 7 | `uv run pytest` | `0` | `16 passed in 1.73s` |
| 8 | `orbctl start` | `0` | 启动本机 OrbStack Docker 守护进程（初始检查时 daemon 未启动，启动后正常连接） |
| 9 | `docker build -f deploy/Dockerfile -t aurora-binaural:p0 .` | `0` | 基于 `python:3.12.9-slim-bookworm` 构建镜像，安装 Debian `ffmpeg 5.1.9-0+deb12u1` 与 `aurora_binaural` |
| 10 | `docker run --rm --entrypoint /bin/sh aurora-binaural:p0 -c 'ffmpeg -nostdin -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=1" -ac 2 -c:a pcm_s24le /tmp/test_48k.wav && aurora inspect /tmp/test_48k.wav --json'` | `0` | 容器内成功运行 `aurora inspect`，输出 48000 样本、24-bit WAV 元数据与 BS.1770 响度 JSON |
| 11 | `docker run --rm aurora-binaural:p0 timeline-verify` | `0` | 在 Linux 容器（Debian Bookworm `ffmpeg 5.1.9`）内运行完整时间线套件，对比发现 FFmpeg 5.1 与 9.0 在 M4A 尾帧裁剪上的差异（详见第 3、4 节） |

此外，在 `cli/tests/test_aurora_p0.py` 中对方案第 9 节定义的全部退出码做了实测验证：

- **退出码 `0`**：正常执行 `convert`、`inspect`、`presets`、`schema`、`timeline-verify`
- **退出码 `2`（参数错误）**：`--strength 1.5` 越界、输出后缀为 `.aac`（不允许作为输出容器）、CLI 参数格式错误
- **退出码 `3`（输入问题）**：输入文件不存在、损坏／无音频流、目标文件已存在且未传 `--overwrite`
- **退出码 `4`（处理失败）**：核心存在 NaN／Inf、FFmpeg 编码异常
- **退出码 `5`（远程错误）**：`--server http://127.0.0.1:1` 无法连接远程服务
- **退出码 `130`（取消）**：收到 `SIGINT` / `KeyboardInterrupt` 或块边界取消标志触发

---

## 3. 编解码时间线验证与测得的编码延时

### 3.1 测试方法

在临时目录中构造双声道 `float32le` 标准测试信号（含 `-30 dBFS` 440 Hz 底音以保证 BS.1770 响度门限有效，并在样本索引 `1000` 处打入 `+0.85 L / -0.85 R` 的单样本脉冲，在 `N - 500` 处打入尾部标记脉冲）。分别测试：

1. **输入采样率**：`44100 Hz` 与 `48000 Hz`（以及 `44100 Hz -> 48000 Hz` 跨采样率重采样）。
2. **输入样本长度**：
   - 整秒对齐长度（`44100` 样本、`48000` 样本）
   - 非帧对齐素数／奇数长度（`44223` 样本、`48123` 样本，均不能被 AAC 的 `1024` 或 MP3 的 `1152` 整除）
3. **两种回读解码方式对比**：
   - **裸流解码（忽略容器无损间隙元数据）**：对 M4A 传入 `-ignore_editlist 1`，对 MP3 传入 `-flags2 +skip_manual`，直接测量编码器底层的 `priming`（脉冲峰值后移样本数）与 `trailing padding`（尾帧补齐样本数）。
   - **标准容器补偿回读（默认解码）**：利用容器写入的 MP4 Edit List（`elst` + `stts` + `sgpd`/`sbgp`）或 MP3 LAME/Xing Info 头（`-write_xing 1`），测量回读后的样本数与脉冲位置是否与输入严格一致。

### 3.2 实测结果汇总（macOS ARM64，FFmpeg 9.0.2）

完整数据见 `qa/p0/antigravity/timeline.json`，核心测量结果如下表：

| 格式 / 编码器 | 采样率 (Hz) | 输入样本数 $N$ | 编码帧长 | 实测底层 Priming (样本) | 实测底层 Padding (样本) | 裸流回读样本数（差值） | 容器补偿后回读样本数（差值） | 补偿后脉冲偏移 | 样本数是否一致 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **WAV** (`pcm_s24le`) | 44100 | 44100 | 1 | **0** | **0** | 44100 (`+0`) | **44100 (`+0`)** | `0` | 是 |
| **WAV** (`pcm_s24le`) | 44100 | 44223 | 1 | **0** | **0** | 44223 (`+0`) | **44223 (`+0`)** | `0` | 是 |
| **WAV** (`pcm_s24le`) | 48000 | 48000 | 1 | **0** | **0** | 48000 (`+0`) | **48000 (`+0`)** | `0` | 是 |
| **WAV** (`pcm_s24le`) | 48000 | 48123 | 1 | **0** | **0** | 48123 (`+0`) | **48123 (`+0`)** | `0` | 是 |
| **M4A** (`aac` 原生) | 44100 | 44100 | 1024 | **1024** | **0** (`stts` 截断) | 45124 (`+1024`) | **44100 (`+0`)** | `0` | 是 |
| **M4A** (`aac` 原生) | 44100 | 44223 | 1024 | **1024** | **0** (`stts` 截断) | 45247 (`+1024`) | **44223 (`+0`)** | `0` | 是 |
| **M4A** (`aac` 原生) | 48000 | 48000 | 1024 | **1024** | **0** (`stts` 截断) | 49024 (`+1024`) | **48000 (`+0`)** | `0` | 是 |
| **M4A** (`aac` 原生) | 48000 | 48123 | 1024 | **1024** | **0** (`stts` 截断) | 49147 (`+1024`) | **48123 (`+0`)** | `0` | 是 |
| **MP3** (`libmp3lame`) | 44100 | 44100 | 1152 | **1105** | **875** | 46080 (`+1980`) | **44100 (`+0`)** | `0` | 是 |
| **MP3** (`libmp3lame`) | 44100 | 44223 | 1152 | **1105** | **752** | 46080 (`+1857`) | **44223 (`+0`)** | `0` | 是 |
| **MP3** (`libmp3lame`) | 48000 | 48000 | 1152 | **1105** | **431** | 49536 (`+1536`) | **48000 (`+0`)** | `0` | 是 |
| **MP3** (`libmp3lame`) | 48000 | 48123 | 1152 | **1105** | **308** | 49536 (`+1413`) | **48123 (`+0`)** | `0` | 是 |

同时实测了两个重要的对照组（见 `timeline.json` 的 `diagnostic_comparisons`）：

| 对照变体 | 采样率 (Hz) | 输入样本数 $N$ | 实测底层 Priming (样本) | 实测底层 Padding (样本) | 默认回读样本数（差值） | 结论 |
|---|---:|---:|---:|---:|---:|---|
| **M4A (`aac_at` Apple AudioToolbox)** | 44100 | 44100 | **2112** | **956** | 45056 (`+956`) | `elst` 仅裁掉了开头 2112 priming，未裁剪末尾 956 样本 padding |
| **M4A (`aac_at` Apple AudioToolbox)** | 48000 | 48000 | **2112** | **128** | 48128 (`+128`) | `elst` 仅裁掉了开头 2112 priming，未裁剪末尾 128 样本 padding |
| **原始 ADTS `.aac`（无容器）** | 44100 | 44100 | **1024** | **956** | 46080 (`+1980`) | ADTS 流无 `elst`/`stts`，默认回读泄漏全部 1024 priming + 956 padding |
| **原始 ADTS `.aac`（无容器）** | 48000 | 48000 | **1024** | **128** | 49152 (`+1152`) | ADTS 流无 `elst`/`stts`，默认回读泄漏全部 1024 priming + 128 padding |

### 3.3 编码器 Priming / Padding 机理与补偿策略

1. **内部引擎延时补偿（所有格式通用）**
   - 核心通过 `Processor.latency_samples()` 报告算法前瞻延时 $L_{\text{engine}}$，通过 `Processor.tail_samples()` 报告混响尾巴 $T_{\text{tail}}$。
   - 当 `timing.mode == "preserve"` 时，离线管线向核心多送入 $L_{\text{engine}} + T_{\text{extra}}$ 个静音帧，并丢弃核心输出的前 $L_{\text{engine}}$ 个样本帧：
     - `timing.tail == "trim"`：$T_{\text{extra}} = 0$，输出帧数严格等于输入帧数 $N$，并在末端施加短余弦淡出（默认 `5.0 ms`）防止截断咔嗒声。
     - `timing.tail == "preserve"`：$T_{\text{extra}} = T_{\text{tail}}$，输出帧数为 $N + T_{\text{tail}}$，并在报告 `timeline.extra_tail_samples_kept` 中记录多出的样本数。

2. **WAV（`pcm_s24le` / `pcm_s16le` / `pcm_f32le`）**
   - **Priming = 0，Padding = 0**。无需编解码补偿，任意采样率、任意非对齐样本数下回读 100% 精确对齐，是 AI 视频制作流程的首选中间格式。

3. **M4A（AAC-LC）**
   - **编码器选择**：强制指定 FFmpeg 原生 `-c:a aac` 搭配 M4A 容器（`-f ipod -movflags +faststart`），**禁止在 macOS 上调用 `aac_at`**。原因：实测表明 `aac_at` 使用 2112 样本 priming，且不向 `movenc` 报告尾包有效时长，导致默认回读多出 `(1024 - (N % 1024)) % 1024` 个尾填充样本（例如 44.1 kHz 1 秒多出 956 个样本，48 kHz 1 秒多出 128 个样本），且在 Linux 上不存在 `aac_at`。
   - **容器限制**：**禁止输出裸 `.aac`（ADTS）**。实测表明 ADTS 流无法承载 `elst`/`stts` 元数据，回读必然多出 `1024` 个开头 priming 样本和尾帧 padding（CLI 已对 `.aac` 输出直接拦截并返回退出码 `2`）。
   - **Priming 与 Padding 数值**：FFmpeg 原生 `aac` 编码器固定 `priming = 1024` 样本（1 个 AAC-LC MDCT 帧），尾帧补齐为 $P_{\text{tail}} = (1024 - (N \bmod 1024)) \bmod 1024$。FFmpeg 写入 MP4 `elst`（Edit List）和 `stts`（最后一包精确 `duration`）。
   - **补偿与核验策略**：
     - 在支持 MP4 `elst` + 尾包 `duration` 裁剪的解码器（如 FFmpeg ≥ 7.0 / 9.0.2）下，容器自动剥离前 `1024` 样本与尾包 padding，回读样本差为 `0`，脉冲偏移为 `0`。
     - 对不支持 `elst` 的裸流解码器，补偿切片区间为 `[1024 : 1024 + N]`。

4. **MP3（`libmp3lame`）**
   - **Priming 与 Padding 数值**：`libmp3lame` 编码器固有延时为 `576` 样本，MPEG-1 Layer III 合成滤波器组解码器固有延时为 `529` 样本，**总往返 priming 恒为 `1105` 样本**；总裸帧数为 $\lceil (N + 1105) / 1152 \rceil \times 1152$，因此尾部填充样本数为：
     $$P_{\text{tail}} = \left\lceil \frac{N + 1105}{1152} \right\rceil \times 1152 - N - 1105$$
   - **补偿策略**：编码时显式传入 `-c:a libmp3lame -write_xing 1`，将 `enc_delay=576` 与 `enc_padding` 写入 LAME/Xing Info 头部。支持 Gapless 的解码器（包括 FFmpeg 5.1 与 9.0）在回读时自动剥离 `1105` 个开头样本与尾部 padding，实测 44.1 kHz／48 kHz（含非帧对齐长度 `44223`、`48123`）回读样本差均为 `0`，脉冲偏移均为 `0`。若下游工具忽略 Xing 头，则按 `[1105 : 1105 + N]` 裁剪。

---

## 4. 失败项、跨平台差异发现与已知局限

1. **Debian Bookworm 容器内 `ffmpeg 5.1.9` 的 M4A 尾包裁剪局限（重要实测发现）**
   - 在本机 macOS（`ffmpeg 9.0.2`）下，WAV、M4A（`aac`）、MP3 的所有测试用例（含非对齐样本数）回读样本差均为 `0`。
   - 但在 `deploy/Dockerfile`（`python:3.12.9-slim-bookworm`）容器内运行 `aurora timeline-verify` 时，Debian 12 自带的旧版 `ffmpeg 5.1.9-0+deb12u1` 表现出差异：
     - WAV 与 MP3 在容器内依然 100% 精确对齐（样本差为 `0`）。
     - M4A 在容器内通过 `ffprobe -show_packets` 检查确认：容器已正确写入首包 `elst` 偏移（剥离 1024 priming）以及尾包精确时长（例如 48000 样本输入的最后一包 `pts=47104, duration=896`，总和恰好 48000）；**但 FFmpeg 5.1.9 的 AAC 解码器在回读时不裁剪最后一包的冗余样本（输出全部 1024 样本而非 896 样本）**，导致容器内默认回读多出 `+128` 样本（48000 输入）或 `+956` 样本（44100 输入）。
     - 此时 `aurora convert` 按方案第 8 节要求，诚实将 `timeline.sync_within_tolerance` 标记为 `false`，并输出 `sync_warning` 提示用户改用 WAV 48 kHz／24-bit，绝不谎报同步通过。
2. **本机 Docker 守护进程初始状态**
   - 首次执行 `docker info` 时，本机 OrbStack 守护进程处于 `Stopped` 状态；通过 `orbctl start` 启动后，成功完成了镜像构建与容器内 `aurora inspect`、`aurora timeline-verify` 验证。
3. **P0 范围局限**
   - 核心目前为直通桩（`StubProcessor`），不执行真实空间 DSP。
   - `--server` 远程任务提交流程在 P0 仅做连接探测与退出码 `5` 验证，完整异步 API／SQLite Worker 在 P3 实现。

---

## 5. 对 P2（CLI）与 P3（API／Worker／部署）的修改建议

1. **对 P2（CLI）的建议**
   - **默认采样率策略**：当前 `OutputSpec.sample_rate` 按方案第 10 节默认设为 `48000` Hz（同时在 CLI 提供了 `--keep-sample-rate` 选项）。建议在 P2 明确：当用户通过 CLI 转换 44.1 kHz 素材且未显式传 `--sample-rate` 时，核心内部以 48 kHz 渲染后，默认输出 48 kHz（符合方案第 8 节"重采样到 48 kHz（记录原始采样率）"），并在转换报告 `input.sample_rate` 与 `output.sample_rate` 中同时呈现；若用户需要保持 44.1 kHz 样本数 1:1，可传 `--sample-rate 44100` 或 `--keep-sample-rate`。
   - **原始 `.aac` 格式策略（关闭方案 4.1 节待定项）**：建议在 ADR 中正式确认**原始 `.aac`（ADTS）仅允许作为输入，禁止作为输出格式**。本次 P0 实测已证明 ADTS 容器无 `elst`/`stts` 表，无法消除 1024 样本 priming 与尾帧 padding。
   - **编码器固定策略**：在 macOS 与 Linux 上统一强制使用 `-c:a aac`（或可选检测 `libfdk_aac`）输出 M4A，禁止使用 `aac_at`，保证跨平台行为一致。

2. **对 P3（API／Worker／Docker 部署）的建议**
   - **升级容器内 FFmpeg 版本至 ≥ 7.0（如 Debian Trixie 或固定版本的静态 FFmpeg 构建）**：本次容器实测表明 Debian 12 Bookworm 仓库自带的 `ffmpeg 5.1.9` 在回读 M4A 时不会自动按尾包 `duration` 裁剪末尾 padding，而 FFmpeg ≥ 7.0（及本机 9.0.2）已修复该行为。建议 P3 的 `deploy/Dockerfile` 采用 `python:3.12-slim-trixie`（含 FFmpeg 7.x）或挂载固定 SHA-256 的 FFmpeg 7.1/8.0 静态二进制，使 Linux 容器内的 M4A 回读校验与 macOS 一样达到 `0` 样本误差。
   - **多阶段构建集成 Rust 核心**：P1 Cursor 交付 `core/` 的 PyO3/maturin wheel 后，`deploy/Dockerfile` 应改为多阶段构建（Stage 1: `rust:1.85-slim-bookworm` + `maturin build --release`；Stage 2: Python 运行时镜像安装 wheel 与 `ffmpeg`），并在构建期校验 `data/` 下 HRTF `.sofa` 文件的 SHA-256。
