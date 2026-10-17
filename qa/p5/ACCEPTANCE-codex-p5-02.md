# 验收记录：P5-codex-02（用户文档、集成示例、容器端到端）

验收人：Claude　日期：2026-10-17 10:55（UTC+8）
分支：`agent/codex/P5-02` @ `9479163`
结论：**通过**，合并到 main。

- 改动范围都在允许的目录内（`docs/user/`、`examples/`、`deploy/`、`qa/p5/` 等）✅；`docs/user/` 下 5 份文档，共 400 行。
- 虚拟机干净快照中 pytest 退出码 0 ✅；`examples/python_client.py` 编译通过，`ai_video_pipeline.sh` 语法检查通过 ✅。
- 容器端到端（代理在 OrbStack 上完成，使用真实 Rust 引擎）：回读 1,200,000 个样本，差值为 0，SHA-256 校验一致（依据代理报告）。
- 真峰值 −0.6 dBTP：用的是修复前的核心，P1a-cursor-05 已经修好（路线 B 现在是 −1.41 dBTP）。
- 同步：点击提前约 2 ms，是预振铃，不是延时错误（见 ACCEPTANCE-cursor-p1a-05）。要在 `docs/user/` 中补充这一点（P6 中处理）。
- 代理的做法值得肯定：没有在应用层加一个"凭猜测的固定偏移"去掩盖核心问题，而是把问题如实记录下来。
