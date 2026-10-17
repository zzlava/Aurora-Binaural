# P5-codex-01：应用层接入 Rust 核心，预设目录改为数据驱动

状态：已派发（2026-10-17 08:50）
执行方：Codex　分支：`agent/codex/P5-01`（基于最新 main）
时间上限 120 分钟；每完成一个子项就先提交一次。
依据：ADR 0002、ADR 0004；`qa/p4/ACCEPTANCE-codex-p4-01.md`

## 背景

- Cursor 正在并行把路线 V 移植进 Rust 核心（`core/`，Python 包名 `aurora_core`）；main 上现有的 Rust 核心是路线 B。
- 用户已经明确**不要"绕圈"效果**。现在 GUI 里的"缓慢环绕（orbit）"预设要从默认列表中去掉。

## 任务

1. **核心适配器**：在 `src/aurora_binaural/engine.py` 中加一个 `RustRenderer` 适配器，包装 `aurora_core.Renderer`。
   - 安装了 `aurora_core` 就使用它；没安装时，**明确报错**或显式退回到桩实现，并在报告和界面上标注"桩"，不能静默降级。
   - 通过环境变量或配置选择引擎：`AURORA_ENGINE=rust|stub`，默认 `rust`；缺少 `aurora_core` 时报错并给出安装提示。
2. **构建**：在 pyproject 和 uv 中加入用 maturin 构建本地 `core/aurora-py` 的步骤（可以写成可选依赖组或单独的脚本），并在 `deploy/Dockerfile` 中加一个 Rust 多阶段构建；文档里写明本机的构建命令。
3. **预设目录数据驱动**：`/v1/presets`、CLI 的 `presets` 和 GUI 的预设卡片，都从 `presets/*.toml` 读取路线和预设名（现在先暴露 `V1`／`V2`／`V3`，默认 `V2`；路线 B 的 `room` 保留为"经典"）。不要再在代码里写死预设列表。显示名用中文，可以由 TOML 提供。
4. **HRTF**：只有 SADIE II pack 进入默认打包。如果 TOML 或界面里需要选择 KU100，标注"仅本地研究"（ADR 0004）。
5. **测试**：桩模式下的测试全部保留；新增适配器的契约测试（在有 `aurora_core` 的环境里运行：块大小、延时补偿、尾巴处理、与 `stub` 相同的时间线保证）。如果本机能构建 `aurora_core`，就用 main 上现有的路线 B 核心做一次端到端转换，记录样本数和真峰值。
6. 报告放在 `qa/p5/codex/REPORT.md`。

## 规则

只修改 `src/`、`tests/app/`、`web/`、`deploy/`、`qa/p5/codex/`、`pyproject.toml`、`uv.lock`，以及构建脚本（放在 `tools/` 之外）。不要修改 `core/`、`prototypes/`、`presets/`（只读）。只在本分支提交，不要推送，提交信息末尾加 `Agent: Codex`。
