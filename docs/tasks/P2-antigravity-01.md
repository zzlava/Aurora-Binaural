# P2-antigravity-01：CLI 完整实现（接桩核心）与 P0 后续事项

状态：已派发（2026-10-16）
派单方：Claude　执行方：Antigravity（`agy`）
依据：docs/plan.md 第 7、8、9 节；`docs/adr/0002-app-layer-parallel-and-package-layout.md`；`qa/p0/ACCEPTANCE-antigravity-01.md`

## 工作环境

- 独立克隆 `.agents/antigravity/`，分支 `agent/antigravity/P2-01`（基于最新 main）。
- 可以修改：`src/`、`tests/app/`、`deploy/`、`qa/p2/antigravity/`、根目录的 `pyproject.toml` 和 `uv.lock`，以及按 ADR 0002 从 `cli/`、`api/`、`worker/` 迁出代码（用 `git mv`，保留历史）。
- 不要修改：`core/`、`prototypes/`、`data/`、`presets/`、`docs/`、`tools/`。

## 任务

1. **包布局迁移**（ADR 0002）
   - 应用层统一放到 `src/aurora_binaural/`，测试放到 `tests/app/`。
   - 迁移后 `uv run aurora --help` 和全部测试照常通过。
2. **核心协议** `aurora_binaural.engine`
   - 定义 `Renderer` Protocol，包含：`prepare(sample_rate, max_block, config)`、`set_params(params)`、`process(block) -> block`、`latency_samples()`、`tail_samples()`、`flush() -> block`、`reset()`。
   - 用 numpy 重写桩核心 `StubRenderer`，不要再用 Python 列表逐样本处理；为了测试延时补偿，加一个可配置的固定延时（默认 0）。
   - 加一个**测试专用**的 `DelayTestRenderer`：带已知延时和已知尾巴，用来验证补偿和尾巴处理是否正确。
3. **流式处理流水线**
   - 解码后按块（可配置，默认 1024）送进 Renderer。
   - 按 `latency_samples()` 补偿延时。
   - `timing.tail=trim` 时，末端做 10 ms 淡出，输出样本数严格等于输入。
   - `timing.tail=preserve` 时，追加 `flush()` 的输出，并在报告里记录多出的样本数。
   - 长文件不能一次全部读进内存：用 ffmpeg 管道流式解码和编码，60 分钟 48 kHz 立体声的峰值内存控制在 500 MB 以下。实测并记录。
4. **CLI 命令**（plan 第 9 节）
   - `convert`、`batch`、`presets`、`inspect`、`compare`（生成按 BS.1770 等响度匹配的原声与效果版本）。
   - `gui`：在 P4 之前，只输出明确的"未实现"提示和退出码 2，不要假装能用。
   - `job status`，以及 `convert --server`：按 plan 第 10 节的 API 合同实现客户端，用本地 mock 服务器做测试。
   - `batch` 中每个文件的结果各自独立；默认不覆盖已有文件；Ctrl-C 时退出码为 130，并清理临时文件。
   - stdout 只输出 JSON，进度信息写到 stderr。
5. **P0 后续事项**
   - F-AG-1：启动时检测 ffmpeg 版本。版本低于 7 时，处理 M4A 输入或输出要给出明确的错误或降级提示（并定义对应的退出码）；Dockerfile 改为使用 ffmpeg 7 以上（例如 Debian trixie 的基础镜像，版本要固定）。
   - F-AG-2：M4A 回读测试要感知 ffmpeg 版本，版本过低时，断言程序给出了 `sync_warning`。
6. **测试**：单元测试加端到端测试，覆盖下列情况：
   - 单声道与立体声；44.1、48、96 kHz
   - 静音、10 ms 极短片段、损坏文件、非 ASCII 路径、带空格的路径
   - 批处理中部分文件失败
   - 延时补偿与两种尾巴策略
   - 每个退出码

## 交付（`qa/p2/antigravity/`）

- `REPORT.md`，包括：实际运行过的命令及退出码、内存与速度实测（含 60 分钟文件）、失败项与局限、对 P3 的建议。
- `uv run pytest`、`uv run ruff check`、`uv run mypy` 全部通过。

## Git 规则

只在 `agent/antigravity/P2-01` 上提交，提交信息末尾加 `Agent: Antigravity`。不要推送，不要修改 main，不要删除不属于你负责的文件。
