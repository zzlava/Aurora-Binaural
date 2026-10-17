# P6-codex-04：GUI「高级」面板——5 个空间宏控件与“我的预设”（ADR 0005）

状态：已派发（2026-10-17）
执行方：Codex　分支：`agent/codex/P6-04`（基于最新 main，已含 P1b 宏控件核心）
时间上限 120 分钟；每完成一个子项就先提交一次。不改 core/、presets/、tools/、qa/tools/、prototypes/。

## 核心接口（P1b 已合并）
- `Renderer.set_controls(dict)`：键为 strength、width、height、room、voice_distance，取值为 [0,1] 内的有限数。越界值和 NaN 抛出 ValueError；未知键即将改为报错（Cursor P1c）。
- `Renderer.effective_params()`：返回 dict，含 controls 及展开后的内部参数。
- 每个预设的默认控件值在 `presets/route_v.toml` 的 `[Vx.controls]` 中，catalog 应从这里读取，不要硬编码。

## 任务
1. **参数模型**：AuroraParams 加入 5 个控件（可选；不传时使用预设默认值）。应用层把数值截断到 [0,1] 并记录截断事件；render report 写入最终 controls 与 effective_params。route B 与桩不支持这些控件时明确忽略，并在报告中注明。
2. **CLI**：`aurora convert/preview` 增加 `--width --height --room --voice-distance`（0–1，或 0–100%），`--strength` 不变。`aurora presets --json` 输出每个预设的默认控件值。
3. **API**：任务与试听请求接受 `controls` 对象；OpenAPI 同步更新。
4. **GUI**：在空间强度下方加入可折叠的“高级”面板，5 个滑块中文标签依次为：空间强度、宽度、头顶感、房间感、人声距离；显示百分比。提供“恢复预设默认”按钮。松开滑块后 300 ms 防抖，自动重新生成当前试听片段，并保持 A/B 的播放进度。
5. **我的预设**：把“基础预设 + 5 个控件”保存为命名预设，存在本地 SQLite（新表，带迁移），支持列表、重命名、删除，可导出/导入为 JSON。导出与批量处理都可以选用“我的预设”。
6. **测试**：pytest 覆盖截断、默认值来自 TOML、controls 写入报告、我的预设的增删改与迁移；用 Playwright 真实服务测试“拖动滑块 → 自动重新生成试听 → 下载 → 报告中的 controls 与设置一致”；附桌面与窄屏截图。
7. **文档**：docs/user/quickstart.md 与 cli.md 写明每个控件的听感含义（宽度=声场左右展开；头顶感=上方扬声器层；房间感=混响量与长度；人声距离=人声远近）。

## 交付
qa/p6/codex/REPORT-04.md：附测试结果、截图、各控件取值 0/0.5/1 的 render report 片段。
