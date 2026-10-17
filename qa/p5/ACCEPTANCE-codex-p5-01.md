# 验收记录：P5-codex-01（Rust 核心适配器、数据驱动的预设目录）

验收人：Claude　日期：2026-10-17 09:55（UTC+8）
分支：`agent/codex/P5-01` @ `4a4fe5b`（运行 15 分钟）
结论：**通过**，合并到 main。

| 项目 | 结果 |
|---|---|
| 改动范围 | `src/`、`tests/app/`、`web/`、`deploy/`、`qa/p5/`、`pyproject.toml`、`uv.lock`，以及新增的 `scripts/build-core.sh`、`scripts/stage-core.py`（任务书允许"放在 `tools/` 之外的构建脚本"）✅ |
| pytest／ruff／mypy（虚拟机干净快照） | 退出码 0；31 个源文件 ✅ |
| `aurora presets --json` | 来自 TOML：V1／V2／V3（默认 V2）加上经典 `room`；默认列表里没有 orbit ✅ |
| 没有安装 `aurora_core` 时 `AURORA_ENGINE=rust` | 退出码 4，`CORE_NOT_INSTALLED`，给出构建提示，没有静默降级 ✅ |
| `AURORA_ENGINE=stub` | 成功，报告里写明 `is_stub: true` ✅ |

## 后续

- 预设显示名直接取自 TOML（例如"G，人声更近"），读起来偏内部化。定版时在 TOML 里改成面向用户的名称。
- 代理用路线 B 的 Rust 核心实测，真峰值为 **−0.6 dBTP**，超过了 −1 dBTP 的目标。这是 Rust 核心路线 B 限制器的问题（与 Cursor 在路线 V 中发现的"8 点检测器低估约 0.7 dB"同源），交给 P1a-cursor-05 修复。
