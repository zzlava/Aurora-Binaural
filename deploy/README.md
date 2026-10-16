# Aurora Binaural — 部署与环境变量配置说明（P3）

本文档说明 Aurora Binaural HTTP API 与持久化 Worker 的环境变量配置及容器部署方式（对应 `docs/plan.md` 第 10、11 节）。

## 1. 环境变量配置表

所有运行参数均通过环境变量配置（参见 `deploy/.env.example` 与 `src/aurora_binaural/config.py`）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AURORA_API_KEY` | 无（必填） | `/v1/*` 接口的 Bearer API Key 鉴权密钥；未配置或密钥不匹配时返回 `401 UNAUTHORIZED` |
| `AURORA_HOST` | `127.0.0.1` | HTTP 服务默认监听地址（仅本地回环，远程部署由反向代理提供 HTTPS） |
| `AURORA_PORT` | `8000` | HTTP 服务监听端口 |
| `AURORA_DATA_DIR` | `./var/aurora`（容器内 `/data/runtime`） | 素材、结果、临时尝试目录与 SQLite 数据库的持久化根目录 |
| `AURORA_DB_PATH` | `{AURORA_DATA_DIR}/aurora.db` | SQLite 任务数据库文件路径（API 与 Worker 共享） |
| `AURORA_HRTF_DIR` | `./data/hrtf`（容器内 `/app/data/hrtf`） | 固定版本 `.sofa` HRTF 数据目录（供 `/ready` 探针与渲染器读取） |
| `AURORA_MAX_UPLOAD_BYTES` | `524288000`（500 MiB） | `POST /v1/assets` 流式上传的最大字节数限制；超出时立即中断并返回 `413 ASSET_TOO_LARGE` |
| `AURORA_MAX_DURATION_SEC` | `7200.0`（2 小时） | 上传素材允许的最大音频时长（秒）；超出时返回 `422 DURATION_LIMIT_EXCEEDED` |
| `AURORA_JOB_TIMEOUT_SEC` | `1800.0`（30 分钟） | 单个任务单次尝试的最大处理超时时间（秒）；超出时在块边界中止并标记 `JOB_TIMEOUT_EXCEEDED` |
| `AURORA_RETENTION_SEC` | `86400.0`（24 小时） | 素材与结果文件的保留期限（秒）；过期后由 Worker 定时清理（不会删除正在排队/运行或下载中的文件），下载已过期结果返回 `410 RESULT_EXPIRED` |
| `AURORA_WORKER_CONCURRENCY` | `1` | 单个 Worker 进程的并发处理槽位数 |
| `AURORA_LEASE_DURATION_SEC` | `30.0` | Worker 领取任务的租约有效期（秒） |
| `AURORA_HEARTBEAT_INTERVAL_SEC` | `5.0` | Worker 续租与进程心跳更新间隔（秒） |
| `AURORA_CLEANUP_INTERVAL_SEC` | `60.0` | 过期素材与结果文件的定时清理间隔（秒） |
| `AURORA_WORKER_POLL_INTERVAL_SEC` | `0.15` | Worker 轮询 SQLite 队列的时间间隔（秒） |
| `AURORA_MAX_RETRIES` | `2` | 租约过期或可重试故障的最大重试次数（默认最多重试 2 次） |
| `AURORA_STRICT_FFMPEG` | `0` | 设为 `1` 时，若系统 FFmpeg 版本低于 7.0 则拒绝处理 M4A/AAC |

## 2. Docker Compose 启动与验证

1. 准备环境变量文件：
   ```bash
   cp deploy/.env.example deploy/.env
   # 编辑 deploy/.env 并设置随机生成的 AURORA_API_KEY
   ```
2. 构建并启动 `api` 与 `worker` 服务：
   ```bash
   docker compose -f deploy/compose.yaml up -d --build
   ```
3. 检查健康与就绪状态：
   ```bash
   curl -s http://127.0.0.1:8000/health
   curl -s http://127.0.0.1:8000/ready
   ```
4. 使用 CLI 远程模式执行端到端转换：
   ```bash
   export AURORA_API_KEY="<your-api-key>"
   uv run aurora convert data/assets/synth_voice_mono.wav \
     --server http://127.0.0.1:8000 \
     --preset room \
     --strength 0.4 \
     --output /tmp/remote_out.wav
   ```
