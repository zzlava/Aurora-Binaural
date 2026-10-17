# 验收：P6-codex-03（setup.sh、自动探测 soxr ffmpeg、测试稳定性）

验收人：Claude　日期：2026-10-17　结论：**通过**

- 提交范围 a842ad8..2f221f0，未改动 core/、presets/、tools/、qa/tools/、prototypes/。
- scripts/setup.sh 共 43 行，已逐行阅读：只修改当前进程的 PATH，没有 rm、sudo、管道执行 sh，也不改 shell 配置文件；固定使用 Python 3.12；依次执行 uv sync --locked、ffmpeg 探测、build-core、npm ci 与 build。
- 干净快照（Ubuntu 22.04 VM，FFmpeg 4.4.2，**未设置 AURORA_FFMPEG**，自动探测生效）：tests/app 全部通过，跳过 1 项，即 Codex 报告的旧绑定兼容测试。P6-02 验收时的 AAC 用例与 version 用例现在都通过。
- cancel 竞态用例连续运行 5 次全部通过（Codex 报告 30/30）；ruff 通过。
- 时间线验收已采用新定义，空间路径峰值最大偏移 0.292 ms。
