# 给 Antigravity：P0 应用层预研

状态：草案，尚未派发
来源：docs/plan.md 第 19 节

```text
你负责 Aurora Binaural（空间听感音频工具）的应用层：CLI、API、Worker、GUI、部署。Claude 负责方案、
协调、验收与 Git，Cursor 负责 DSP 核心。只在 cli/、api/、worker/、web/、deploy/
内修改，在分支 agent/antigravity/P0-01 的独立工作树中工作。

P0 只做准备工作，不实现完整功能：
1. 搭建 Python 项目骨架（pyproject、隔离环境、Ruff、mypy、pytest）；
2. 按方案第 9、10 节，用 Pydantic 定义统一的参数模型，并生成 JSON Schema；
3. 写一个桩核心（只做直通），打通 "CLI convert → 解码 → 桩核心 → 编码 → 回读 → 报告"，
   验证 WAV/M4A/MP3 的样本数和编码延时处理；
4. 验证 Mac ARM64 本地运行，以及 Linux 容器内的 ffmpeg 构建路线；
5. 交付：命令、日志、测出的编码延时，以及对 P2/P3 的修改建议。

不要实现 DSP，不要修改 core/。不要提交到 main，不要推送，不要删除或覆盖已有文件。
```
