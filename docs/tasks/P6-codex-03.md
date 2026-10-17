# P6-codex-03：一键安装脚本，以及 P6-02 的后续项

状态：已派发（2026-10-17）
执行方：Codex　分支：`agent/codex/P6-03`（基于最新 main，已含 P0-07 的 V3 默认和 P6-02）
时间上限 90 分钟；每完成一个子项就先提交一次。不改 core/、presets/、tools/、qa/tools/、prototypes/。

## 任务
1. **`scripts/setup.sh`**（macOS 与 Linux 通用，可重复执行）：
   - 检查 cargo、rustup；PATH 里找不到时，自动尝试 `/opt/homebrew/opt/rustup/bin` 与 `$HOME/.cargo/bin`，仍找不到就给出安装提示。
   - 检查 uv、node/npm；依次执行 `uv sync`、`scripts/build-core.sh`、`cd web && npm ci && npm run build`。
   - 探测带 soxr 的 ffmpeg：先用 PATH 里的 ffmpeg，再试 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`，判断方法是实际跑一次 soxr 重采样。结果写入 `.aurora-local.env`（加入 gitignore），并提示用户执行 `brew install ffmpeg-full`。
   - 最后打印一行：下一步运行 `uv run aurora gui`。
2. **自动探测 soxr ffmpeg**：没有设置 `AURORA_FFMPEG` 时，应用按与上面相同的顺序探测（读取 `.aurora-local.env`，或直接探测），使 macOS 用户不设环境变量也能导入 44.1 kHz FLAC。ffprobe 用同样的方式处理。
3. **测试稳定性**：
   - AAC/m4a 时间线用例按 FFmpeg 主版本处理 priming 差异，或改为基于编码器报告的 initial_padding；在 FFmpeg 4.4（Ubuntu 22.04）和 9.x 上都要通过。
   - 修复 `test_cancel_queued_running_duplicate_and_terminal_state_invariance` 中 lease 临时目录清理的竞态（复现率约 1/3），修复对象应是实现本身，不能只放宽测试。
   - core 没有 `__version__` 时，status 回退为 wheel 元数据里的版本号（`importlib.metadata.version("aurora-py")` 或 `aurora_core`）。
4. **时间线验收新定义**：verify_02.py 和应用回归改为“样本数一致 + 旁路逐位对齐 + 空间路径峰值偏移 ≤1 ms 并记录在报告中”（Claude 已撤回 ±1 源采样要求，见 qa/p6/ACCEPTANCE-codex-p6-02.md）。修改 docs/user/troubleshooting.md 中对应的表述。
5. **文档**：在 docs/user/quickstart.md 开头加入 setup.sh 的一步式说明。

## 交付
qa/p6/codex/REPORT-03.md：写明每项结果、在 Linux 与 macOS 上分别执行 setup.sh 的输出摘要、完整 pytest/ruff/mypy/npm 结果。
