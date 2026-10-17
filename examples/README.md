# 集成示例

从仓库根目录运行；目录名带空格也受支持。

## HTTP Python 客户端

Python 3.12+ 标准库即可，不依赖应用安装。先设置 `AURORA_API_KEY` 为服务的 Key：

```bash
python3 examples/python_client.py data/assets/holdout_electronic.wav \
  --server http://127.0.0.1:8000 --output outputs/client.wav \
  --state outputs/client-state.json --require-rust
```

流程：流式原始上传（带 SHA-256）→ 持久保存 asset_id → 使用持久化 Idempotency-Key 创建任务 → 有界轮询 → 获取报告 → 临时文件下载 → 校验摘要与字节数 → 原子替换输出。stdout 为成功 JSON，进度和错误在 stderr。退出码 0 成功、1 失败、2 参数错误、130 中断。

默认请求超时 30 秒，整个流程 300 秒，每次请求最多额外重试 3 次，指数退避；对 429 和可重试 500/502/503/504 重试，拒绝重定向。`--timeout`、`--request-timeout`、`--retries`、`--poll-interval` 可调整。下载损坏时不会交付或替换原文件。

默认另存 `OUTPUT.report.json` 和 `OUTPUT.state.json`，可通过 `--report`、`--state` 覆盖路径。中断后使用相同输入、参数、服务和 state 重跑；超时／Ctrl-C 不取消服务器任务。下载已完成时重跑需 `--overwrite`，会复用原 job。state 不保存 Key，但含任务／素材 ID，按私有数据保存；同一个 state 只供一个客户端进程写入。

上传本身没有服务端幂等保证，上传响应丢失可能产生额外素材；任务创建响应丢失后会用同一请求和 key 重放。不要删除 state 再用旧 `--idempotency-key` 重新上传，那会改变 asset_id 并触发 409。410 需要新 state、新上传和新 key。

## AI 视频管线

```bash
# 可重现合成测试：6 秒、25 fps、1/3/5 秒闪光与音频点击。
bash examples/ai_video_pipeline.sh --demo outputs/video-demo
# 自己生成的 AI 配音／音乐，已用静音补齐到视频长度、起点为 0：
bash examples/ai_video_pipeline.sh "input video.mp4" "AI 配音.wav" outputs/ai-video
# 通过 API 运行相同流程：
bash examples/ai_video_pipeline.sh --demo outputs/remote-video --server http://127.0.0.1:8000
```

本地模式需先按 [快速开始](../docs/user/quickstart.md) 安装原生核心，默认强制 Rust + room。API 模式调用上述 Python 客户端并要求真实 Rust 报告。示例不调用 AI 生成服务，demo 音频是可重现的合成替身；实际模式接受你的 AI 音频文件。

输出包括 input-48k.wav、spatial.wav、aurora-report.json、final.mov、sync.json。视频流直接复制，音轨为 48 kHz／24-bit PCM；使用 MOV 以避免额外引入 AAC 编码延时。脚本要求新的空输出目录，拒绝自动截短或拉伸时长；输入视频须从 0 开始，音频长度与视频相差不超过一采样。保持前置静音，不使用 `-shortest`。

sync.json 记录输入／渲染／最终解码样本数、视频帧数、起点 PTS、封装 PCM 最大误差、点击起点偏移和容差。脚本还解码最终视频，实际检出白色闪光帧 25/75/125。demo 的事件容差明确为 **一视频帧（25 fps 时为 1920 样本）**，不是一采样；当前 Rust B 实测三个点击相对源音均提前 102 样本（2.125 ms、0.053125 帧），相对闪光提前 101 样本，封装新增偏移为 0，所有样本数均为 288000。起点采用 −80 dBFS 阈值检测。源到效果的起点偏移保留在报告，不以样本数相同冒充起点精确相同。

脚本将短片音频读入内存作校验，适合接入演示与短素材验证。发布平台需要 MP4/AAC 时，再转码并回读检查；这里的 PCM MOV 证据不覆盖有损转码或每一种播放器。
