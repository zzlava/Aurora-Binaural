# Docker Compose 部署与维护

从仓库根目录执行。需要可用的 Docker Engine／Compose；macOS 可使用本机 OrbStack 的 Docker context。API 与一个 Worker 共用本机 Docker volume，不支持跨节点共享 SQLite。生产访问通过 HTTPS 反向代理。

## 构建与启动

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
# 编辑 deploy/.env，设置随机 AURORA_API_KEY；不要使用模板值。
# 可用 python3 -c 'import secrets; print(secrets.token_hex(32))' 生成。
docker compose --env-file deploy/.env -f deploy/compose.yaml build
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait
curl --fail-with-body -sS http://127.0.0.1:8000/health
curl --fail-with-body -sS http://127.0.0.1:8000/ready
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
```

必须显式给出 `--env-file deploy/.env`，不能假设 Compose 自动加载该路径。镜像分三个阶段：Node 构建静态 GUI、Rust/maturin 编译 release 原生 wheel、Python/FFmpeg 运行镜像。最终镜像包含 `aurora_core`、SADIE II D1 SOFA/HRIR pack 和许可证，运行时无需下载 HRTF，也没有 Rust 工具链。

Compose 固定 `AURORA_ENGINE=rust`，API/Worker 共享同一镜像，默认路线 V/V2 与 SADIE II D1。验证时从宿主机运行 [Python 客户端](../../examples/python_client.py)，检查 report.engine.is_stub=false、engine.route=V、params.preset.id=V2。`room` 为显式选择的经典路线 B。`up --wait` 的 healthcheck 只检查 API 存活，仍需检查 `/ready` 并实际提交任务。

## 环境变量

| 变量 | 应用默认 | 作用 |
|---|---|---|
| AURORA_API_KEY | 无 | Bearer Key；Compose 要求非空，模板值需自行替换 |
| AURORA_HOST / AURORA_PORT | 127.0.0.1 / 8000 | Compose 中为宿主机绑定地址/端口；容器内部固定 0.0.0.0:8000 |
| AURORA_IMAGE | aurora-binaural:p5 | Compose 镜像名/标签 |
| AURORA_ENGINE | rust | 本机可设 stub；此 Compose 固定 rust |
| AURORA_DATA_DIR | ./var/aurora | 持久目录；Compose 固定 /data/runtime |
| AURORA_DB_PATH | DATA_DIR/aurora.db | SQLite；Compose 固定 /data/runtime/aurora.db |
| AURORA_HRTF_DIR | 仓库或安装包资源 | SOFA/pack 目录；Compose 固定 /app/data/hrtf |
| AURORA_PRESETS_DIR | 仓库或安装包资源 | 本机预设覆盖；镜像固定 /app/presets |
| AURORA_WEB_DIR | 仓库 web/dist | 静态 GUI；镜像 /app/web/dist |
| AURORA_CORS_ORIGINS | 空 | 逗号分隔的准确浏览器 origin，默认仅同源 |
| AURORA_MAX_UPLOAD_BYTES | 524288000 | 上传最大字节数，500 MiB |
| AURORA_MAX_DURATION_SEC | 7200 | 素材最长秒数 |
| AURORA_JOB_TIMEOUT_SEC | 1800 | 每次尝试超时秒数 |
| AURORA_RETENTION_SEC | 86400 | 素材／结果 TTL 秒数 |
| AURORA_WORKER_CONCURRENCY | 1 | 每个 Worker 的并发槽位 |
| AURORA_LEASE_DURATION_SEC | 30 | 任务租约秒数 |
| AURORA_HEARTBEAT_INTERVAL_SEC | 5 | 心跳／续租间隔，必须小于租约并留余量 |
| AURORA_CLEANUP_INTERVAL_SEC | 60 | 文件清理周期秒数 |
| AURORA_WORKER_POLL_INTERVAL_SEC | 0.15 | Worker 队列轮询间隔 |
| AURORA_MAX_RETRIES | 2 | 首次尝试之外的最多重试次数 |
| AURORA_STRICT_FFMPEG | 0 | 1 时 FFmpeg <7 拒绝 M4A/AAC |

`AURORA_WORKER_BLOCK_DELAY_MS=0` 是本机取消／恢复测试的调试延时，Compose 不转发。`AURORA_CONFIG_PATH` 仅为 CLI 远程 Key 的 JSON 配置路径。容器路径变量按固定挂载设计，不会因修改宿主机 `.env` 同名路径而自动重挂载。

## 数据目录、日志与备份

volume `aurora_runtime` 挂载 `/data/runtime`，实际名称带 Compose 项目前缀。包含 aurora.db（及 SQLite WAL/SHM）、assets/、results/、tmp/。文件以 UID/GID 10001 写入；换用 bind mount 时要保证权限。不要删除正在运行的临时目录。

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail 100 api worker
docker compose --env-file deploy/.env -f deploy/compose.yaml logs -f worker
```

备份必须同时覆盖数据库和媒体。先暂停入口流量并停止服务，避免只复制 `.db` 丢失 WAL 或出现文件与任务不一致；以下脚本备份整个 `/data/runtime`，使用新备份目录避免覆盖：

```bash
backup_dir="$(pwd)/outputs/backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
docker compose --env-file deploy/.env -f deploy/compose.yaml stop api worker
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  -v "$backup_dir:/backup" --entrypoint python api -c \
  'import tarfile; t=tarfile.open("/backup/runtime.tar.gz","w:gz"); t.add("/data/runtime",arcname="runtime"); t.close()'
docker compose --env-file deploy/.env -f deploy/compose.yaml start api worker
```

另存 `.env`（含 Key，安全保存）、镜像 ID/标签和本次源码提交。恢复时先停止服务，在新的空 volume 中解包到 `/data/runtime`，确保 UID/GID=10001，使用备份对应镜像启动。不要覆盖运行中的数据；TTL 到期后恢复的文件仍可能被清理。

## 升级、重启与停止

升级前完成上述备份，记录 `docker compose … images`，将 `AURORA_IMAGE` 换成新的明确标签并构建，再 `up -d --wait`。检查 `/ready`，上传一个短 WAV 实际转换，确认版本、摘要、样本数。失败时恢复旧镜像；若数据库结构已变更，则同时使用升级前的数据备份。

Worker 在启动和运行时回收过期租约，重试使用独立尝试目录；仅在编码、回读、报告和原子交付完成后才标 succeeded。重启后可能需等待旧租约过期，状态与进度通过 job ID 查询。不要用无限重启掩盖失败原因。

`docker compose --env-file deploy/.env -f deploy/compose.yaml down` 停止服务并保留 volume。`down -v` 会删除持久数据，不用于常规停止或升级。

本地 GUI 连接远程服务时，在服务器配置准确的 GUI origin（例如 http://127.0.0.1:8765），并用 `aurora gui --port 8765` 固定端口。直接打开容器 GUI 时选择远程连接模式并填写服务地址与 Key。
