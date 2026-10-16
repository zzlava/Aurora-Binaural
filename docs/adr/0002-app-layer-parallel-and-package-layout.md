# ADR 0002：应用层与 P0 听感关并行；Python 包布局

日期：2026-10-16
状态：已接受（Claude 决定；用户已授权非确认类事项由 Claude 决定）

## 决定

1. **并行推进**：P0 听感关只决定渲染算法、HRTF 和预设，CLI、API、Worker 这些应用层功能不依赖它的结论。因此 P2（CLI）在 P0 听感关之前开始，接一个"桩核心"开发。
   - 门槛仍然有效：P1 Rust 核心、预设定版、GUI 的效果试听，都要等听感关通过之后才开始。
2. **包布局**：应用层统一放到 `src/aurora_binaural/`，下面分 `cli/`、`api/`、`worker/`、`media/`、`params.py` 等子模块。原来顶层的 `cli/`、`api/`、`worker/` 目录迁移过去，测试放在 `tests/app/`。
   - 目录所有权：`src/`、`tests/app/`、`web/`、`deploy/` 归 Antigravity。
3. **核心接口**：应用层只能通过 `aurora_binaural.engine.Renderer` 协议调用核心，协议签名对齐 plan 第 7 节，并采纳 Cursor 在 P0 提出的 `flush()` 建议。P1 的 Rust 绑定实现同一个协议。

## 原因

- 避免应用层空等 P0 的迭代。
- 解决 P0 遗留的 F-AG-3：API 和 Worker 跨目录引用 `cli/aurora_binaural`。
