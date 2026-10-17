# P6-codex-03 交付报告

日期：2026-10-17。执行方：Codex。分支：`agent/codex/P6-03`。应用代码提交：`14e3635`。

**五项任务完成。macOS 与 Ubuntu 22.04 ARM64 实际执行安装及完整应用回归，均通过。** 只在当前分支提交，未推送；未修改 `core/`、`presets/`、`tools/`、`qa/tools/`、`prototypes/`。Linux 使用隔离 Docker 容器，未改现有 Ubuntu VM。应用代码和文档的 18 个变更文件与 Linux 实测快照逐字节相同，见 [源码核对记录](checks-03/source-verification.json)。

## 1. 一键安装

新增可执行的 `scripts/setup.sh`，从脚本位置定位仓库，可重复执行。

- 检查 cargo/rustup；PATH 缺少时依次尝试 `/opt/homebrew/opt/rustup/bin`、`$HOME/.cargo/bin`，失败输出安装提示。另检查 uv、node、npm。
- 顺序运行 `uv sync --locked --python 3.12`、`bash scripts/build-core.sh`、`cd web && npm ci && npm run build`。在同步后、核心构建前检测 FFmpeg，缺少 soxr 时提前给出操作提示。
- 固定 Python 3.12：干净 Linux 首次试跑时 uv 自动选择了 Python 3.15，当前 PyO3 0.25 不支持；固定项目支持的解释器后，重跑及重复安装均成功。
- 实际运行 44.1 → 48 kHz soxr 重采样，选中的 FFmpeg/ffprobe 原子写入 `.aurora-local.env`；此文件及写入临时文件均已加入 gitignore。输出包含 `brew install ffmpeg-full` 提示。
- 最后一行严格为：下一步运行 `uv run aurora gui`。

实际输出摘要：

| 平台 | 环境与探测结果 | 安装结果 |
|---|---|---|
| macOS 26.7.1 / ARM64 | Python 3.12.13，Rust 1.99.1；PATH FFmpeg 9.0.2 不支持 soxr，自动选择 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` 9.0.1；ffprobe 使用 PATH 中的 9.0.2 | 首次、重复、最终 Python 3.12 + Rust PATH 回退执行均退出 0；核心 smoke 显示 `Rust 0.1.0 / V / V3 / SADIE II D1`，npm ci/build 成功 |
| Ubuntu 22.04 / ARM64 容器 | Python 3.12.15，Rust 1.99.1，Node 22.23.2；自动选择 `/usr/bin/ffmpeg` 与 `/usr/bin/ffprobe` 4.4.2，实际 soxr 试跑成功 | 最终脚本连续两次退出 0；相同的 V3 核心 smoke 通过，npm ci/build 成功 |

macOS 常规检查使用 Node 24.12.0；Rust PATH 回退执行把 Homebrew 放在前面，实际使用 Node 26.10.0，安装同样成功。该次 PATH 不包含 Rust 目录，由脚本自行补齐。启动完整检查时未显式设置 `AURORA_FFMPEG` / `AURORA_FFPROBE`。

完整输出：[macOS 首次](checks-03/macos-setup-first.txt)、[重复](checks-03/macos-setup-repeat.txt)、[最终解释器及 PATH 回退](checks-03/macos-setup-python312-path-fallback.txt)、[Linux 首次](checks-03/linux-setup-1.txt)、[重复](checks-03/linux-setup-2.txt)。Linux 构建环境与执行命令保存在 [Dockerfile](checks-03/linux.Dockerfile) 和 [检查脚本](checks-03/linux-checks.sh)。

## 2. 应用自动探测

安装与应用共用 `ffmpeg_discovery.py`。显式环境变量优先；未设置时读取仓库 `.aurora-local.env`，或依次探测 PATH 与 Homebrew ffmpeg-full。ffprobe 同样处理，并支持所选 FFmpeg 的同目录探测。配置路径失效时自动重新探测；显式错误配置会报错，不会静默替换。

探测依据是实际 soxr 试跑，不依赖 filter help。配置只解析两个字面路径，不执行 shell；支持带空格的路径。没有 soxr 时，仍可处理不需重采样的文件；一旦需要重采样就以 `SOXR_UNAVAILABLE` 明确失败。

5 项新增探测回归覆盖 PATH 优先、Homebrew 回退、ffprobe 回退、带空格路径与跨目录配置读取、失效配置、显式覆盖、缺少 soxr 和配置不被执行。真实 Rust 回归包含未设置环境变量时的 44.1 kHz FLAC 导入、处理和回读；96 kHz FLAC 的真实 GUI 流程也通过。

## 3. 测试稳定性与版本号

- **AAC/M4A**：FFmpeg <8 按 1024 样本 AAC 帧精确断言尾部填充：`padding = (-expected_samples) % 1024`，同时要求报告警告及正确的 `sync_within_tolerance`。9.x 仍要求精确回读。4.4.2 的实测例子：预期 11886／12937／25874，回读 12288／13312／26624；分别多 402／375／750 样本。没有把旧版有填充的输出标成精确同步。
- **取消竞态**：原实现先发布数据库 `canceled`，再在 `finally` 删除 attempt 目录。现在先停止租约心跳、清理该 attempt，再发布终态；异常失败转取消的路径也使用该顺序。取消终态写入同时严格检查 lease，旧 Worker 不能终结另一个 attempt。
- 保留原取消测试的即时清理断言；新增两个确定性测试，在终态写入边界检查目录已经消失、此前状态仍为 running，分别覆盖取消异常和处理异常。原用例另行连续执行 **30/30 通过**，见 [压力复跑记录](checks-03/macos-cancel-stress.txt)。
- **核心版本**：依次读取 `aurora-py`、`aurora_core` 的 wheel 元数据，最后才使用模块 `__version__`。新增无模块版本号时两种 distribution 名称的回归。
- 修正随 V3 默认合入后过期的 V2 硬编码测试，以及 OpenAPI 默认值快照；默认值继续取自 TOML，未修改预设。

## 4. 时间线新定义

`verify_02.py` 与应用回归均执行：**样本数一致，旁路逐位对齐，空间路径峰值偏移 ≤1 ms，并记录偏移。** 已移除 ±1 源采样的空间峰值要求；故障排查文档同步更新。[新证据](evidence-03.json) 写入独立文件，保留旧 `evidence-02.json` 历史记录。

下表适用于 FLAC 24 bit、WAV 32f；V2 独立脚本与 TOML 当前默认 V3 的应用回归得到相同峰值位置。每个采样率的输出样本数差均为 0，旁路峰值位置误差均为 0。

| 源采样率 | 空间峰值偏移（源样本） | 空间峰值偏移（ms） | ≤1 ms |
|---|---:|---:|---|
| 44100 | −12 | −0.272109 | 通过 |
| 48000 | −14 | −0.291667 | 通过 |
| 88200 | −24 | −0.272109 | 通过 |
| 96000 | −28 | −0.291667 | 通过 |
| 176400 | −51 | −0.289116 | 通过 |
| 192000 | −55 | −0.286458 | 通过 |

旁路逐位检查比较原生 48 kHz renderer 输入／输出 PCM 字节，全部相同；48 kHz 源文件还核对端到端 PCM 逐位一致。其他源采样率需要经过 soxr 往返，波形幅度会变化，不能声称其源文件与输出文件 PCM 字节相同；对此另行严格断言样本数相同、旁路峰值偏移恰为 0。`source_to_output_bit_exact` 与 `bypass_renderer_bit_exact` 在证据中分别记录。

`uv run python qa/p6/codex/verify_02.py` 返回 0，`spatial_timeline_pass`、`bypass_timeline_pass`、`timeline_acceptance_pass` 均为 true。应用回归的逐例偏移另存 [JUnit 记录](checks-03/macos-timeline.xml)。Linux 完整 pytest 同样执行全部新时间线条件。

## 5. 文档与完整检查结果

`docs/user/quickstart.md` 开头新增 `bash scripts/setup.sh` / `uv run aurora gui` 的一步式流程、前置工具、soxr 安装及自动配置说明；`troubleshooting.md` 更新探测和新时间线定义。

| 检查 | macOS | Ubuntu 22.04 / FFmpeg 4.4.2 |
|---|---|---|
| `bash scripts/setup.sh`，含 npm ci/build | 通过，退出 0 | 连续两次通过，退出 0 |
| `.venv/bin/pytest -ra tests/app` | **162 passed, 1 skipped**（88.06 s） | **162 passed, 1 skipped**（88.99 s） |
| `.venv/bin/ruff check src tests/app` | 通过 | 通过 |
| `.venv/bin/mypy src tests/app` | 通过，37 个源文件 | 通过，37 个源文件 |
| `npm --prefix web run lint` | 通过 | 通过 |
| `npm --prefix web run typecheck` | 通过 | 通过 |
| `npm --prefix web run test:e2e` | **4 passed** | 未执行浏览器测试 |
| `npm --prefix web run test:e2e:rust` | **2 passed**，真实 Rust / FLAC / 四种导出回读 | 未执行浏览器测试 |
| `bash -n scripts/setup.sh` / `git diff --check` | 通过 | 安装实际执行通过 |

唯一 skip 是新路线 V binding 下不适用的旧 binding 兼容测试。两平台各有一条现有 Starlette/httpx 弃用警告，没有失败或不稳定重试。Linux 首轮归档曾带入 macOS AppleDouble 元数据文件，已用不携带 xattr 的源文件快照重跑；最终 Ruff/ESLint 检查没有屏蔽这些错误。

完整结果：[macOS pytest](checks-03/macos-pytest.txt)、[ruff](checks-03/macos-ruff.txt)、[mypy](checks-03/macos-mypy.txt)、[npm lint](checks-03/macos-npm-lint.txt)、[typecheck](checks-03/macos-npm-typecheck.txt)、[通用 E2E](checks-03/macos-stub-e2e.json)、[Rust E2E](checks-03/macos-rust-e2e.json)、[Linux 全项退出码](checks-03/linux-results.txt)、[pytest](checks-03/linux-pytest.txt)、[ruff](checks-03/linux-ruff.txt)、[mypy](checks-03/linux-mypy.txt)、[npm lint](checks-03/linux-npm-lint.txt)、[typecheck](checks-03/linux-npm-typecheck.txt)。

各子项已独立提交，提交信息均以 `Agent: Codex` 结尾：`476588d`（安装）、`09870b8`（应用探测）、`c2388a3`（快速开始）、`9413859`（时间线）、`bddde7d`（解释器兼容）、`14e3635`（稳定性与版本）。本报告及证据另行提交。全部为本地提交，未推送。
