# 验收：P1c-cursor-01（路线 V 低频：余量与低频平衡）

验收人：Claude　日期：2026-10-17　结论：**通过（低频平衡部分达标）**

在 VM 的临时克隆中合入 main、P1c 与 P6-04，构建 maturin release 核心后独立复测（V3，强度 1，holdout_electronic 前 20 秒，频带增益相对 1 kHz）：

| | 真峰值 | 40 Hz | 63 Hz | 100 Hz | 160 Hz |
|---|---:|---:|---:|---:|---:|
| 改前，限幅器开 | −1.4 | −4.4 | −3.0 | −3.0 | −3.2 |
| 改后，限幅器开 | −2.41 | −0.8 | −1.2 | −1.1 | −2.0 |

- 余量：限幅器开与关时的输出完全相同，说明不再触发限幅；Cursor 用 −6.6 LUFS 的合成母带测得最大增益衰减 1.62 dB（≤ 2 dB）。
- 低频平衡：31.5–125 Hz 基本达到 ±1 dB，160 Hz 仍差约 2 dB。Cursor 说明了原因：残余差异与信号相关，动态补偿会造成泵动，因此采用固定搁架，Claude 认可这一取舍，最终以用户听感为准。
- 低于 120 Hz 的 L/R 相关：holdout 0.9992，real_music 0.987。强度 0 时逐位旁路（maxdiff 0）。
- `cargo test -p aurora-dsp --lib`：51 项通过；集成后 tests/app 中除 core `__version__` 用例（属于 Claude 安装方式的环境产物）外全部通过。
- 集成问题：P1c 在 report 中新增了 headroom_db，但没有重新生成 qa/p6/codex/openapi.json，Claude 已重新生成（只新增这一字段的 6 行）。
- 副作用：响度母带的输出会比原曲轻约 7 dB（为避免限幅所付出的代价）。GUI 的 A/B 对比做了等响度匹配，不受影响；导出响度的策略留给用户决定（见 DECISIONS-PENDING N2）。
