# Aurora Binaural Web GUI

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Vite + TypeScript + Preact，npm（本机无 pnpm），FastAPI 提供构建后的本地静态资源。

## Product Purpose
导入一个或多个音频，选择基础预设并调节五个空间控件，生成最长 20 秒的 BS.1770 等响度 A/B 试听，再导出完整音频；可保存和复用“我的预设”。

## Operating Context
依据 P4-codex-01 明确任务书实现。中文操作界面，本地 / 远程 API 切换，桌面与 ≤480px 窄屏，浅色 / 深色。使用者进一步细分尚未指定；本次按音频处理工作台组织。

## Capabilities and Constraints
- 默认真实 Rust 路线 V 与 SADIE II D1，默认预设和强度读取 TOML；界面显示路线、预设、HRTF 和已安装核心版本。仅显式桩模式显示直通桩，桩未实际应用 HRTF。
- 支持 FLAC/WAV/ALAC/AIFF/AAC/MP3；导出默认与源文件同采样率，内部 48 kHz，16 bit 使用 TPDF 抖动。
- 试听按原始时间推进同一核心、预设版本和 seed。
- 高级面板按空间强度、宽度、头顶感、房间感、人声距离显示百分比滑块；默认值来自 TOML。松手 300 ms 后自动刷新当前试听，保留 A/B 播放进度、状态与选中侧。
- 我的预设把基础预设版本和五个控件保存于 SQLite，支持命名、列表、重命名、删除及 JSON 往返；试听、导出和批处理共用。由本地 GUI 启动时，即使远程渲染也保存于本机。
- API Key 仅保留在 sessionStorage / 运行时内存；不放 URL、日志或持久存储。
- 开发者 JSON 与日志默认折叠；队列支持进度、取消、错误、报告、下载。
- 所有资源本地打包，无 CDN；不发布或推送。
