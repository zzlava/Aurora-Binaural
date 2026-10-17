# P1a-cursor-05：修复可变块大小导致的输出错误，修正路线 B 的真峰值

状态：已派发（2026-10-17 09:57）
执行方：Cursor（模型 grok-4.7-high）　分支：`agent/cursor/P1a-03`（在原分支上继续）
时间上限 90 分钟。
前置：阅读 `qa/p1/REVIEW-cursor-p1a-04.md`。复现脚本：`qa/tools/rust_block_fuzz.py`（只读，归 Claude 所有）。

## 任务

1. **B1**：找出并修复出错的根因。不要只放大缓冲区来掩盖问题，必须说明是哪个组件、在什么条件下溢出或错位。
   - 在 Rust 中加入**属性测试或模糊测试**：对路线 B 和路线 V，至少 200 个随机种子，块大小在 1 到 `max_block` 之间随机，`max_block` 分别取 512、4096；与固定块大小 256 的结果相比，误差 ≤ −100 dBFS。
   - 把 seed 3 这个失败用例固化成回归测试。
2. **B2**：路线 B 限制器的真峰值，在 `holdout_electronic` 和一段 0 dBFS 正弦扫频上都要 ≤ −1.0 dBTP（4 倍过采样测量，用 scipy `resample_poly` 交叉验证）。
3. 修复之后，P1a-cursor-04 的全部门禁重新跑一遍（与原型对照、零分配、fmt、clippy、test、criterion），结果写进 `qa/p1/cursor/REPORT-v3.md` 的新小节。

只修改 `core/`、`qa/p1/cursor/`。在本分支提交，不要推送。
