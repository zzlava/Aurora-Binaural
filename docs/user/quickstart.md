# 快速开始

Aurora Binaural 把单声道／双声道音频渲染为耳机播放的双耳立体声文件。输入支持 WAV、M4A、MP3 和 AAC；推荐视频流程使用 WAV 48 kHz／24 bit。需要在源码仓库根目录执行下列命令，路径有空格时始终加引号。

GUI、CLI、API 和 Worker 默认使用真实 Rust **路线 V、预设 V2、SADIE II D1 HRTF**，默认空间强度为 50%。无需指定路线参数，应用会从预设读取路线。路线 B 的 `room` 保留为“经典”，需要时显式选择；核心缺失或版本过旧会报错，不会自动替换成其他预设或桩。

## 本机安装与 Rust 核心

准备 Python 3.12+、uv、Rust/Cargo（本机已验证 1.99.1）、FFmpeg/ffprobe 和 Node/npm。M4A 精确时间线推荐 FFmpeg 7.1+；容器提供 7.1。确认命令可用：

```bash
python3 --version
uv --version
cargo --version
ffmpeg -version
ffprobe -version
node --version
npm --version
```

安装应用、编译并安装原生扩展，构建界面：

```bash
uv sync --locked
bash scripts/build-core.sh
uv run python -c 'import aurora_core; print(aurora_core.__file__)'
npm --prefix web ci
npm --prefix web run build
uv run aurora presets --json
```

构建脚本在临时副本中用 maturin 构建 release wheel，不修改 `core/`，安装后会实际准备路线 V/V2 并处理、刷新一个音频块。看到 `Default core smoke passed` 表示此检查通过。`uv sync` 的精确同步可能移除单独安装的 `aurora_core`，此时重新执行构建脚本。默认 `AURORA_ENGINE=rust`；只有显式 `AURORA_ENGINE=stub` 才运行直通桩，桩不会产生空间效果，报告为 `engine.is_stub=true`。

## 图形界面

```bash
uv run aurora gui
# 固定端口，手动打开日志中的地址：
uv run aurora gui --port 8765 --no-browser
```

启动器绑定 `127.0.0.1` 的空闲端口，启动 API 和 Worker，并打开浏览器。导入自己的音频 → 保持默认 V2 或调整强度 → 生成最多 20 秒试听 → 播放并切换等响度 A/B → 导出完整音频 → 下载。界面会显示 Rust、路线、预设、HRTF 和核心版本；每个任务的摘要记录其实际引擎信息。按 Ctrl-C 退出并停止 Worker。默认数据目录为 `~/Library/Caches/AuroraBinaural`，可设置 `AURORA_DATA_DIR`。刷新页面后可用原 job ID 继续查询服务端任务。

## 命令行转换

```bash
mkdir -p outputs
uv run aurora convert data/assets/real_music.wav \
  --output outputs/music-spatial.wav \
  --sample-rate 48000 --bit-depth 24 --tail trim \
  --report outputs/music-report.json
uv run aurora inspect outputs/music-spatial.wav --json
uv run aurora compare data/assets/real_music.wav --output-dir outputs/ab
# 换成自己的路径（包含空格时用引号）：
uv run aurora convert "/absolute/path/my audio.wav" --output outputs/my-spatial.wav
# 需要经典路线 B 时：
uv run aurora convert data/assets/real_music.wav --preset room --output outputs/classic.wav
```

默认不覆盖；确需覆盖加 `--overwrite`。`convert` 的 stdout 是完整 JSON 报告，进度写 stderr。检查 `engine.is_stub=false`、`engine.route="V"`、`params.preset.id="V2"`、`timeline.sample_diff=0` 和 `timeline.sync_within_tolerance=true`，以及 `loudness_after.true_peak_dbtp`。文件转换成功与质量指标分别以实际报告为准。

## 连接服务与视频流程

按 [部署文档](deploy.md) 启动服务，在宿主机设置同一 `AURORA_API_KEY`：

```bash
uv run aurora convert data/assets/synth_voice_mono.wav \
  --server http://127.0.0.1:8000 --output outputs/remote.wav
python3 examples/python_client.py data/assets/synth_voice_mono.wav \
  --server http://127.0.0.1:8000 --output outputs/client.wav \
  --state outputs/client-state.json
bash examples/ai_video_pipeline.sh --demo outputs/video-demo
```

Python 示例会校验上传与下载 SHA-256；state 用于中断后的任务恢复。视频示例提供合成音画测试，也可接入自己的 AI 配音／音乐。详见 [CLI](cli.md)、[API](api.md) 和 [故障排查](troubleshooting.md)。
