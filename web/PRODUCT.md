# Aurora Binaural Web GUI

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Vite + TypeScript + Preact，npm（本机无 pnpm），FastAPI 提供构建后的本地静态资源。

## Product Purpose
导入一个或多个音频，选择预设和强度，生成最长 20 秒的 BS.1770 等响度 A/B 试听，再导出完整音频。

## Operating Context
依据 P4-codex-01 明确任务书实现。中文操作界面，本地 / 远程 API 切换，桌面与 ≤480px 窄屏，浅色 / 深色。使用者进一步细分尚未指定；本次按音频处理工作台组织。

## Capabilities and Constraints
- 默认真实 Rust 路线 V/V2 与 SADIE II D1；界面显示路线、预设、HRTF 和已安装核心版本。仅显式桩模式显示直通桩，桩未实际应用 HRTF。
- 试听按原始时间推进同一核心、预设版本和 seed。
- API Key 仅保留在 sessionStorage / 运行时内存；不放 URL、日志或持久存储。
- 开发者 JSON 与日志默认折叠；队列支持进度、取消、错误、报告、下载。
- 所有资源本地打包，无 CDN；不发布或推送。
