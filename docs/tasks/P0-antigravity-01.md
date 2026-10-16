# P0-antigravity-01：应用层骨架与编解码时间线验证

状态：已派发（2026-10-16，用户批准）
派单方：Claude　执行方：Antigravity（`agy`）
依据：docs/plan.md 第 8、9、10、19.2 节

## 你的身份与工作环境

- 你负责 Aurora Binaural 的应用层：CLI、API、Worker、GUI、部署。Claude 负责方案、协调、验收和 Git；Cursor 负责 DSP 核心。
- 工作目录：你当前所在目录就是项目的独立克隆 `.agents/antigravity/`，分支为 `agent/antigravity/P0-01`。
- 只修改 `cli/`、`api/`、`worker/`、`web/`、`deploy/`、`qa/p0/antigravity/`，以及根目录的 `pyproject.toml` 和 `uv.lock`（如需新建）。不要修改 `core/`、`prototypes/`，也不要修改克隆外的任何文件。
- 先完整阅读 `docs/plan.md`，尤其是第 8–11 节。

## P0 范围（只做准备工作，不实现完整功能）

1. **项目骨架**
   - 建立 Python 包 `aurora_binaural`（根目录 `pyproject.toml`，用 uv 管理环境，Python ≥ 3.12）
   - 配置 Ruff、mypy、pytest
   - CLI 命令名为 `aurora`
2. **统一参数模型**
   - 用 Pydantic v2 实现 plan 第 9、10 节的参数模型（preset、strength、seed、output、timing）
   - 导出 JSON Schema 到 `qa/p0/antigravity/params.schema.json`
3. **桩核心（只做直通）的完整链路**
   - 打通 `aurora convert` → ffmpeg 解码为 float32 → 桩核心 → 编码 → 回读解码 → 报告 JSON
   - 原子写出结果；默认不覆盖已有文件
4. **编解码时间线验证**
   - 对 WAV、M4A（AAC）、MP3，在 44.1 kHz 和 48 kHz 输入下，测量回读后的样本数与输入是否一致
   - 测量编码器的 priming 与 padding，写出补偿策略
   - 结果写入 `qa/p0/antigravity/timeline.json`
5. **退出码与输出**
   - 按 plan 第 9 节实现退出码
   - stdout 只输出 JSON，日志写到 stderr
6. **Linux 路线**
   - 写一份 `deploy/Dockerfile` 草案（基础镜像固定版本，安装 ffmpeg）
   - 本机如果有 Docker，就实际构建并在容器内运行一次 `aurora inspect`；没有 Docker 就在报告里说明，不要假装验证通过

## 交付（全部放在 `qa/p0/antigravity/` 下）

- `REPORT.md`，内容包括：
  - 实际运行过的命令及其退出码
  - 测得的编码延时
  - 失败项与局限
  - 对 P2／P3 的修改建议
- `params.schema.json`、`timeline.json`
- 测试命令可以一键运行：`uv run pytest`、`uv run ruff check`、`uv run mypy`

## Git 规则

- 只在当前分支 `agent/antigravity/P0-01` 上提交。提交信息格式为 `<范围>: <摘要>`，末尾加一行 `Agent: Antigravity`。
- **不要推送，不要切换分支，不要修改 main，不要改写历史。**
- 不要删除或覆盖已有文件。测试音频只在临时目录里生成，不要提交到仓库。

## 注意

- 不要实现 DSP，不要修改 `core/`。
- 只有实际运行过的结果才算验证。不能完成的事项写进报告。
