# Aurora Binaural

[English](README.md) | **中文**

把普通立体声音乐处理成**耳机可听的双耳空间音频**：声场更宽、有头顶感、人声仍贴近听者。提供 Web GUI、CLI 与 HTTP API；核心为实时安全的 Rust 引擎，为将来的实时处理预留了路线。

## 现在能做什么

- **真实空间引擎（路线 V）**：多只虚拟扬声器（前方 ±45°、两侧 ±90°、后方 ±130°、4 只 45° 仰角的头顶扬声器）加上 shoebox 房间、天花板与地面反射和 FDN 混响；人声与中央声像保持在近处。默认 HRTF 为 SADIE II D1（Apache-2.0）。
- **三种预设**（默认 V3）：
  - V1 开阔·人声稍远
  - V2 沉浸·人声贴近
  - V3 沉浸·头顶环绕（推荐）
  - 另保留经典 room（路线 B）。
- **5 个空间宏控件**：空间强度、宽度、头顶感、房间感、人声距离，可在 GUI“高级”面板、CLI、API 中调节，播放中途调整不会产生爆音；可以保存为“我的预设”（支持导入／导出 JSON）。
- **高解析度输入输出**：FLAC、WAV（含 RF64）、ALAC、AIFF、AAC、MP3；支持 44.1–192 kHz 输入，导出默认与源文件同采样率；保留标签与封面；16 bit 输出加 TPDF 抖动。内部以 48 kHz float32 处理（ADR 0006）。
- **等响度 A/B 试听**：BS.1770 对齐，切换版本时保持播放进度。
- **低频保真**：
  - 处理前自动预留余量，响度很高的母带也不会被限幅器压扁低频（P1c）。
  - 左右不相关的厅堂录音低频不再被合成单声道而抵消（P1d）。
  - 已修正 700 Hz 着色。

## 快速开始（macOS）

```bash
cd ~/Desktop/leo/"Aurora Binaural"
bash scripts/setup.sh      # 检查 Rust/uv/Node 与带 soxr 的 ffmpeg（推荐 brew install ffmpeg-full），构建核心与前端
uv run aurora gui          # 打开 Web GUI
```

CLI 示例：`uv run aurora convert 输入.flac --preset V3 --format flac --sample-rate source`。详见 [docs/user/quickstart.md](docs/user/quickstart.md)、[cli.md](docs/user/cli.md)、[api.md](docs/user/api.md)、[troubleshooting.md](docs/user/troubleshooting.md)、[deploy.md](docs/user/deploy.md)。

## 进度（2026-10-21）

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 算法路线与听感校准（路线 B → 校准锚点 → 路线 V，用户选定 V3） | ✅ 已验收（`p0-accepted`） |
| P1a | Rust 核心：实时安全、零分配、可变块长 fuzz、真峰值 −1 dBTP 限幅 | ✅ |
| P1b | 5 个宏控件（默认值展开后与原预设逐位一致） | ✅ |
| P1c | 输入余量与低频平衡（响度母带的限幅从约 9 dB 降到 ≤1.6 dB） | ✅ |
| P1d | 保留不相关低频的能量，修正 700 Hz 凸起（《卡门》低频偏差从 −5.2 dB 降到 −1.1 dB） | ✅ |
| P3–P6 | 应用层：CLI、FastAPI、SQLite Worker、Web GUI、Docker、高解析度格式、setup.sh、高级面板与我的预设 | ✅ |
| P6-05 | 导出响度：默认“智能音量”（纯增益归一化到 −1 dBTP），另有匹配原曲、−14 LUFS、不调整；合并重复的强度滑块 | ⏳ 已写任务书，待派发 |

待用户决定的事项见 [docs/DECISIONS-PENDING.md](docs/DECISIONS-PENDING.md)（例如 U6：用 force push 清理 Git 历史中误提交的约 130 MB 音频）。

## 文档

- 方案：[docs/plan.md](docs/plan.md)
- 决策记录：[docs/adr/](docs/adr/)（0001 架构 … 0006 高解析度）
- 任务书：[docs/tasks/](docs/tasks/)
- 验收记录：`qa/p0` … `qa/p6`（每单都有 ACCEPTANCE 文件）
- 代理协作与派单器：[docs/agents.md](docs/agents.md)

## 协作方式与目录所有权

Claude 负责方案、任务协调、独立验收与 Git；编码由本机的 Cursor（核心）与 Codex（应用层）完成，通过 `tools/dispatch.sh` 派单器在 macOS 终端执行（`bash tools/dispatch.sh`）。

| 目录 | 负责方 |
|---|---|
| `core/` `prototypes/` `presets/` | Cursor |
| `src/` `tests/app/` `web/` `deploy/` `scripts/` | Codex（2026-10-17 起接替 Antigravity，ADR 0004） |
| `qa/` `docs/` 与 Git 操作 | Claude |
| `data/` | Cursor 提交数据，Claude 核验来源、授权与哈希 |

## 分支规则

- `main`：只合并经 Claude 验收的内容，禁止 force push。
- `agent/<代理>/<任务编号>`：代理工作分支，各用独立克隆（`.agents/`）。
- 阶段验收通过后打标签 `pN-accepted`。

## 许可证

- 本项目代码以 [MIT 许可证](LICENSE) 发布。
- 第三方数据各自沿用原许可证：SADIE II HRTF 为 Apache-2.0，TH Köln KU100 HRIR 为 CC BY-SA 3.0（仅用于本地研究，见 [data/hrtf/SOURCES.md](data/hrtf/SOURCES.md)）；测试音频为 CC0 或 CC BY 3.0，作者与出处见 [data/assets/registry.json](data/assets/registry.json)。完整声明见 [deploy/THIRD_PARTY_NOTICES.md](deploy/THIRD_PARTY_NOTICES.md)。

## 注意

- 本地文件夹名包含空格（`Aurora Binaural`），所有脚本与命令都要给路径加引号。
- `qa/listen/` 与仓库根目录的音频文件为用户本机的版权试听素材，已列入 `.gitignore`，不得提交。
