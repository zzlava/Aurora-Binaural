# Aurora Binaural — 构建与部署

本文档说明 Aurora Binaural HTTP API 与持久化 Worker 的环境变量配置及容器部署方式（对应 `docs/plan.md` 第 10、11 节）。

完整用户手册与备份／升级流程见 [docs/user/deploy.md](../docs/user/deploy.md)。以下 Compose 命令显式加载 `deploy/.env`；当前 Compose 要求非空 API Key，API／Worker 固定使用 Rust 核心。

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
   docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
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


## P4 Web GUI

- 在仓库根目录运行 `npm --prefix web ci`、`npm --prefix web run build`，再运行 `uv run aurora gui`。
- 本地启动器自动启动 Worker，随机选择空闲端口，固定绑定 `127.0.0.1`，打开浏览器；`Ctrl-C` 一并停止。`--no-browser`、`--port 8765` 可用于自动化。
- `AURORA_WEB_DIR` 可指定已构建资源；源码默认读 `web/dist`。GUI 默认运行数据在 `~/Library/Caches/AuroraBinaural`，可用 `AURORA_DATA_DIR` 覆盖。
- 本地启动生成一次性会话凭据，仅通过同源、回环、Host 校验后的 `/gui/session` 取回；不把凭据放进打开的 URL。
- Docker 先用 Node 构建，再把静态资源复制进 Python 镜像。`docker compose --env-file deploy/.env -f deploy/compose.yaml up --build` 后打开映射端口，切换远程模式，填服务地址和部署 Key（Key 仅在浏览器 sessionStorage 中保留）。
- 从另一个本地 GUI 连接远程 API 时，给远程 API 配置 `AURORA_CORS_ORIGINS` 为该 GUI 的准确 origin（例如 `http://127.0.0.1:8765`）；固定本地端口可避免每次变更。默认不开放跨域。远程经 HTTPS 反向代理访问；所有静态资源本地打包，不使用 CDN。
- 浏览器刷新或切换连接会清空页面中的素材 / 任务列表；服务器队列仍持久保存，可用 `aurora job status` 查询原 job ID。
- 默认使用 Rust 核心。只有显式设置 `AURORA_ENGINE=stub` 才使用直通桩，界面和报告标注“桩”，未应用 HRTF。

## P5 本机 Rust 核心构建

需要 uv、Rust/Cargo、Python 3.12+ 和 FFmpeg。仓库根目录运行：

```bash
bash scripts/build-core.sh
uv run python -c 'import aurora_core; print(aurora_core.__file__)'
AURORA_ENGINE=rust uv run aurora convert data/assets/synth_voice_mono.wav \
  --preset room --output /tmp/aurora-route-b.wav --report /tmp/aurora-route-b.json
```

`core-build` 是 uv 可选依赖组；构建脚本复制本地 `core/` 到临时目录，补充独立的 maturin 元数据，使用 `--release --locked` 构建并安装 `aurora_core` wheel。不会修改原始 `core/`。应用仍使用 hatchling 构建。`uv sync` 的精确同步可能移除未纳入应用依赖的本地扩展，之后重新执行构建脚本即可。

`AURORA_ENGINE=rust|stub` 默认 `rust`；缺失扩展会明确失败并显示安装提示。路线 V 核心尚未合并时默认 V2 会报 `CORE_ROUTE_UNSUPPORTED`，可显式选择 `--preset room` 验证经典路线 B，绝不自动代换预设。

Docker 增加独立 Rust wheel 构建阶段，最终镜像只安装 wheel、Python 运行依赖与 FFmpeg，不包含 Rust 工具链。

```bash
docker build -f deploy/Dockerfile -t aurora-binaural:p5 .
docker run --rm aurora-binaural:p5 presets --json
```

预设列表由 `presets/*.toml` 读取，默认 V2，界面列出 V1/V2/V3 与经典 room。`AURORA_PRESETS_DIR` 可指定外部目录，数据更新后重启服务。旧 surround/orbit 仅为既有任务保留显式调用兼容。当前 TOML 缺少 route/default 字段，应用依据共享 `[layout]` 判断路线 V，集中设置默认 V2，不维护第二份预设清单。

## HRTF 打包边界

默认 wheel、sdist 和 Docker 仅携带 SADIE II D1 SOFA 与 `sadie_d1_frontal.ahrp`，附 Apache-2.0 全文及署名；Rust 核心验证 pack 内源 SOFA 哈希。应用无 KU100 选择控件。只读 TOML 中的 `thk_ku100` 均衡数据为“仅本地研究”，TH Köln KU100 的 SOFA／pack 不进入默认产物。wheel 内携带预设和 HRTF，离开仓库运行无需依赖当前工作目录；可以用 `AURORA_HRTF_DIR` 明确指定 SADIE 资源目录。

### P5 验证命令

```bash
uv run pytest                         # 桩回归 + 有原生扩展时的 Rust 契约
uv run ruff check src tests/app
uv run mypy
npm --prefix web ci
npm --prefix web run build
npm --prefix web run lint
npm --prefix web run test:e2e         # 显式 AURORA_ENGINE=stub 的流程回归
uv build                             # wheel + sdist；仅 SADIE II 数据
```

真实核心测试在 `tests/app/test_aurora_p5_rust_contract.py`；没有安装 `aurora_core` 时明确 skip。测试 fixture 将旧回归流程显式设为桩，原生测试直接创建适配器或配置 `engine="rust"`，以验证真实执行。当前主线 B 核心电子乐实测真峰值为 −0.6 dBTP；该结果只用于接入验证，不代表路线 V 的 −1 dBTP 限制器验收已完成。
