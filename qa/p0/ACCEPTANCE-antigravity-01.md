# 验收记录：P0-antigravity-01（应用层骨架与编解码时间线）

验收人：Claude　日期：2026-10-16
分支：`agent/antigravity/P0-01` @ `40c9559`
结论：**通过（附带后续事项）**，合并到 main。

## 独立复现（在 Linux 虚拟机中；环境与代理使用的 macOS 不同）

环境：Ubuntu 22.04 aarch64，Python 3.12（uv），ffmpeg 4.4.2；使用 `git archive` 导出的干净快照，不复用代理的 .venv。

| 检查 | 结果 |
|---|---|
| `uv sync` | 通过 |
| `uv run ruff check` | All checks passed |
| `uv run mypy` | Success: no issues found in 12 source files |
| `uv run pytest` | 14 passed，2 failed：`test_convert_roundtrip_sample_accuracy[m4a-44100/48000]` |
| `aurora --help` | 子命令齐全：convert／inspect／presets／schema／timeline-verify／batch／job |

两项失败的原因：ffmpeg 4.4 的 AAC 解码器不按尾包时长裁剪多余样本（48000 采样率下多出 499 个样本）。这与代理报告里在 Debian ffmpeg 5.1 容器中发现的现象一致；代理在 macOS 的 ffmpeg 9.0.2 上全部通过。

## 范围与规则

- 只修改了允许的目录，没有触碰 `core/`、`prototypes/`。
- 有 Docker 构建与容器内运行的记录（OrbStack 28.5.2）。这部分没有独立复现，依据是代理报告。
- 遇到同步问题时如实标记 `sync_within_tolerance=false`，并给出 `sync_warning`，没有谎报通过。

## 后续事项（纳入 P2／P3 任务书）

1. **F-AG-1**：容器镜像固定使用 ffmpeg ≥ 7（Debian trixie，或固定 SHA-256 的静态构建）；程序启动时检查 ffmpeg 的最低版本，不满足就拒绝处理 M4A，或给出明确降级提示。
2. **F-AG-2**：M4A 回读测试要感知 ffmpeg 版本：版本过旧时，应断言输出了 `sync_warning`，而不是要求样本数完全一致。
3. **F-AG-3**：Python 包目前放在 `cli/aurora_binaural/` 下，API 和 Worker 的代码会跨目录引用它。P2 要明确包的布局，考虑统一放到 `src/aurora_binaural/`，并由 Claude 记录 ADR。
