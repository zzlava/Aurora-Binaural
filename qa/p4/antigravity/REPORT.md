# P4-codex-01 执行报告

分支：`agent/codex/P4-01`。仅修改任务允许的目录；不推送。

## F-P3-1

- 清理精确匹配的旧尝试目录（含空目录、部分文件），由租约恢复先隔离旧尝试。
- 当前 running 尝试、符号链接、其他命名目录受保护；在事务内检查，失败下轮重试。
- `uv run ruff check src/aurora_binaural/storage.py src/aurora_binaural/worker tests/app/test_aurora_p4_cleanup.py --fix` → 0。
- `uv run pytest tests/app/test_aurora_p4_cleanup.py tests/app/test_aurora_p3.py -k 'cleanup or sigkill'` → 0，4 passed。含真实 Worker SIGKILL 后重试且无残留目录。

后续 GUI、试听与最终验收记录将在各功能完成时补充。

## 试听 API 与 Worker

- `POST /v1/previews` 复用持久队列、幂等、租约、取消和重试；`GET /v1/jobs/{id}/original` 提供原声，`result` 提供效果，最长 20 秒。
- 从素材原点推进核心，丢弃前缀；只保留选定区间。两侧 stereo / 48 kHz / float32 WAV，按 BS.1770 匹配并回读验证长度与 ≤0.2 LU 差；真峰值预留 1 dB。静音明确标记。
- 报告包含原始位置、实际长度、两侧响度与增益；SADIE II D1 标为配置目标，桩并未应用 HRTF。
- 原声与效果 / 报告同一次租约交付，错误回滚，过期清理一并回收。
- 新 OpenAPI 快照位于本目录，P3 合同测试升级引用它，历史 P3 文件未改。
- `uv run ruff check src tests/app/test_aurora_p4_previews.py` → 0。
- `uv run mypy src tests/app/test_aurora_p4_previews.py` → 0。
- `uv run pytest tests/app/test_aurora_p3.py tests/app/test_aurora_p4_previews.py` → 0，17 passed。
- 首次联合回归曾因 P3 固定 OpenAPI 快照不含新增合同而失败（1 failed / 16 passed）；迁移到 P4 快照后通过。
