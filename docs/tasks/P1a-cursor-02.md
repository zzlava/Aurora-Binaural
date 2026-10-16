# P1a-cursor-02：HRIR pack 改用 Git LFS（小修）

状态：已派发（2026-10-17）
执行方：Cursor　分支：`agent/cursor/P1a-01`（**这个分支还没有推送**，因此允许改写本分支上自己的历史）

## 问题

`data/hrtf/*.ahrp`（11 MB 和 36 MB）是作为普通 Git 对象提交的，没有走 LFS。

## 任务

1. 在 `.gitattributes` 中加入 `*.ahrp filter=lfs diff=lfs merge=lfs -text`。
2. 只对本分支执行 `git lfs migrate import --include="*.ahrp" --include-ref=refs/heads/agent/cursor/P1a-01`。**不要**动 main 或任何其他引用。
3. 确认 `git lfs ls-files` 列出这两个 pack，并且 `git log --stat` 中它们是 LFS 指针。
4. 在 `qa/p1/cursor/REPORT.md` 里追加一小节记录这次操作。不要做其他代码改动。
