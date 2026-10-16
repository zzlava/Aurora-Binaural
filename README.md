# Aurora Binaural

把普通单声道／立体声音频处理成耳机可听的双耳空间音频（WAV／M4A／MP3），提供 CLI、Web GUI 与 HTTP API，并为后续实时处理预留路线。

- 方案与任务书：[docs/plan.md](docs/plan.md)
- 决策记录：[docs/adr/](docs/adr/)
- 阶段任务书：[docs/tasks/](docs/tasks/)
- 代理调用方式：[docs/agents.md](docs/agents.md)

## 当前状态

P0-0（仓库与协作准备）进行中。尚无可运行代码。

## 目录所有权

| 目录 | 负责方 |
|---|---|
| `core/` `prototypes/` `presets/` | Cursor |
| `src/` `tests/app/` `web/` `deploy/` | Codex（2026-10-17 起接替 Antigravity，ADR 0004） |
| `qa/` `docs/` 与 Git 操作 | Claude |
| `data/` | Cursor 提交数据，Claude 核验来源、授权与哈希 |

## 分支规则

- `main`：只合并经 Claude 验收的内容，禁止 force push。
- `agent/cursor/<任务编号>`、`agent/antigravity/<任务编号>`：代理工作分支，各用独立 worktree。
- `qa/<任务编号>`：验收脚本与记录。
- 阶段验收通过后打标签 `pN-accepted`。

## 注意

本地文件夹名包含空格（`Aurora Binaural`），所有脚本与构建命令必须对路径加引号。
