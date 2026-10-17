# P5-codex-02 执行报告

日期：2026-10-17（Asia/Shanghai）。分支：`agent/codex/P5-02`，基线：`b716a6a`。仅在该分支提交，未推送；本次执行少于 120 分钟。

**四项应用层交付已完成：用户文档、可运行集成示例、OrbStack Compose 真实 Rust 转换和本报告。** 当前主线只有路线 B，所有真实示例明确选择 `room`。核心质量仍有未通过项：电子乐真峰值 **−0.6 dBTP**，高于 −1 dBTP 目标；合成点击经 Rust B 后的阈值起点提前 **102 样本**。没有修改只读核心或以桩代替真实渲染，没有宣称整套 DSP／听感验收通过。

## 1. 用户文档

新增五份中文手册：

- [quickstart.md](../../../docs/user/quickstart.md)：本机依赖、uv 安装、Rust 构建、GUI、CLI 与远程接入。
- [cli.md](../../../docs/user/cli.md)：12 个子命令、参数、退出码、JSON 字段；区分本地与远程选项。
- [api.md](../../../docs/user/api.md)：全部系统／v1 端点、curl、鉴权、幂等、状态轮询、预览、下载校验、过期和错误码。
- [deploy.md](../../../docs/user/deploy.md)：显式 env-file 的 Compose 部署、完整配置表、持久化目录、停机备份、升级和日志。
- [troubleshooting.md](../../../docs/user/troubleshooting.md)：FFmpeg/M4A 时间线、核心缺失／路线不兼容、GUI 构建、端口、鉴权、恢复与峰值问题。

核对实际参数解析器、Pydantic 模型、API 路由、存储与配置代码；12 个子命令 `--help` 均可运行，文档相对链接全部存在。本地 `npm ci`／构建成功；`aurora gui --port 18766 --no-browser` 实际启动，静态页面、带 `X-Aurora-GUI` 的会话入口、内置 Worker 与 `/ready` 通过，Ctrl-C 退出码 130。此处是启动冒烟检查，未做本轮浏览器视觉验收。

提交：`2736724`（后续验证时补充试听下载 curl）。

## 2. 集成示例与同步测量

### Python 客户端

[examples/python_client.py](../../../examples/python_client.py) 仅依赖 Python 标准库：

- 流式上传，发送并检查 SHA-256 与字节数。
- 在发出任务请求前保存幂等 key，保存 asset_id、请求和 job_id；中断／超时后从 state 恢复。state 不含 API Key。
- 网络错误、429 和可重试 5xx 使用有限次指数退避；每次上传重开文件，任务创建重用同一 key 和正文。
- 有界轮询，识别 failed/canceled；下载到临时文件，核对任务／报告元数据、摘要与大小后原子交付。
- 默认拒绝覆盖和 HTTP 重定向；支持 `--require-rust`；Key 仅从环境读取。

6 项真实 HTTP 测试验证：上传重试、服务已接收任务但响应丢失后的幂等重放、断点恢复复用同一个 job、参数不一致拒绝、损坏下载不替换已有目标、拒绝重定向及非法限额。这里的故障注入服务用于客户端测试；下一节的容器转换调用实际 API、Worker 和 Rust 核心。

### AI 视频示例

[examples/ai_video_pipeline.sh](../../../examples/ai_video_pipeline.sh) 接受视频和 AI 配音／音乐文件，或用 `--demo` 生成可重现的合成替身。脚本不调用 AI 生成服务。实际流程为：

```text
音频 → WAV 48 kHz/24 bit → Rust room（preserve + trim）
     → 复制原视频流 + PCM 音轨到 MOV → 解码最终音视频核验
```

输出目录必须为空；输入视频从 0 开始，音轨须已补齐至相同时长。保留前置静音，不通过截短、变速或 `-shortest` 隐藏误差。中间与最终使用 PCM，避免附加 AAC priming；发布为 AAC/MP4 后还需再次回读。

本机 CLI 与容器 API 两条流程均实际跑通。合成素材为 6 秒、320×180、25 fps，音频在 1/3/5 秒产生 10 ms、1 kHz 点击，最终视频实际解码检测白色闪光帧。

| 检查 | 本机 CLI / 容器 API 实测 |
|---|---|
| 引擎 | aurora_core 0.1.0，is_stub=false，room，strength=0.4，seed=20261016 |
| 输入／渲染／最终音轨样本数 | 均 288,000（48 kHz、6 秒）；差值 0 |
| 原视频／最终视频帧数 | 均 150 |
| 最终视频检出的闪光帧 | 25、75、125 |
| 最终音轨／视频起点时间戳 | 均为 0 |
| 封装前后 PCM 最大绝对误差 | 0，封装没有新增样本偏移 |
| 点击相对输入的起点偏移 | 三次均 −102 样本 = −2.125 ms = −0.053125 视频帧 |
| 点击相对实际闪光的偏移 | 三次均 −101 样本（输入正弦点击在闪光后 1 样本越过检测阈值） |
| 明示的 A/V 事件容差 | 一视频帧 = 1,920 样本；通过帧级检查，未宣称起点达到一采样精度 |
| 合成素材输出真峰值 | −16.4 dBTP |

点击起点采用任一声道超过 −80 dBFS 的首帧定义。滤波会改变波形，以上是可重现的阈值起点测量。应用按核心声明的 1,229 样本延迟补偿；本轮不在应用层加入臆测的固定偏移去修正核心。报告的 `timeline.sample_diff=0` 证明长度一致，不能代替这个事件测量。

证据：[本机同步](p5-02-local-video-sync.json)、[本机渲染报告](p5-02-local-video-report.json)、[容器同步](p5-02-remote-video-sync.json)、[容器视频渲染报告](p5-02-remote-video-report.json)。音视频大文件保留在被忽略的 `.runtime/p5-02-local-av/` 与 `.runtime/p5-02-remote-av/`。

提交：`6e0e144`（后续验证补充最终闪光帧的解码定位与文档）。

## 3. OrbStack 容器端到端

实际 Docker context 为 `orbstack`，Linux ARM64，Docker Engine 28.5.2。多阶段 Dockerfile 保留 Node、Rust wheel、Python/FFmpeg 三阶段；增强最终安装步骤的原生扩展／引擎检查。Compose 将 API 与 Worker 的 `AURORA_ENGINE` 固定为 rust，镜像标签可由 `AURORA_IMAGE` 配置，转发 strict-ffmpeg 配置，并拒绝未设置的 API Key。

本轮运行：

```bash
docker compose --env-file qa/p5/codex/.runtime/p5-02.env \
  -p aurora-p5-02 -f deploy/compose.yaml build
docker compose --env-file qa/p5/codex/.runtime/p5-02.env \
  -p aurora-p5-02 -f deploy/compose.yaml up -d --wait
# 在宿主机配置同一 AURORA_API_KEY 后：
python3 examples/python_client.py data/assets/holdout_electronic.wav \
  --server http://127.0.0.1:18765 \
  --output qa/p5/codex/.runtime/p5-02-container.wav \
  --report qa/p5/codex/p5-02-container-report.json \
  --state qa/p5/codex/.runtime/p5-02-client-state.json --require-rust
```

该私有 env 文件权限为 0600、被 Git 忽略，实际 Key 未进入提交或报告。构建日志位于 `.runtime/p5-02-build.log`。**本轮复用了已存在的 Rust 编译层缓存**，最终应用安装／原生可用性检查重新执行，随后实际启动 Compose 和转换；不将缓存命中描述为本轮重新编译核心。

镜像：`sha256:5a7e9da30d97efc3bbd2bc01bc1d85ee27a9aab5705ee071ba322bbd1f03f569`，787,487,510 字节。API/Worker 均以 UID 10001 运行，FFmpeg **7.1.5**，可导入 `aurora_core`，引擎显示仅 B 可用；最终镜像无 cargo/rustc。镜像没有 `.git`，原始转换报告的 engine.git_commit 为 unknown；以此镜像 ID 和本分支构建内容关联，不伪造报告提交号。

### 宿主机 Python 客户端实测

| 项目 | 结果 |
|---|---|
| 输入 | holdout_electronic.wav，25 秒 |
| 参数 | room v1，strength=0.4，seed=20261016，WAV 48 kHz／24 bit，preserve/trim |
| job ID | job_0a51167b7a3e4e5d |
| 引擎／HRTF | aurora_core 0.1.0、is_stub=false；SADIE II D1，Apache-2.0，SOFA 哈希记录在报告 |
| 输入／期望／默认解码回读 | 均 1,200,000 样本；差值 0 |
| 引擎延迟／声明尾巴 | 1,229 / 21,600 样本；trim 不追加尾巴 |
| 响度／真峰值 | −11.4 LUFS / **−0.6 dBTP** |
| 下载 SHA-256 | 3313299d5619775e869c65aa71859b349e3a8f404b7a67bb309e6b6f1a1222cb；客户端核对通过 |
| 独立宿主机回读 | FFmpeg 9.0.2 再解码得到 1,200,000 样本，ebur128 再测仍为 −0.6 dBTP |

−0.6 dBTP 与前次验收记录一致，**未通过 −1 dBTP 核心质量目标**。本任务不允许修改 `core/`，保留给核心负责人处理。

证据：[构建／运行环境](p5-02-container-build.json)、[探针和预设](p5-02-container-probes.json)、[客户端结果](p5-02-client-result.json)、[原始转换报告](p5-02-container-report.json)、[独立解码](p5-02-host-readback.json)、[独立 ebur128 摘要](p5-02-host-ffmpeg.txt)。提交：`02ae547`。

### 额外实际 API 与维护检查

- 缺 Key 返回 401；同幂等 key／相同正文返回原 job；更改 strength 返回 409；HTTP 本地路径被 422 拒绝；未知 job 返回 404。
- 停止 Worker 后创建 queued 任务，连续取消两次均为 canceled，下载返回 409 JOB_NOT_SUCCEEDED；重新启动 Worker。
- 实际 Rust 试听：源起点 48,000 样本，输出 96,000 样本；等响度差 0.0 LU，原音／效果下载摘要均通过。
- 用户部署文档中的停机整目录备份命令实际成功，归档含 SQLite 和结果；重启 API/Worker 后 `/ready` 恢复，原 job 仍可下载且摘要相同。没有执行备份恢复演练或容器运行中强杀／租约恢复测试；后者由既有应用测试覆盖，不冒充本轮实机证据。
- Compose 在未配置 Key 时拒绝启动配置。

证据：[API 检查](p5-02-container-api-checks.json)、[备份／重启](p5-02-backup-restart.json)、[Compose 配置检查](p5-02-compose-config-check.json)、[本地 GUI 冒烟](p5-02-gui-smoke.json)。

## 4. 验证汇总与交付状态

| 验证 | 本轮结果 |
|---|---|
| `uv run pytest` | **104 passed**，26.56 秒，含既有真实 Rust 契约与新增 6 项客户端测试；1 条既有 Starlette/httpx 弃用提示 |
| 客户端最终小修后的专项复验 | 6 passed |
| `uv run ruff check src tests/app` | 通过 |
| 显式将 examples 纳入 Ruff 检查 | 通过 |
| `uv run mypy` | 通过，32 个源文件 |
| `uv lock --check`、`git diff --check` | 通过 |
| `npm --prefix web ci`／`npm --prefix web run build` | 通过；未修改前端源码 |
| `bash -n examples/ai_video_pipeline.sh` | 通过，另有本机／API 两次实际执行 |
| 文档链接、12 个 CLI 帮助入口 | 通过 |
| Docker 构建、Compose 启动、宿主机真实转换 | 通过；Rust 编译阶段缓存复用已明确记录 |
| 真峰值 ≤−1 dBTP | **未通过：实测 −0.6 dBTP，核心已知问题** |

本机：uv 0.12.13、Cargo 1.99.1、Node 24.12.0、应用 Python 3.12.13；独立宿主机示例亦使用 Python 3.14.7 执行成功。没有修改 core/、prototypes/、presets/、tools/、qa/tools/ 或 docs/user/ 以外的 docs 内容。

最终已对 `aurora-p5-02` 执行 `compose down`，未删除 volume；镜像、`aurora-p5-02_aurora_runtime` 卷、私有 env、备份和 `.runtime/` 音视频留在本机供复验。没有留下本次测试监听端口。常规文档使用默认 8000，本次测试为避免影响其他服务使用 18765。

本轮完成应用接入与文档交付；路线 V、核心限制器修复、人工听感验收、发布平台 AAC/MP4 回读与生产部署不在本轮完成声明中。
