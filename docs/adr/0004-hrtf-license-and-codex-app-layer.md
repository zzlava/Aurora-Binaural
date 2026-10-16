# ADR 0004：默认 HRTF 与应用层负责人

日期：2026-10-17
状态：已接受（用户："许可证按照你推荐来；U5 交给 Codex"）

## 决定

1. **默认 HRTF 使用 SADIE II D1（Apache-2.0）**，随产品分发（Docker 镜像、安装包、仓库）。
   - 理由：用户没有排除商用或闭源分发的可能。Apache-2.0 只要求保留许可证和署名，不会让衍生滤波器背上 share-alike 义务。D1 本身也是 KU100 假头，空间线索与 KU100 L2702 同源。
   - 代价：客观测量中 SADIE 的音色比 TH Köln KU100 多偏 1–1.7 dB（见 ACCEPTANCE-cursor-05）。之后调参以 SADIE 为准，把这部分差距补回来。
   - TH Köln KU100 L2702（CC BY-SA 3.0）保留为**可选项**，只用于本地试听和研究，不放进默认分发包。如果以后要分发它，需要单独做许可证评估。
   - 交付前生成第三方许可清单，写明 SADIE II 的 Apache-2.0 全文和署名要求。
2. **应用层（CLI、API、Worker、GUI、部署）改由 Codex 负责**，接替额度用尽的 Antigravity。
   - Codex 在 `.agents/codex/` 克隆中工作，分支名为 `agent/codex/*`，目录所有权与原来的 Antigravity 相同（`src/`、`tests/app/`、`web/`、`deploy/`）。
   - Codex 原来作为 Cursor 后备的角色保持不变。
   - Antigravity 额度恢复后，再决定是否让它回来，届时另记 ADR。
