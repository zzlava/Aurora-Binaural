# P3-antigravity-01 交付报告：HTTP API、持久化任务队列与 Worker、容器部署

- **任务编号**：`P3-antigravity-01`
- **执行方**：Antigravity (`agy`)
- **工作分支**：`agent/antigravity/P3-01`
- **日期**：2026-10-17

---

## 1. 改动摘要与文件列表

本次交付严格在允许修改的目录（`src/`、`tests/app/`、`deploy/`、`qa/p3/antigravity/`、`pyproject.toml`、`uv.lock`）内完成，未触碰 `core/`、`prototypes/`、`data/`、`docs/`、`tools/`：

1. **环境变量配置模块（`src/aurora_binaural/config.py`、`deploy/.env.example`、`deploy/README.md`）**
   - 实现 `ServiceConfig.from_env()`，支持通过环境变量配置：
     - `AURORA_API_KEY`（Bearer API Key 鉴权密钥）
     - `AURORA_HOST`（默认 `127.0.0.1`）、`AURORA_PORT`（默认 `8000`）
     - `AURORA_DATA_DIR`、`AURORA_DB_PATH`、`AURORA_HRTF_DIR`
     - `AURORA_MAX_UPLOAD_BYTES`（默认 500 MiB）、`AURORA_MAX_DURATION_SEC`（默认 7200 秒）
     - `AURORA_JOB_TIMEOUT_SEC`（默认 1800 秒）、`AURORA_RETENTION_SEC`（默认 86400 秒）
     - `AURORA_WORKER_CONCURRENCY`（默认 1）、`AURORA_LEASE_DURATION_SEC`（默认 30 秒）、`AURORA_HEARTBEAT_INTERVAL_SEC`（默认 5 秒）、`AURORA_CLEANUP_INTERVAL_SEC`（默认 60 秒）、`AURORA_MAX_RETRIES`（默认 2 次）
   - 提供不含真实密钥的 `deploy/.env.example` 与配置部署说明 `deploy/README.md`。

2. **SQLite 持久化存储层（`src/aurora_binaural/storage.py`）**
   - 启用 SQLite WAL 模式，所有状态变更均通过 `BEGIN IMMEDIATE` 短事务执行。
   - 实现 `assets`、`jobs`、`worker_heartbeats` 三张表。
   - **幂等键（`Idempotency-Key`）**：对规范化后的 `AuroraParams` 计算 SHA-256 请求摘要；相同 Key + 相同参数返回原任务（HTTP 202），相同 Key + 不同参数返回 `409 Conflict`（`IDEMPOTENCY_KEY_CONFLICT`）。
   - **租约与围栏令牌（`lease_id`）**：每次领取任务生成唯一 `lease_id` 并递增 `attempts`；最终提交结果时在 `BEGIN IMMEDIATE` 事务内校验 `status == 'running' AND lease_id == ? AND cancel_requested == 0`，保证旧尝试（Stale Worker）绝不可能覆盖新尝试或产生两个成功结果。
   - **租约回收与重试上限**：启动及周期性调用 `recover_expired_leases()`，回收租约过期的 `running` 任务；当 `attempts <= max_retries`（默认 `max_retries = 2`，即最多重试 2 次）时退回 `queued`，超出上限时标记为 `failed`（`LEASE_EXPIRED_MAX_RETRIES`）。
   - **安全过期清理**：`cleanup_expired_resources()` 清理已过期的结果文件与素材文件，通过 SQL `NOT EXISTS (... status IN ('queued', 'running'))` 与活动下载引用计数保护正在排队、运行或下载中的文件不被误删；下载已过期结果时返回明确的 `410 Gone`（`RESULT_EXPIRED`）。

3. **后台 Worker（`src/aurora_binaural/worker/__init__.py`、`src/aurora_binaural/worker/__main__.py`）**
   - 启动时执行过期租约回收与过期资源清理，并维护 `worker_heartbeats` 进程心跳与任务续租线程。
   - 每次尝试使用独立的临时目录 `{tmp_dir}/attempt_{job_id}_{attempts}_{lease_id}/`；仅在流式编码、回读校验、报告生成全部完成后，才在最终短事务内通过 `os.replace` 原子移动到 `{results_dir}` 并标记 `succeeded`。
   - 若写入或原子移动失败（如磁盘满 `ENOSPC` 或无权限），立即回滚事务、清理半成品文件并将任务标记为 `failed`（`OUTPUT_WRITE_FAILED`）。
   - 在每个音频块边界通过 `cancel_check` 检查取消标志、租约状态与超时；收到取消时立即中止 FFmpeg 子进程、删除尝试临时目录并标记 `canceled`。

4. **HTTP API 服务（`src/aurora_binaural/api/__init__.py`、`src/aurora_binaural/api/__main__.py`）**
   - 严格实现 `docs/plan.md` 第 10 节的全部 9 个接口：
     - `GET /health`：存活检查，无需鉴权，不暴露任何内部路径。
     - `GET /ready`：检查 SQLite 存储、活跃 Worker 心跳与固定版本 HRTF `.sofa` 数据是否就绪（不暴露内部路径）。
     - `GET /v1/presets`：返回预设列表、版本与参数范围。
     - `POST /v1/assets`：支持流式二进制（`application/octet-stream`）与 `multipart/form-data` 上传，流式强制校验 `AURORA_MAX_UPLOAD_BYTES`（超限返回 `413 ASSET_TOO_LARGE`）与 `AURORA_MAX_DURATION_SEC`（超限返回 `422 DURATION_LIMIT_EXCEEDED`）。
     - `POST /v1/jobs`：创建异步任务（返回 `202 Accepted`），支持 `Idempotency-Key`（冲突返回 `409 IDEMPOTENCY_KEY_CONFLICT`）。
     - `GET /v1/jobs/{id}`：查询状态（`queued`/`running`/`succeeded`/`failed`/`canceled`，取消中过渡状态通过 `canceling: true` / `cancel_requested: true` 子字段表示）、进度、结果元数据与错误详情。
     - `POST /v1/jobs/{id}/cancel`：幂等取消；终态任务调用取消不改变结果。
     - `GET /v1/jobs/{id}/result`：流式下载输出音频；过期返回 `410 RESULT_EXPIRED`。
     - `GET /v1/jobs/{id}/report`：下载 JSON 转换报告（已脱敏内部路径为 `asset://...` 与 `result://...`）；过期返回 `410 RESULT_EXPIRED`。
   - 导出完整 OpenAPI 3.1 规范至 `qa/p3/antigravity/openapi.json`。

5. **CLI 扩展与远程客户端增强（`src/aurora_binaural/cli/__init__.py`、`src/aurora_binaural/cli/remote.py`）**
   - 新增 `aurora api`、`aurora worker`、`aurora openapi` 子命令。
   - 增强 `RemoteAuroraClient` 在上传与下载失败时对服务端结构化错误码（如 `UNAUTHORIZED`、`ASSET_TOO_LARGE`、`RESULT_EXPIRED`）的透传解析。

6. **容器部署（`deploy/Dockerfile`、`deploy/compose.yaml`）**
   - 基于 P2 锁定 SHA-256 的 `python:3.12.15-slim-trixie`（内置 `ffmpeg 7.1.5-0+deb13u1`），打包固定版本 HRTF 数据集（`data/hrtf/`）。
   - `deploy/compose.yaml` 配置 `api` 与 `worker` 两个服务共享命名持久卷 `aurora_runtime:/data/runtime`。

---

## 2. 实际运行过的命令及退出码

| 序号 | 命令 | 退出码 | 结果摘要 |
|---|---|---:|---|
| 1 | `uv run aurora openapi --output qa/p3/antigravity/openapi.json` | `0` | 成功生成并导出 OpenAPI 3.1 文档（包含全部 9 个路由及 Schema） |
| 2 | `uv run pytest -v` | `0` | **45 passed in 10.36s**（P0 16 项 + P2 17 项 + P3 12 项全部通过） |
| 3 | `uv run ruff check` | `0` | `All checks passed!` |
| 4 | `uv run mypy` | `0` | `Success: no issues found in 20 source files` |
| 5 | `docker compose -f deploy/compose.yaml build` | `0` | 基于 OrbStack 成功构建 `aurora-binaural:p3-trixie` 镜像 |
| 6 | `docker compose -f deploy/compose.yaml up -d` + `/health` + `/ready` + `aurora convert --server` + `down -v` | `0` | 容器端到端上传 → 排队 → Worker 处理 → 下载音频与报告验证通过 |

---

## 3. 端到端与故障恢复测试结果（对应 plan 16.4 节 & 任务 5、6）

在 `tests/app/test_aurora_p3.py` 中覆盖并实测通过以下全部场景：

| 测试项 | 对应测试函数 | 实测结果 |
|---|---|---|
| OpenAPI 导出与路由完整性 | `test_openapi_export_and_artifact_parity` | `qa/p3/antigravity/openapi.json` 与运行时 schema 完全一致，覆盖全部 9 个接口 ✅ |
| `/health` 与 `/ready` 不暴露路径 + Bearer Key 鉴权 | `test_health_and_ready_no_internal_paths_and_auth_enforcement` | `/health` 返回 200 且不含任何路径；无 Key 或错误 Key 访问 `/v1/*` 稳定返回 `401 UNAUTHORIZED` (`retryable=false`)；Worker 心跳注册后 `/ready` 从 `503` 变为 `200` ✅ |
| 流式上传大小限制、时长限制、SHA-256 校验 | `test_asset_upload_size_limit_duration_limit_and_checksum` | 超出 `AURORA_MAX_UPLOAD_BYTES` 返回 `413 ASSET_TOO_LARGE`；超出 `AURORA_MAX_DURATION_SEC` 返回 `422 DURATION_LIMIT_EXCEEDED`；临时文件全部清理无残留 ✅ |
| `Idempotency-Key` 幂等与参数冲突 | `test_idempotency_key_same_vs_different_params_409` | 相同 Key + 相同参数返回 `202` 原 `job_id`；相同 Key + 不同参数返回 `409 IDEMPOTENCY_KEY_CONFLICT` ✅ |
| 取消任务（排队中、运行中块边界、重复取消、终态不变性） | `test_cancel_queued_running_duplicate_and_terminal_state_invariance` | 排队任务立即变更为 `canceled`；运行中任务先呈现 `status="running", canceling=True`，在下一音频块边界停止、清理临时目录并变更为 `canceled`；重复取消返回 200；已完成（`succeeded`）任务调用取消后状态与结果文件保持不变 ✅ |
| Worker 被 `SIGKILL` 后租约过期回收并重试成功 | `test_worker_sigkill_lease_expiry_recovery_and_retry_success` | 启动真实子进程 Worker 领取任务（`attempts=1`）后发送 `SIGKILL` 强杀；等待 0.5s 租约过期后由新 Worker 回收并重试成功（`status="succeeded", attempts=2`） ✅ |
| 最大重试次数（最多重试 2 次）上限 | `test_max_retries_exhaustion_marks_job_failed` | 初始尝试（Attempt 1）+ 2 次重试（Attempt 2、3）均租约过期后，任务标记为 `failed`（`LEASE_EXPIRED_MAX_RETRIES`, `retryable=false`） ✅ |
| API 重启后任务状态一致性 | `test_api_restart_preserves_job_state_and_artifacts` | 销毁原 FastAPI 实例并基于同一 SQLite 数据库重建新实例后，任务状态、结果文件 SHA-256 与报告完全一致 ✅ |
| 输出写入失败（模拟磁盘满 `ENOSPC`） | `test_output_write_failure_disk_full_marks_failed_and_leaves_no_partial_files` | 模拟向 `results_dir` 原子提交时抛出 `ENOSPC`，任务标记为 `failed` (`OUTPUT_WRITE_FAILED`)，`results_dir` 与 `tmp_dir` 均无任何半成品文件残留 ✅ |
| 定时清理过期文件（保护正在使用的素材）与过期下载错误 | `test_expiration_cleanup_protects_in_use_assets_and_returns_410_on_expired_download` | 当素材被 `queued`/`running` 任务引用时，即使超过 `expires_at` 也不会被删除；任务完成后过期清理删除文件，下载过期结果或报告返回 `410 RESULT_EXPIRED` ✅ |
| 重试不会产生两个成功结果（围栏令牌验证） | `test_retry_fencing_prevents_two_successful_results` | 当旧尝试（Attempt 1）租约过期且新尝试（Attempt 2）已提交成功后，旧尝试持旧 `lease_id` 提交被 `finalize_job_success` 拒绝，不会覆盖最终结果文件 ✅ |
| CLI 远程模式（`aurora convert --server`、`aurora job`）对接真实 API | `test_cli_convert_server_and_job_commands_end_to_end` | 对接真实 Uvicorn FastAPI + `AuroraWorker` 完成上传、轮询、下载与报告校验，样本数 `24000 == 24000` ✅ |

---

## 4. OrbStack 容器构建与端到端验证情况

在本机 macOS ARM64 + OrbStack (Docker Engine `28.5.2`, Compose `v2.40.3`) 上实际构建并启动 `deploy/compose.yaml`（`api` + `worker` 共用 `aurora_runtime` 持久卷）：

1. **容器内环境核实**
   - 基础镜像：`python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`
   - 容器内 FFmpeg 版本：`ffmpeg version 7.1.5-0+deb13u1`
   - `GET http://127.0.0.1:18080/health`：`{"ok":true,"status":"ok","service":"aurora-binaural-api","api_version":"v1"}`
   - `GET http://127.0.0.1:18080/ready`：`{"ok":true,"status":"ready","checks":{"database":true,"worker":true,"hrtf_data":true}}`
2. **端到端 CLI 远程转换（`aurora convert --server`）**
   - 输入：`data/assets/synth_voice_mono.wav`（48 kHz, 单声道, `288,000` 样本, 6.0 秒）
   - 流程：通过 `POST /v1/assets` 流式上传 → `POST /v1/jobs` 入队 → `worker` 容器从共享 SQLite 领取任务并处理 → `GET /v1/jobs/{id}/result` 与 `/report` 下载到本地。
   - WAV 输出结果：`288,000` 样本，立体声 24-bit，`sample_diff = 0`，`sync_within_tolerance = true`。
   - M4A 输出结果：容器内编码为 AAC-LC M4A（带 `elst` 编辑列表与 `stts` 持续时间），下载后本机回读解码样本数为 `288,000`（与输入严格一致）。

---

## 5. 已知局限

1. **核心仍为桩实现（`StubRenderer`）**：按任务书要求，当前核心通过 `aurora_binaural.engine.Renderer` 协议调用 numpy 直通桩实现；待 Cursor 完成 P1 Rust 核心与 Python 绑定后无缝替换。
2. **M4A 尾包裁剪在不同 FFmpeg 7.x / 8.x 解码器下的差异**：在 Debian Trixie（`ffmpeg 7.1.5`）容器内，`aac` 编码器已正确写入 MP4 `elst`（首部 1024 priming）与 `stts`（尾部精确时长），使用 FFmpeg 8.0+ 解码时首尾裁剪均为 0 偏差（`288,000` 样本）；而 FFmpeg 7.1.5 自带的 `mov` 解码器在回读非 1024 整数倍帧长的 M4A 文件时仅裁剪首部 1024 priming，未裁剪最后一个 AAC 帧的尾部填充（报告中已按方案第 8 节如实标记 `sync_within_tolerance: false` 并给出改用 WAV 48 kHz / 24-bit 的同步提示，绝不谎报同步通过）。
3. **Web GUI 尚未启用**：`aurora gui` 仍按 P2 规范返回退出码 `2`，将在 P4 阶段实现并挂载到 FastAPI。
