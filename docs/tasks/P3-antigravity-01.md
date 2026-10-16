# P3-antigravity-01：HTTP API、持久化任务队列与 Worker、容器部署

状态：已派发（2026-10-17）
执行方：Antigravity（`agy`）
依据：docs/plan.md 第 10、11、16.4 节；`qa/p2/ACCEPTANCE-antigravity-p2-01.md`

## 工作环境

- 独立克隆 `.agents/antigravity/`，分支 `agent/antigravity/P3-01`（基于最新 main）。
- 可以修改：`src/`、`tests/app/`、`deploy/`、`qa/p3/antigravity/`、`pyproject.toml`、`uv.lock`。不要修改 `core/`、`prototypes/`、`data/`、`docs/`、`tools/`。
- 核心仍然通过 `aurora_binaural.engine.Renderer` 协议使用桩实现；Rust 核心由 Cursor 并行开发，完成后会替换进来。

## 任务

1. **API**（FastAPI，严格按 plan 10 节的接口表实现）
   - 接口：`/health`、`/ready`、`/v1/presets`、`/v1/assets`（流式上传，有大小限制）、`/v1/jobs`（返回 202；支持 Idempotency-Key：同一个 key 加不同参数返回 409）、`/v1/jobs/{id}`、`/v1/jobs/{id}/cancel`（幂等）、`/v1/jobs/{id}/result`、`/v1/jobs/{id}/report`。
   - 导出 OpenAPI 文档到 `qa/p3/antigravity/openapi.json`。
   - 用 Bearer API Key 鉴权，Key 从环境变量读取。`/health` 不暴露任何内部路径。
2. **SQLite 任务存储与 Worker**（plan 11 节）
   - Worker 用短事务领取任务，带租约与心跳；启动时回收租约过期的任务；最多重试 2 次。
   - 每次尝试使用独立的临时目录，结果原子移动到最终位置；只有编码、回读、报告都完成后才标记为成功。
   - 取消：Worker 在块边界检查取消标志，结束处理并清理临时文件。
   - 定时清理过期的素材和结果，但不能删除正在使用的文件；下载已过期的结果时返回明确的错误。
3. **配置**：上传大小、时长、超时、保留期限、并发数、监听地址（默认 127.0.0.1）都用环境变量配置，写成文档；提供 `deploy/.env.example`，里面不放真实密钥。
4. **部署**
   - `deploy/compose.yaml`：api 和 worker 两个服务共用持久卷。镜像基于 P2 的 trixie 镜像。
   - 本机有 OrbStack 就实际构建，并完成一次端到端：上传 → 排队 → 处理 → 下载。
5. **CLI 远程模式**：P2 的 `RemoteAuroraClient` 要对接真实 API 测试通过，测试中要包含 `convert --server`。
6. **故障与恢复测试**（plan 16.4 节）
   - 无效 Key；同一个 key 加不同参数（409）；重复取消；终态任务取消后不改变结果。
   - Worker 被 kill 后，租约过期回收并重试成功。
   - API 重启后，任务状态仍然一致。
   - 输出写入失败（磁盘满或无权限的模拟）时，任务标记为失败，并且没有残留的半成品文件。
   - 下载过期结果时返回错误。
   - 重试不会产生两个成功结果。

## 交付（`qa/p3/antigravity/`）

- `REPORT.md`：实际运行过的命令及退出码、端到端与故障测试结果、容器验证情况、已知局限。
- `openapi.json`
- `uv run pytest`、`uv run ruff check`、`uv run mypy` 全部通过。

## Git 规则

只在 `agent/antigravity/P3-01` 上提交，提交信息末尾加 `Agent: Antigravity`。不要推送，不要修改 main。
