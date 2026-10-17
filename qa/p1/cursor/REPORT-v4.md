# P1b-cursor-01 验收报告

日期：2026-10-17。分支 `agent/cursor/P1b-01`。执行方 Cursor（grok-4.7-high）。依据 ADR 0005。

只改了 `core/`、`prototypes/`、`presets/`、`qa/p1/cursor/`。没有推送。

## 宏控件

五个控件都在 0 到 1。`presets/route_v.toml` 的 `[macros]` 给出映射，`[V1.controls]`、`[V2.controls]`、`[V3.controls]` 给出各预设的默认值。

`width`、`height`、`room`、`voice_distance` 是锚在预设上的直线：

`value = clamp(stored + (control - default) * per_unit, min, max)`

控件等于该预设的默认值时，加项是 0，展开结果和预设里写下的内部参数逐位相同。`strength` 在这之后按原来的规则作用：非零时把 `front_az` 从 30° 打开到锚点（再夹到 30–60°），并把宽声场、环绕、高度、早期、后期的线性增益乘以 `strength`。`strength == 0` 在还没出声时仍是整段旁路。`voice_distance` 的 0 是近端。限制器一直开着。

| 控件 | 内部参数 | 每单位 | 下限 | 上限 |
|---|---|---|---|---|
| width | front_az | 30° | 30° | 60° |
| width | wide_db | 14 dB | −12 | 8 |
| width | surround_db | 16 dB | −18 | 4 |
| height | height_db | 8 dB | −12 | 2 |
| height | height_el | 20° | 30° | 70° |
| height | ceiling_db | 4 dB | 0 | 10 |
| height | height_direct_db | 8 dB | −20 | −6 |
| room | room_db | 16 dB | −12 | 16 |
| room | t60 | 1.0 s | 0.3 s | 1.8 s |
| voice_distance | center_distance_m | 2.6 m | 0.2 m | 3.2 m |
| voice_distance | direct_room_send_db | 16 dB | −18 | 6 |

默认值：三个预设的 `strength` 都是 1，`width` 和 `room` 都是 0.5。V1 和 V2 的 `height` 是 0.25，V3 是 0.75。V2 和 V3 的 `voice_distance` 是 0.25（距离 0.90 m，直达送房间 −8 dB），V1 是 0.75（距离 2.20 m，送房间 0 dB）。`early_db` 和 `center_height_db` 不跟这四个控件走，早期增益仍只乘 `strength`。`center_spread_deg` 仍被 `front_az` 夹住。

V3 在默认控件、强度 1 时，`effective_params()` 给出 `front_az` 45°、`height_el` 55°、`t60` 0.95 s、`center_distance_m` 0.9 m、`wide_db` 1 dB。

## 展开和平滑

原型 `map_route_v` 和 Rust `map_route_v` 用同一条锚点直线，再做原来的强度缩放。`RenderConfig.controls` 传给路线 V 的 `prepare`。缺省时行为和以前一样：只按强度和预设默认值展开。

`Params` 增加 `width`、`height`、`room`、`voice_distance`，缺省表示用预设默认。路线 B 忽略这四个字段。

已经出过声之后再改控件，过渡是 1536 个样本，48 kHz 下 32 ms，落在 20–50 ms 里。`process` 这段不分配内存：

- 八路床增益和最多 16 条早期反射的增益逐样本线性插值。
- 前方张角在过渡期间每 64 个样本更新一次上混宽度。稳态仍是一次 `process`，块大小不变性还在。
- 方位、仰角或近场距离变了才换 HRIR。卷积器的交叉淡化从 128 点改成 1536 点。第一次装入系数时没有旧脉冲响应，不淡化。
- `t60` 只改 FDN 各延迟线的目标衰减，按现有长度重算增益并逐样本跟上。不清缓冲，不换反馈矩阵。
- 均衡系数在 `set_params` 里设计。播放中逐样本把现有系数收到新系数上，过渡结束时对齐。
- 播放中不切到零延时旁路，否则延时跳变会有阶跃。`strength` 收到 0 时处理链还在，床增益收到 0。从旁路回到处理时，输出在限制器前和干信号做 1536 点交叉淡化，起点等于干信号。

还没出声时，`set_params` 仍是立刻安装，包括 FDN 重新播种。先设参数再处理，和冷启动逐位一致。播放中改种子被忽略，避免清掉混响缓冲。

## Python

`Renderer.set_controls(dict)` 只改字典里出现的键，值必须是 0 到 1 的有限数。`effective_params()` 返回目标控件和展开后的渲染参数（方位、仰角、天花板、T60、距离、线性增益，以及锚点的 dB），不是过渡中途的值。只对路线 V 有效。原来的 `set_params` 仍接受强度、预设和种子；对象上如果带了四个新字段，也会更新。

## 测试

脚本 `qa/p1/cursor/test_controls_v4.py`。方向用原型、SADIE II、`frontal+power`、预设 V3、强度 1，其余控件停在 V3 默认值。床声是 0.5 秒不相关噪声，人声是同长度的双声道同相噪声，幅度 0.04。

| 控件 0 / 0.5 / 1 | 指标 | 读数 |
|---|---|---|
| width | IACC | 0.140 → 0.063 → 0.044 |
| room | 爆发相对晚期尾音的 DRR | 66.7 → 51.2 → 39.3 dB |
| voice_distance | 中心声像对房间的 DRR | 13.48 → 5.49 → −2.52 dB |
| height | 高度层 RMS | 0.0199 → 0.0313 → 0.0523 |

宽度增大时 IACC 下降，房间感增大时尾音 DRR 下降，人声距离增大时人声 DRR 下降，头顶感增大时高度层能量上升。房间感的 DRR 取爆发之后 4096 个样本以外的尾音：早期反射不跟 `room` 走，若把延迟后的直达算进尾音，读数几乎不动。尾音能量本身随 `room` 上升（RMS 约 2.2e-5 → 1.3e-4 → 5.3e-4）。

Rust 绑定，同一段 200 Hz 正弦：

- 只设强度 1，和显式传入 V3 五个默认控件，输出最大绝对差是 0。
- 处理到一半把控件改成强度 0.35、宽度 1、高度 0、房间 1、人声距离 1。输出没有 NaN 或 Inf。真峰值 −13.09 dBTP，低于 −1 dBTP。改参之后的单声道一阶差分峰值是 0.00566，改参之前是 0.00575。
- `prepare` 的最大块长固定为 4096，宿主块长 64 和 1024 相对 256，误差是 0（脚本里显示 −400 dBFS，那是 1e-20 的下限）。

`cargo test -p aurora-dsp --lib` 在平滑实现上是 50 passed，含路线 V 的块大小对照（32、64、256、1024、4096 和整段，误差不超过 −100 dBFS）和 200 个种子的块长模糊。`mid_stream_controls_do_not_step_or_allocate` 把分配计数包在改参之后的 `process` 上，分配次数是 0，样本有限且绝对值不超过 1。之后的改动是只读的 `effective_params` 和排版；`cargo fmt` 之后这几项和默认控件逐位测试仍通过。预设默认展开在 Rust 里用 `to_bits` 对过 V1、V2、V3 和强度 0、0.5、1：锚点不变，再经强度映射后的前方张角、增益、仰角、T60、距离和旁路与旧路径逐位相同。
