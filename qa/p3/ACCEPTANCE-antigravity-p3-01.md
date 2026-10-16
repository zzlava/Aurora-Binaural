# 验收记录：P3-antigravity-01（HTTP API、SQLite 任务队列、Worker、容器部署）

验收人：Claude　日期：2026-10-17 01:25（UTC+8）
分支：`agent/antigravity/P3-01` @ `676a913`
结论：**通过（附 1 项小的后续事项）**，合并到 main。

## 独立复现（Linux 虚拟机，git archive 干净快照，ffmpeg 4.4.2）

| 项目 | 结果 |
|---|---|
| pytest／ruff／mypy | 全部通过，退出码 0；20 个源文件 ✅ |
| `/health`、`/ready` | ok；ready 检查 database、worker、hrtf_data ✅ |
| 不带 Key 访问 `/v1/presets` | 401 ✅ |
| 上传 10 分钟 48 kHz 立体声（172 MB） | 返回 asset_id 与探测信息 ✅ |
| 创建任务 | 202 ✅；同一个 Idempotency-Key 加相同请求体返回 202（原任务），加不同请求体返回 409 ✅ |
| 任务运行中对 Worker 执行 `kill -9` | 任务保持 running；新的 Worker 在租约过期后回收任务，第 2 次尝试 succeeded ✅ |
| 下载结果 | 200；ffprobe 显示 pcm_s24le、48000 Hz、2 声道，样本数 28,800,000 = 输入 ✅ |
| 任务报告 | 包含 engine、params、timeline、处理前后响度、耗时等字段 ✅ |
| 容器端到端 | 依据代理在 OrbStack 上的报告，Claude 没有独立复现 |

## 后续事项

- **F-P3-1**：Worker 被 SIGKILL 后，那次尝试的临时目录（空目录）会残留在 `tmp/` 下。清理任务要回收租约已过期的那些尝试的临时目录。
