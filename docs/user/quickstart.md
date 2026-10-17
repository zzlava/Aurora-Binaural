# 快速开始

Aurora Binaural 把单声道／双声道音频渲染为耳机播放的双耳立体声文件。输入支持 FLAC、WAV、ALAC（M4A）、AIFF、AAC 和 MP3；推荐视频流程使用 WAV 48 kHz／24 bit。需要在源码仓库根目录执行下列命令，路径有空格时始终加引号。

GUI、CLI、API 和 Worker 默认使用真实 Rust **路线 V、SADIE II D1 HRTF**；默认预设由 `presets/route_v.toml` 根字段 `default_preset` 决定，强度由该预设的 `default_strength` 决定（省略时为 50%）。无需指定路线参数，应用会从预设读取路线。路线 B 的 `room` 保留为“经典”，需要时显式选择；核心缺失或版本过旧会报错，不会自动替换成其他预设或桩。

## 本机安装与 Rust 核心

准备带 libsoxr 的 FFmpeg，以及 Python 3.12+、uv、Rust/Cargo（本机已验证 1.99.1）、FFmpeg/ffprobe 和 Node/npm。M4A 精确时间线推荐 FFmpeg 7.1+；容器提供 7.1。确认命令可用：

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

构建脚本在临时副本中用 maturin 构建 release wheel，不修改 `core/`，安装后会实际准备 TOML 指定的路线 V 默认预设 并处理、刷新一个音频块。看到 `Default core smoke passed` 表示此检查通过。`uv sync` 的精确同步可能移除单独安装的 `aurora_core`，此时重新执行构建脚本。默认 `AURORA_ENGINE=rust`；只有显式 `AURORA_ENGINE=stub` 才运行直通桩，桩不会产生空间效果，报告为 `engine.is_stub=true`。

## 格式、采样率与位深

输入支持单声道或立体声，采样率包括 44.1、48、88.2、96、176.4、192 kHz。GUI、CLI 和 API 的导出规则相同：

| 格式 | 扩展名／`format` | 位深 |
|---|---|---|
| FLAC | `.flac`／`flac` | 16、24 bit |
| WAV | `.wav`／`wav` | 16、24 bit、32 bit float（`32f`） |
| ALAC | `.m4a`／`alac` | 16、24 bit |
| AIFF | `.aiff` 或输入 `.aif`／`aiff` | 16、24 bit |
| AAC-LC | `.m4a`／`m4a` | 有损，默认 256 kbps |
| MP3 | `.mp3`／`mp3` | 有损，默认 256 kbps |

导出采样率默认“与源文件相同”（`source`），也可选择 44.1、48、96 kHz。MP3 最高 48 kHz，AAC 最高 96 kHz；更高采样率源文件应选无损格式或显式降低导出采样率，应用不会静默改变选择。无损格式默认 24 bit；16 bit 加 TPDF 抖动，24 bit 和 float 默认不加。裸 `.aac` 可输入，AAC 导出使用 M4A 以保留 gapless 信息；`.m4a` 文件同时可能是 AAC 或 ALAC，导出 ALAC 时须显式指定 `--format alac`。

**内部按 48 kHz 处理，超过 24 kHz 的内容不做空间处理**。内部为 32 bit float；高采样率输入先经 soxr VHQ 降到 48 kHz，导出再升到目标采样率，24 kHz 以上的内容会被滤掉。选择源采样率不代表原生高解析度空间处理。普通导出不加应用增益；试听／compare 为等响度 A/B 会单独记录匹配增益。桩模式在无需重采样和位深转换时保持解码 PCM 逐位一致，包括末尾。

重采样必须使用 `aresample=resampler=soxr:precision=28`；FFmpeg 不支持时返回 `SOXR_UNAVAILABLE`。本机 Homebrew 普通版可能不带 soxr，若已安装完整版本，可显式选择：

```bash
export AURORA_FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
# 可选：ffprobe 不在 PATH 时也显式指定
export AURORA_FFPROBE=/opt/homebrew/opt/ffmpeg-full/bin/ffprobe
```

导出保留标题、艺术家、专辑和嵌入封面。WAV 的封面写入 RIFF ID3/APIC 块；不同播放器对 WAV 封面的展示支持可能不同。标签按目标容器支持映射。

```bash
uv run aurora convert "input.flac" --output outputs/spatial.flac \
  --format flac --sample-rate source --bit-depth 24
uv run aurora convert "input.aiff" --output outputs/spatial.m4a \
  --format alac --sample-rate 96000 --bit-depth 16
uv run aurora convert "input.flac" --output outputs/spatial.wav --bit-depth 32f
```

## 图形界面

```bash
uv run aurora gui
# 固定端口，手动打开日志中的地址：
uv run aurora gui --port 8765 --no-browser
```

启动器绑定 `127.0.0.1` 的空闲端口，启动 API 和 Worker，并打开浏览器。导入自己的音频 → 保持 TOML 默认预设或调整强度 → 生成最多 20 秒试听 → 播放并切换等响度 A/B → 导出完整音频 → 下载。界面会显示 Rust、路线、预设、HRTF 和核心版本；每个任务的摘要记录其实际引擎信息。按 Ctrl-C 退出并停止 Worker。默认数据目录为 `~/Library/Caches/AuroraBinaural`，可设置 `AURORA_DATA_DIR`。刷新页面后可用原 job ID 继续查询服务端任务。

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

默认不覆盖；确需覆盖加 `--overwrite`。`convert` 的 stdout 是完整 JSON 报告，进度写 stderr。检查 `engine.is_stub=false`、`engine.route="V"`、`params.preset.id` 与 `aurora presets` 返回的 `default_preset` 一致、`timeline.sample_diff=0` 和 `timeline.sync_within_tolerance=true`，以及 `loudness_after.true_peak_dbtp`。文件转换成功与质量指标分别以实际报告为准。

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
