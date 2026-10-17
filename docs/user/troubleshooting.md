# 故障排查

优先记录命令退出码、JSON 的 error.code、job_id、引擎版本、FFmpeg 版本和报告。以下命令均从仓库根目录执行。

| 现象 | 检查与处理 |
|---|---|
| 找不到 ffmpeg/ffprobe | 运行 `bash scripts/setup.sh`；应用自动读取仓库的 `.aurora-local.env`，或依次探测 PATH、`/opt/homebrew/opt/ffmpeg-full/bin`，无需手动 source。显式的 AURORA_FFMPEG/AURORA_FFPROBE 优先，失效时需修正或取消设置 |
| SOXR_UNAVAILABLE | 当前 FFmpeg 无法实际运行 soxr；macOS 执行 `brew install ffmpeg-full` 后重跑 setup.sh 或重启应用；Linux 安装带 --enable-libsoxr 的 FFmpeg。显式 AURORA_FFMPEG 不会被自动替换，不使用低质量重采样替代 |
| route_v.toml has no default_preset 警告 | 当前 TOML 尚无根字段，默认退回该文件第一个预设；合入 Cursor 的 default_preset 配置并重启后自动采用指定值 |
| M4A 样本数多出一帧／AAC 延时 | 使用 FFmpeg 7.1+，优先 WAV 48 kHz／24 bit；检查 timeline.sample_diff、codec_priming_samples、codec_trailing_padding_samples、residual_priming_shift_samples 和 sync_warning |
| FFmpeg 旧版仍显示成功 | 默认低于 7 只警告；`--strict-ffmpeg` 或服务端 AURORA_STRICT_FFMPEG=1 可拒绝，最终以回读报告为准 |
| 裸 .aac 无法输出 | 使用 `.m4a`（AAC-LC + MP4 容器）；裸 ADTS 无精确 gapless 元数据 |
| CORE_NOT_INSTALLED / import aurora_core 失败 | 用当前仓库的 `bash scripts/build-core.sh`，确认 `.venv/bin/python` 导入成功；`uv sync` 后可能需要重建；不要用 stub 代替真实验收 |
| CORE_ROUTE_UNSUPPORTED，默认转换失败 | 安装的是旧核心，重新运行当前仓库的 `bash scripts/build-core.sh` 并重启 GUI／API／Worker；TOML 默认预设需要支持路线 V 的核心。确需经典路线 B 时显式 `--preset room` |
| HRIR/SOFA 哈希或数据错误 | 检查 AURORA_HRTF_DIR、数据文件和分发许可证；恢复同版本 SADIE II D1 的 SOFA/pack，不要混用自制或其他来源 pack |
| GUI_BUILD_MISSING | `npm --prefix web ci` 后 `npm --prefix web run build`；或设置 AURORA_WEB_DIR 指向含 index.html 的 dist |
| Address already in use / 端口占用 | `lsof -nP -iTCP:8765 -sTCP:LISTEN` 查进程；GUI 省略端口自动选择，API 换 `--port`，Compose 改 AURORA_PORT 后 up；不要盲目杀进程 |
| 401 UNAUTHORIZED | 检查客户端与服务的 AURORA_API_KEY 一致、无首尾空格；Compose 使用显式 `--env-file deploy/.env`；本地 GUI 使用临时会话 Key |
| health 正常、ready=503 | 查看 checks；确认 Worker 运行、共享同一 DB/volume、SOFA 非空；查看 api/worker 日志 |
| ready=200 但任务失败 | ready 不检测引擎／预设兼容；查询任务 error 并检查原生扩展、所选预设与 HRTF 哈希 |
| 任务一直 queued | 启动 `aurora worker`；确认 API 和 Worker 的 AURORA_DATA_DIR/AURORA_DB_PATH 相同，心跳新鲜；容器看 compose ps/logs |
| 运行中重启后还在 running | 等待租约过期和 Worker 回收；不要手改数据库；达到重试上限会失败 |
| 409 IDEMPOTENCY_KEY_CONFLICT | 相同 key 必须复用相同 asset_id 和参数；真正的新操作使用新 key；恢复客户端复用 state 文件 |
| 410 结果过期 | 使用新上传和新 key 重建；状态 succeeded 不保证下载尚未过期；及时归档音频与报告 |
| OUTPUT_ALREADY_EXISTS / REPORT_ALREADY_EXISTS | 换新路径或明确加 `--overwrite`；示例脚本默认也不覆盖 |
| source 导出 MP3/AAC 参数错误 | MP3 最高 48 kHz、AAC 最高 96 kHz；显式降低导出采样率，或改用 FLAC/WAV/ALAC/AIFF |
| 浏览器跨域失败 | 服务端 AURORA_CORS_ORIGINS 填准确 scheme/host/port，重启 API；本地 CLI 和 Python 客户端不受浏览器 CORS 限制 |
| 真峰值高于 −1 dBTP | 先构建最新核心，保留实测报告、素材、预设、强度交核心负责人；不能据“任务成功”宣称质量验收通过 |
| 波形起点前有微弱能量 | 路线 V 叠加虚拟扬声器、HRIR 与反射，补偿固定延时后峰值可提前约 14 个 48 kHz 采样（0.29 ms）；约 2 ms 前还可能有低电平预振铃。时间线验收现为“样本数一致、旁路逐位对齐、空间路径峰值偏移 ≤1 ms，并记录偏移”，已撤回 ±1 源采样峰值要求，见 [P6-02 验收定义](../../qa/p6/ACCEPTANCE-codex-p6-02.md)和 [P6-03 实测](../../qa/p6/codex/REPORT-03.md)。旁路原生 48 kHz 输入／输出逐位相同；不同源采样率经过 soxr 往返后幅度会改变，另行核对其峰值位置误差为 0。低阈值检测到的最早能量不等于峰值或口型整体提前 |

内部按 48 kHz 处理，超过 24 kHz 的内容不做空间处理，重采样会滤掉这些超声内容；把导出率设回源采样率不会恢复它们。

## 视频音画同步

推荐先将 AI 配音／音乐解码成从时间 0 开始的 WAV 48 kHz，保持开头静音；`timing.mode=preserve, tail=trim`。音轨原本在视频中有偏移时，应先在素材中补足前置静音，或在外部剪辑中明确保留相同偏移。

不要使用 `silenceremove`、未记录的 `atrim` 或 `-shortest` 隐藏长度误差。`preserve` 尾巴会延长音频，不适合锁定原视频长度的默认流程。AAC/MP3 原始包的 priming/padding 不等于最终播放偏移，需解码最终视频并量样本数／事件起点；不同播放器对 edit-list 的支持也需验证。

运行 `bash examples/ai_video_pipeline.sh --demo outputs/sync-check` 可生成 25 fps 的合成闪光／音频脉冲测试，真实 Rust 转换后合成 MOV，输出 sync.json。中间和最终音轨均为 PCM，用样本数、首个事件位置和视频帧数检验同步；面向发布平台再转 AAC/MP4 后应重新回读验证。该测试证明这条管线的时间线，不替代所有素材／编码组合的质量验收。
