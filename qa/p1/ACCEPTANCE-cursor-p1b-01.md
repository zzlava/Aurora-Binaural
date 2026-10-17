# 验收：P1b-cursor-01（路线 V 的 5 个宏控件）

验收人：Claude　日期：2026-10-17　结论：**通过（附 1 个小后续项）**

- 分支 7d9063d 基于 71f54f7。Claude 在 VM 的临时克隆中合入 main（9e690b4），无冲突；route_v.toml 同时保留了 main 的元数据（default_preset、name、description、default_strength）和新增的 [macros.*]、[Vx.controls] 段。合并结果作为 agent/cursor/P1b-01m 合入 main。
- 在 VM 中分别构建 main 核心与 P1b 核心（maturin release）。用 holdout_electronic 前 6 秒对比，预设 V1/V2/V3 × 强度 0.5/1.0 × 块长 512/173，**12 组全部逐位一致**（maxdiff 0.0）。
- 运行中每 0.5 秒随机设置 5 个控件（取值 0–1，块长 256）：输出全部为有限值，峰值 −1.61 dBFS，最大一阶差分 0.713，低于输入的 1.054，没有咔哒声。
- 输入检查：NaN 和越界值抛出 ValueError，非数值抛出 TypeError。任务书写的是“截断”，实现选择了拒绝，这种做法更严格，可以接受；应用层需要在调用前把数值截断到 [0, 1]（写入 GUI 面板任务）。
- `cargo test -p aurora-dsp --release`：50 项通过，包括块长 fuzz 测试。
- Cursor 报告的方向读数（IACC 随 width 增大而下降、DRR 随 room 和 voice_distance 增大而下降、高度层能量随 height 增大而上升）合理。
- 后续项（并入 P1c）：未知的控件名（如 `nonsense`）会被静默接受，应改为报错。
