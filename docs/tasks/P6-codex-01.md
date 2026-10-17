# P6-codex-01：GUI／CLI／API 默认走真实的 Rust 路线 V，用户可以直接处理自己的音频

状态：已派发（2026-10-17 10:55）
执行方：Codex　分支：`agent/codex/P6-01`（基于最新 main，已包含路线 V 的 Rust 核心）
时间上限 120 分钟；每完成一个子项就先提交一次。

## 任务

1. 用 `scripts/build-core.sh` 在本机构建并安装最新的 `aurora_core`（包含路线 V）。`aurora gui`、`aurora convert`、API 和 Worker 都默认使用 `route="V"` 加预设 `V2`，HRTF 默认 SADIE II。路线 B 的 `room` 保留为"经典"。
2. **端到端**：在 GUI 中实际导入 `data/assets/real_music.wav` 和 `data/assets/holdout_electronic.wav`，选 V2，生成试听，做 A/B 对比，导出 WAV，下载并回读，记录样本数、真峰值、处理耗时。截取 4 张截图（浅色／深色，桌面／窄屏），放到 `qa/p6/codex/screenshots/`。
3. 界面上原来的"效果引擎：桩"提示，在 Rust 引擎可用时改为显示**实际引擎信息**：路线、预设、HRTF、核心版本或 git 提交号；只有在桩模式下才显示桩提示。
4. **文档**：
   - `docs/user/quickstart.md` 写明默认使用路线 V2，以及怎么构建核心。
   - `docs/user/troubleshooting.md` 增加一条："处理后峰值是对齐的，在起点前约 2 ms 处有一段低电平（约 −40 dB）预振铃，这是正常现象"，并引用 `qa/p1/ACCEPTANCE-cursor-p1a-05.md`。
5. **长文件**：用一个 20 分钟的立体声文件跑通真实路线 V 的 `aurora convert`，记录实时倍数和最大内存占用（RSS）。
6. 报告写在 `qa/p6/codex/REPORT.md`。

## 规则

可以修改 `src/`、`tests/app/`、`web/`、`deploy/`、`docs/user/`、`examples/`、`scripts/`、`qa/p6/codex/`、`pyproject.toml`、`uv.lock`。不要修改 `core/`、`prototypes/`、`presets/`、`tools/`、`qa/tools/`。只在本分支提交，不要推送，提交信息末尾加 `Agent: Codex`。
