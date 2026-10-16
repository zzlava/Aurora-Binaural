# P2-antigravity-01 交付报告：CLI 完整实现（接桩核心）、流式处理管线与 P0 后续事项

- **阶段与任务编号**：`P2-antigravity-01`
- **执行代理**：Antigravity (`agy`)
- **工作分支**：`agent/antigravity/P2-01`
- **日期**：2026-10-16 (Asia/Shanghai)
- **依据文档**：`docs/plan.md` 第 7、8、9、10 节；`docs/adr/0002-app-layer-parallel-and-package-layout.md`；`qa/p0/ACCEPTANCE-antigravity-01.md`；`docs/tasks/P2-antigravity-01.md`

---

## 1. 改动摘要与文件列表

### 1.1 包布局迁移（ADR 0002）
使用 `git mv` 将原顶层 `cli/`、`api/`、`worker/` 下的代码与 `cli/tests/` 统一迁移至 `src/aurora_binaural/` 与 `tests/app/`，完整保留 Git 历史：
- `src/aurora_binaural/__init__.py`（原 `cli/aurora_binaural/__init__.py`）
- `src/aurora_binaural/py.typed`（原 `cli/aurora_binaural/py.typed`）
- `src/aurora_binaural/engine.py`（原 `cli/aurora_binaural/stub_core.py`，并保留 `src/aurora_binaural/stub_core.py` 兼容导出）
- `src/aurora_binaural/params.py`（原 `cli/aurora_binaural/params.py`）
- `src/aurora_binaural/exit_codes.py`（原 `cli/aurora_binaural/exit_codes.py`）
- `src/aurora_binaural/report.py`（原 `cli/aurora_binaural/report.py`）
- `src/aurora_binaural/pipeline.py`（原 `cli/aurora_binaural/pipeline.py`）
- `src/aurora_binaural/timeline.py`（原 `cli/aurora_binaural/timeline.py`）
- `src/aurora_binaural/cli/__init__.py`（原 `cli/aurora_binaural/cli.py`）
- `src/aurora_binaural/cli/remote.py`（新增：符合第 10 节 API 合同的 HTTP 客户端）
- `src/aurora_binaural/media/__init__.py`（原 `cli/aurora_binaural/media.py`）
- `src/aurora_binaural/api/__init__.py`、`src/aurora_binaural/api/py.typed`（原 `api/`）
- `src/aurora_binaural/worker/__init__.py`、`src/aurora_binaural/worker/py.typed`（原 `worker/`）
- `tests/app/test_aurora_p0.py`（原 `cli/tests/test_aurora_p0.py`）
- `tests/app/test_aurora_p2.py`（新增：P2 完整单元测试与端到端测试）
- `pyproject.toml`、`uv.lock`（更新 `src/aurora_binaural` 与 `tests/app` 路径，新增 `numpy>=2.0.0`）
- `deploy/Dockerfile`（切换至固定 SHA-256 的 `python:3.12.15-slim-trixie` 基础镜像与 `ffmpeg 7:7.1.*`，同步 `src/` 布局）

### 1.2 核心协议与渲染器（`aurora_binaural.engine`）
- **`Renderer` Protocol**：定义 `prepare(sample_rate, max_block, config)`、`set_params(params)`、`process(block) -> block`、`latency_samples()`、`tail_samples()`、`flush() -> block`、`reset()`。
- **`StubRenderer`**：基于 `numpy` (`NDArray[np.float32]`) 向量化切片与拼接重写，彻底移除 Python 列表逐样本循环；支持可配置的固定延时（默认 `0`）。
- **`DelayTestRenderer`**：测试专用渲染器，具有已知固定延时（默认 128 样本）和已知确定性余弦指数衰减尾巴（默认 240 样本，可通过 `expected_tail()` 获取真值），用于严格验证延时补偿与 `trim` / `preserve` 两种尾巴策略。

### 1.3 流式处理管线（`aurora_binaural.media` & `aurora_binaural.pipeline`）
- **FFmpeg 管道流式解码与编码**：通过 `stream_decode_f32le` 与 `StreamingF32LeEncoder` 按块（默认 `1024` 帧）流式读写，配合 `RendererStreamProcessor` 的固定窗口缓冲（仅保留末尾 10 ms 淡出窗口，48 kHz 下仅 480 帧 ≈ 3.8 KB），全链路内存占用为 $O(\text{block\_size})$，不随文件时长增长。
- **延时补偿与尾巴策略**：
  - 自动丢弃前 `latency_samples()` 帧并在流结束时调用 `renderer.flush()` 补齐延时帧。
  - `timing.tail=trim`：输出样本数严格等于输入样本数，末端施加 `10 ms` 半余弦平滑淡出（`fade_out_ms=10.0`）。
  - `timing.tail=preserve`：完整保留 `flush()` 输出的 `tail_samples()` 帧，并在报告 `timeline.extra_tail_samples_kept` 中记录多出的样本数。

### 1.4 CLI 命令与 P0 后续事项（F-AG-1 / F-AG-2）
- 实现完整的 `convert`、`batch`、`presets`、`inspect`、`compare`（BS.1770 等响度匹配与 `-1.0 dBTP` 真峰值保护）、`gui`（在 P4 前明确返回退出码 `2` 及 `GUI_NOT_IMPLEMENTED`）、`job status` / `job cancel` 及 `convert --server`（实现 `POST /v1/assets`、`POST /v1/jobs`、`GET /v1/jobs/{id}`、`POST /v1/jobs/{id}/cancel`、`GET /v1/jobs/{id}/result`、`GET /v1/jobs/{id}/report`）。
- **F-AG-1**：启动与处理时检测 `ffmpeg` 版本；当版本 `< 7` 且涉及 M4A/AAC 输入或输出时，默认在 `stderr` 与报告 `timeline.sync_warning` 中输出明确降级提示；启用 `--strict-ffmpeg`（或 `AURORA_STRICT_FFMPEG=1`）时以退出码 `4`（`FFMPEG_VERSION_TOO_OLD`）拒绝执行。
- **F-AG-2**：M4A 回读测试感知 `ffmpeg` 版本，在旧版 FFmpeg 下断言程序如实输出了 `sync_warning`。

---

## 2. 实际运行过的命令及退出码

| 序号 | 命令 | 退出码 | 结果摘要 |
|---:|---|---:|---|
| 1 | `uv sync` | `0` | 解析并安装 `aurora-binaural==0.1.0` 与 `numpy==2.5.4` |
| 2 | `uv run aurora --help` | `0` | 正常列出 `convert`、`compare`、`inspect`、`presets`、`gui`、`schema`、`timeline-verify`、`batch`、`job` 子命令 |
| 3 | `uv run aurora gui` | `2` | stdout 输出 `{"ok": false, "error": {"code": "GUI_NOT_IMPLEMENTED", "exit_code": 2, ...}}` |
| 4 | `uv run ruff check` | `0` | `All checks passed!` |
| 5 | `uv run mypy` | `0` | `Success: no issues found in 15 source files`（`--strict` 模式） |
| 6 | `uv run pytest -v` | `0` | `33 passed in 6.66s`（覆盖 P0 16 项 + P2 17 项测试） |
| 7 | `/usr/bin/time -l uv run --no-sync aurora convert in_60min_48k_stereo.wav --output out_60min_48k_stereo.wav --preset room --strength 0.4 --block-size 1024 --simulated-latency 256 --report report_60min.json` | `0` | 60 分钟 48 kHz 立体声流式转换与回读验证通过（详见第 3 节） |
| 8 | `docker build -f deploy/Dockerfile -t aurora-binaural:p2-trixie .` | `0` | 基于 `python:3.12.15-slim-trixie` 构建成功，内含 `ffmpeg 7:7.1.5-0+deb13u1` |
| 9 | `docker run --rm aurora-binaural:p2-trixie timeline-verify` | `0` | 容器内完成 WAV / M4A / MP3 时间线测试并输出 JSON 报告 |

---

## 3. 内存与速度实测（含 60 分钟 48 kHz 立体声文件）

### 3.1 测试环境与输入规格
- **硬件与系统**：macOS Darwin ARM64（Apple Silicon），Python 3.12.13，numpy 2.5.4，FFmpeg 9.0.2
- **输入文件**：60 分钟（`3,600.0` 秒）48 kHz 双声道 WAV，总样本帧数 `172,800,000` frames/channel（未压缩 float32 等效体积 `1,382.4 MB`）
- **输出配置**：WAV 48 kHz / 24-bit（输出文件体积 `988.8 MB`），`block_size = 1024`，`simulated_latency_samples = 256`，`timing.tail = trim`（含末端 10 ms 淡出）
- **执行的完整环节**：
  1. `ffprobe` 流元数据探测
  2. 输入文件流式 BS.1770 响度与真峰值测量（`ffmpeg -af ebur128=peak=true`）
  3. 流式解码 → `StubRenderer`（1024 帧分块 + 256 帧延时补偿 + 10 ms 末端淡出）→ 流式 24-bit WAV 编码
  4. 输出文件流式回读样本数校验与 NaN/Inf 检查
  5. 输出文件流式 BS.1770 响度与真峰值测量 + 原子文件替换

### 3.2 实测结果（`/usr/bin/time -l`）

| 指标 | 实测值 | 任务要求 / 目标 | 结论 |
|---|---:|---:|---|
| 输入样本帧数（每声道） | `172,800,000` | `172,800,000` | 一致 |
| 输出回读样本帧数（每声道） | `172,800,000` | `172,800,000` (`sample_diff = 0`) | 严格对齐 |
| 补偿的引擎延时样本数 | `256` | `256` | 严格对齐 |
| 处理前后 BS.1770 综合响度 | `-21.8 LUFS` / `-21.8 LUFS` | 一致 | 通过 |
| 总耗时（含前后两次 BS.1770 与回读） | `27.02 s` (`26,870.11 ms`) | — | **133.2× 实时速率** |
| 最大常驻内存集（Maximum RSS） | `70,615,040 B` (**67.34 MiB**) | **< 500 MB** | **远低于上限（仅占上限 13.5%）** |
| 峰值内存足迹（Peak Memory Footprint） | `8,372,656 B` (**7.98 MiB**) | **< 500 MB** | **通过** |

---

## 4. 失败项与已知局限

1. **所有 P2 验收测试与静态检查均已通过（0 失败项）**。
2. **Debian Trixie (`ffmpeg 7.1.5`) 下 M4A 尾包裁剪的实测发现**：
   - 在容器验证中，Debian 13 (Trixie) 的 `ffmpeg 7:7.1.5-0+deb13u1` 在编码 M4A 时已正确将 `duration_ts = 48000` 和尾包 `duration = 896` 写入 MP4 `stts`/`elst`，且首包通过 `AV_PKT_DATA_SKIP_SAMPLES` 精确剥离了 1024 个 priming 样本（脉冲偏移为 `-1` ~ `0`）。
   - 但 `libavformat/mov.c` 在解码时将尾包 `duration < 1024` 转化为 `AV_PKT_DATA_SKIP_SAMPLES`（尾包裁剪）的补丁是在 FFmpeg 8.0+（本机 macOS FFmpeg 9.0.2 已包含）才合入的；因此在 FFmpeg 7.1.5 默认解码 M4A 时，尾包仍会多出 `1024 - 896 = 128` 个样本。
   - 我们的管线在 FFmpeg 7.1.5 下如实检测到该差异（`sync_within_tolerance = false`）并输出 `sync_warning` 提示改用 WAV 48 kHz / 24-bit，同时 `F-AG-2` 测试已兼容这一行为，绝不谎报同步通过。
3. **`aurora compare` 的两遍流式处理开销**：
   - 为保证在未知渲染器增益变化下同时满足 BS.1770 等响度（误差 ≤ 0.2 LU）和真峰值不超过 `-1.0 dBTP`（方案 16.2 节），`compare_audio` 先在临时目录生成 32-bit float 参考文件测量干声与湿声的精确 LUFS / dBTP，计算安全对齐增益后再流式写出最终对比文件。内存占用仍为 $O(\text{block\_size})$，但耗时约为单次转换的 2 倍。

---

## 5. 对 P3（HTTP API / Worker / 部署）的建议

1. **Worker 取消检查直接接入 `convert_audio(..., cancel_check=...)`**：
   - `pipeline.convert_audio` 与 `engine.RendererStreamProcessor` 已原生支持 `cancel_check: Callable[[], bool] | None` 参数，在每个 `block_size`（1024 帧，约 21.3 ms）边界检查取消标志，一旦触发立即终止子进程并清理 `.aurora_tmp_*` 临时文件，抛出 `OperationCanceledError`（退出码 130）。P3 的 SQLite Worker 可直接传入租约／取消状态检查回调。
2. **容器内 M4A 帧级精确回读方案**：
   - 若 P3 部署容器需要 M4A 输出在容器内也达到 0 样本误差，可在 `Dockerfile` 中使用固定 SHA-256 的 FFmpeg 8.0+ 静态构建，或在解码 M4A 时读取 `ffprobe` 的 `stream.duration_ts` 进行精确样本数截断。
3. **复用 `aurora_binaural.cli.remote` 与统一模型**：
   - `src/aurora_binaural/cli/remote.py` 已按方案第 10 节完整实现了客户端协议（含 `X-Asset-Sha256`、`Idempotency-Key`、状态轮询、取消与报告下载），P3 的 FastAPI 服务端可直接复用 `aurora_binaural.params.AuroraParams` 与 `aurora_binaural.report.ConversionReport` 作为请求与响应 Schema。
