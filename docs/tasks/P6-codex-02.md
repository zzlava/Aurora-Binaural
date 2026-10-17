# P6-codex-02：FLAC／高解析度格式支持，默认预设改为从 TOML 读取

状态：已派发（2026-10-17 11:45）
执行方：Codex　分支：`agent/codex/P6-02`（基于最新 main）
时间上限 120 分钟；每完成一个子项就先提交一次。
依据：ADR 0006；`qa/p6/ACCEPTANCE-codex-p6-01.md`

## 任务

1. 按 ADR 0006 实现以下内容：
   - 输入输出格式：FLAC、WAV（16／24／32f）、ALAC、AIFF，原有的 AAC 和 MP3 保留。
   - 导出采样率选项："与源文件相同"（默认）、44.1、48、96 kHz。
   - 重采样使用 soxr VHQ（ffmpeg 用 `aresample=resampler=soxr:precision=28`，或者等效做法；先检查本机 ffmpeg 是否带 soxr，没有就明确报错或降级并写进报告）。
   - 16 bit 输出加 TPDF 抖动。
   - 导出时保留元数据和封面。
   - GUI、CLI（`--format flac --sample-rate source|44100|48000|96000 --bit-depth 16|24|32f`）、API 三处一致。
2. **时间线测试**：输入为 44.1／88.2／96／192 kHz 的 FLAC 或 WAV，经路线 V 处理后，以"与源文件相同"的采样率导出；回读的样本数与源文件的差 ≤ 1，冲激峰值的位置误差 ≤ 1 个源采样。报告中记录采样率链路（输入 → 处理 → 输出）。
3. **默认预设从 TOML 读取**：去掉 `catalog.py` 中硬编码的 `DEFAULT_PRESET_ID = "V2"`，改为读取 `presets/route_v.toml` 中的默认字段（Cursor 正在把它设为 V3，字段名以 Cursor 在 TOML 头部的说明为准；字段不存在时退回到 TOML 中的第一个预设，并给出警告）。
4. **复核导出响度**：P6 中 real_music 导出时响度从 −28.0 变到 −25.0 LUFS。确认应用层在桩模式下没有加任何增益（桩模式下的输出必须与输入逐位一致，只允许封装和重采样带来的差异），并在报告中说明这 +3 LU 来自引擎的空间处理。
5. 文档：更新 `docs/user/` 中的格式支持和采样率说明，并写明"内部按 48 kHz 处理，超过 24 kHz 的内容不做空间处理"。
6. 报告写在 `qa/p6/codex/REPORT-02.md`。

## 规则

与 P6-codex-01 相同。只在本分支提交，不要推送，提交信息末尾加 `Agent: Codex`。
