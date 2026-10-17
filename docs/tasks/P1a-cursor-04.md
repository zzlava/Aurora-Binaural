# P1a-cursor-04：把路线 V 移植到 Rust 核心（参数全部来自预设）

状态：已派发（2026-10-17 08:50）
执行方：Cursor（模型 grok-4.7-high）　分支：`agent/cursor/P1a-03`（基于最新 main）
时间上限 120 分钟；每完成一个子项就先提交一次。
依据：`qa/p0/ACCEPTANCE-cursor-06.md`；原型 `prototypes/aurora_p0/route_v.py`（以 main 为准）；`presets/p0_draft.toml`

## 背景

路线 V 的结构已经确定：虚拟前方／宽声场／环绕／4 只高度音箱，房间部分包括天花板和地板反射以及 FDN，人声靠近处理，再加固定 FIR 均衡。具体参数要等第 3 轮听感结果，所以**所有参数都必须从预设 TOML 读取**，以后调参不需要改代码。

## 任务

1. 在 `core/aurora-dsp` 中实现路线 V 的块处理链，要满足实时安全（`process` 内不分配内存、不加锁），并通过 `Renderer` 选择路线（`route="V"`，预设名为 `V1`／`V2`／`V3`）。
2. 多个虚拟音箱的 HRIR 卷积复用现有的均匀分块卷积；方向数据来自 HRIR pack（默认 SADIE II，ADR 0004）。
3. 限制器放在链路末端；`holdout_electronic` 的真峰值必须 ≤ −1 dBTP。
4. 之前 `agent/cursor/P1a-02` 上中止的 WIP（路线 B 同步）只作参考，不要求合并进来。

## 验收（写进 `qa/p1/cursor/REPORT-v3.md`）

- **与原型路线 V 对照**：人声、钢琴、电子乐、雨声 × SADIE × V1／V2／V3 × 强度 0.5／1.0，1/3 倍频程差 ≤ 0.2 dB，IACC 差 ≤ 0.01。限制器动作导致的差异要单独标出。
- 块大小一致性 ≤ −100 dBFS；零分配测试通过；`cargo fmt`、`clippy -D warnings`、`test` 全部通过；用 criterion 测一次 Mac 上的实时倍数（路线 V 声道多，在报告里写明结果，目标 ≥ 10 倍实时）。
- 只修改 `core/`、`qa/p1/cursor/`，以及必要的 `data/` pack（走 LFS）。不要修改 `prototypes/`、`presets/`、`qa/tools/`。在本分支提交，不要推送。
