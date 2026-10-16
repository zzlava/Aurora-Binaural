# P4-codex-01：Web GUI（框架与完整流程，效果试听接桩核心）+ F-P3-1

状态：已派发（2026-10-17）
执行方：Codex（`codex exec`；接替 Antigravity，见 ADR 0004）
来源：P4-antigravity-01 在 Antigravity 额度用尽时失败，没有产生任何改动；本任务内容与它相同
依据：docs/plan.md 第 10、12 节；`docs/adr/0002`、`docs/adr/0003`；`qa/p3/ACCEPTANCE-antigravity-p3-01.md`

## 工作环境

- 独立克隆 `.agents/codex/`，分支 `agent/codex/P4-01`（基于最新 main）。
- 可以修改：`web/`、`src/`、`tests/app/`、`deploy/`、`qa/p4/antigravity/`、`pyproject.toml`、`uv.lock`。不要修改 `core/`、`prototypes/`、`data/`、`docs/`、`tools/`。

## 技术选型（Claude 决定）

- 前端：Vite + TypeScript + Preact，放在 `web/`，使用 pnpm（没有就用 npm）。锁定依赖版本，提交 lockfile；`web/dist/` 不提交。
- 构建产物由 FastAPI 作为静态文件提供。`aurora gui` 启动本地 API 和 Worker（只监听 127.0.0.1），然后打开浏览器。
- Docker 改为多阶段构建：Node 阶段构建 `web/dist`，再复制进运行镜像。
- 不使用任何外部 CDN，所有资源都在本地打包。

## 功能（plan 第 12 节）

1. 主流程：导入（拖入或选择文件，可以多个）→ 选择预设与强度 → 生成试听片段 → 原声／效果等响度 A/B 切换 → 导出。
2. 任务队列：显示状态、进度、取消、错误详情、下载链接，以及结果报告的摘要（采样率、样本数、响度、HRTF 名称）。
3. 本地／远程模式：远程模式填服务器地址和 API Key。Key 只放在 sessionStorage，不写进 URL，也不写进日志。
4. 试听片段
   - 需要后端新增接口：`POST /v1/previews`（素材 ID 加起始秒数加时长，最长 20 秒），复用任务系统；结果同时给出原声片段与效果片段，二者按 BS.1770 等响度匹配。
   - 运动效果的片段要按它在原音频中的时间位置计算（plan 第 12 节）。
5. A/B 播放器：在两个已解码的 AudioBuffer 之间做无缝切换，切换时播放位置保持同步。键盘快捷键：空格播放／暂停，A/B 键切换。
6. 开发者区域默认折叠，显示原始 JSON 和日志。
7. 体验要求：中文界面；同时支持浅色和深色模式；窄屏（≤ 480 px）可用；所有操作都有加载中和出错状态。
8. 默认 HRTF 名称显示为 SADIE II（ADR 0004）。现阶段核心仍是桩实现，所以界面要显著标注"效果引擎：桩（直通），P1 核心接入后替换"，避免误导。

## F-P3-1

清理任务要回收租约已过期的那些尝试留下的临时目录（包括 Worker 被 SIGKILL 后遗留的空目录），并补上测试。

## 测试与交付（`qa/p4/antigravity/`）

- 用 Playwright 写端到端测试（本机有就用；Python 版或 Node 版都可以）。至少覆盖：上传 → 试听 → A/B 切换 → 导出下载；远程模式填错 Key 时的提示；取消任务。
- 截图：浅色和深色、桌面和窄屏各一张，放在 `qa/p4/antigravity/screenshots/`。
- `REPORT.md`：实际运行过的命令及退出码、端到端结果、已知局限。
- `uv run pytest`、`ruff`、`mypy` 全部通过；前端的 `tsc --noEmit` 和 lint 也要通过。

## Git 规则

只在 `agent/codex/P4-01` 上提交，提交信息末尾加 `Agent: Codex`。不要推送，不要修改 main。

## 时间上限

总时长不超过 150 分钟。每完成一个功能点就先提交一次；超时就把已完成和未完成的部分写进报告，然后结束。
