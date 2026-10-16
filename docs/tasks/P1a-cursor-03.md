# P1a-cursor-03：把 P0-03 算法同步进 Rust 核心

状态：已派发（2026-10-17 06:55）
执行方：Cursor（模型 grok-4.7-high）　分支：`agent/cursor/P1a-02`（基于最新 main）
时间上限 120 分钟；每完成一个子项就先提交一次。
依据：`qa/p0/ACCEPTANCE-cursor-05.md`、`qa/p1/ACCEPTANCE-cursor-p1a-01.md`、原型 `prototypes/aurora_p0/`（以 main 上的当前版本为准）

## 任务

1. 把原型在 P0-02 和 P0-03 中的全部算法改动移植到 `core/aurora-dsp`：
   - 上混中直达成分与环境成分的分配
   - 侧向通路的固定延时（从预设读取）
   - 引擎内部的 512 点最小相位固定 FIR 均衡（按 HRTF × 预设 × 强度从 `presets/p0_draft.toml` 读取，参数之间做插值或取最近值，并说明采用哪种）
   - 开头点击和尾点问题的修复
2. 预设 TOML 的解析与校验：缺少字段时报错并说明缺了哪个，不允许使用静默默认值。
3. **限制器**：确认真峰值限制器位于链路的最后一级。用 `data/assets/holdout_electronic.wav`（原型输出峰值 +3.5 dBFS）验证：Rust 输出的真峰值 ≤ −1 dBTP（4 倍过采样测量）。
4. Python 绑定和 `Renderer` 协议保持不变；如果需要新增参数，向后兼容。

## 验收（写进 `qa/p1/cursor/REPORT-v2.md`）

- **与原型做数值回归**：真实素材（3 段训练素材加 2 段留出素材）× KU100／SADIE × 强度 0.2／0.4／0.8，1/3 倍频程差 ≤ 0.1 dB，IACC 差 ≤ 0.005。限制器动作导致的差异要单独标出，并给出不开限制器时的对照结果。
- 块大小一致性（32／64／256／1024／4096，以及整段一次处理）≤ −100 dBFS；`process` 不分配内存的测试继续通过。
- `cargo fmt --check`、`cargo clippy -- -D warnings`、`cargo test` 全部通过；在 Mac 上用 criterion 测一次实时倍数。
- 只修改 `core/`、`qa/p1/cursor/`，以及 `data/` 下的 pack（如需要；必须走 LFS）。不要修改 `prototypes/`、`presets/`、`qa/tools/`。
- 在 `agent/cursor/P1a-02` 上提交，不要推送。如果要用 `git lfs migrate`，必须加 `--exclude-ref=refs/remotes/origin/main`。

## 续做说明（2026-10-17 07:25）

第一次运行在 07:11 因派单器被关闭而中止。中止前的改动已经由派单器以 WIP 形式提交到 `agent/cursor/P1a-02`，请在此基础上继续，计时重新开始，上限仍为 120 分钟。

注意：你新建了 `core/aurora-dsp/src/bed.rs`。Ambisonics 床属于 P1b，不在本任务范围内。如果它不是路线 B 必需的，请移除或停用，并在报告中说明。
