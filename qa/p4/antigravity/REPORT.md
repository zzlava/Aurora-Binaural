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

## Web GUI 与本地启动

- Vite 8.3.4、TypeScript 6.0.3、Preact 11.0.1，npm 精确版本与 lockfile（本机无 pnpm）。静态产物不提交，无外部 CDN。
- 中文工作台：多文件导入 / 拖放、三种预设 / 强度、时间区间试听、共享时钟双 AudioBuffer / 8ms 增益切换、Space / A / B、WAV / M4A / MP3 完整导出、队列 / 进度 / 取消 / 错误 / 报告 / 下载。
- `aurora gui` 启动 API 与 Worker，固定回环绑定、随机空闲端口（可指定），就绪后打开浏览器；本地一次性凭据只在内存 / 同源引导接口中传递。
- 远程通过 Bearer header 请求；Key 仅保留在 sessionStorage，不放在地址或日志。显式 CORS origins 支持跨域远程连接。
- `npm --prefix web run build` → 0（含 `tsc --noEmit`）。
- `npm --prefix web run lint` → 0。
- `uv run pytest tests/app/test_aurora_p4_gui.py` → 0，3 passed（Host / Origin / 非回环隔离、静态服务、CORS、CLI 参数）。
- `uv run pytest tests/app/test_aurora_p2.py tests/app/test_aurora_p3.py tests/app/test_aurora_p4_previews.py` → 0，34 passed。
- `npm --prefix web run test:e2e` → 0，4 passed（20.8s）。真实 API + Worker，本地与第二 origin 的远程服务；覆盖完整流程、播放进度同步、快捷键、下载 WAV 读取、错误 Key / Key 存储与日志、运行中取消、多文件 / 批量导出、损坏音频错误。
- 第一轮 E2E 1 passed / 3 failed：人为逐块延时使任务超过默认 5s 断言窗口，后续任务等待前序完成；改为隔离每次测试运行数据，使用 20s 有界状态等待并减少故障注入延时后通过，未通过增加重试掩盖错误。
- 四张实际 full-page 截图：`screenshots/desktop-light.png`、`desktop-dark.png`（1440px）与 `narrow-light.png`、`narrow-dark.png`（390px），均已打开检查，无横向溢出。机械设计检查记录在 `design-detector.json`。
- 初次脚手架时有一次 CSS 写入路径错误导致 build 返回 1；修正后构建通过。lint 在 tests/ 建立之前曾返回 2；测试目录完成后通过。
