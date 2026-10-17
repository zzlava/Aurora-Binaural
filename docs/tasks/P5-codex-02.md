# P5-codex-02：使用文档、集成示例与容器端到端（含 Rust 核心）

状态：已派发（2026-10-17 10:00）
执行方：Codex　分支：`agent/codex/P5-02`（基于最新 main）
时间上限 120 分钟；每完成一个子项就先提交一次。
依据：docs/plan.md 第 9–13、16.4、17 节；`qa/p5/ACCEPTANCE-codex-p5-01.md`

## 任务

1. **用户文档**（`docs/user/`）。这个目录归你负责，本任务对它有写权限。
   - `quickstart.md`：本机安装、构建 Rust 核心、`aurora gui`、`aurora convert` 的用法。
   - `cli.md`：每个子命令、退出码、JSON 输出字段。
   - `api.md`：每个端点，以及鉴权、幂等、轮询、下载、过期、错误码；附 curl 示例。
   - `deploy.md`：Docker Compose 部署、环境变量、数据目录、备份、升级、日志。
   - `troubleshooting.md`：ffmpeg 版本、M4A 时间线、找不到核心、端口占用等常见问题。
2. **集成示例**（`examples/`）：
   - `python_client.py`：上传、创建任务、轮询、下载、校验 SHA-256，带重试和幂等键。
   - `ai_video_pipeline.sh`：从一段 AI 生成的配音或音乐出发，经过空间化，再用 ffmpeg 合入视频，并保持音画同步（plan 第 13 节）。用一段合成的测试视频跑通，并记录同步误差（样本级或帧级）。
3. **容器端到端**：`deploy/Dockerfile` 的多阶段构建要包含 Rust 核心（路线 B 已在 main 上），在本机 OrbStack 上实际构建并 `compose up`；从宿主机用 `examples/python_client.py` 完成一次真实引擎（`AURORA_ENGINE=rust`）的转换，记录真峰值和样本数。
4. 报告写在 `qa/p5/codex/REPORT-02.md`。

## 规则

可以修改 `src/`、`tests/app/`、`web/`、`deploy/`、`docs/user/`、`examples/`、`scripts/`、`qa/p5/codex/`、`pyproject.toml`、`uv.lock`。不要修改 `core/`、`prototypes/`、`presets/`、`docs/` 下除 `docs/user/` 以外的内容，也不要修改 `tools/`、`qa/tools/`。只在本分支提交，不要推送，提交信息末尾加 `Agent: Codex`。
