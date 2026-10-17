# P6-codex-02：FLAC／高解析度与 TOML 默认预设

日期：2026-10-17；执行：Codex；分支：`agent/codex/P6-02`；基线：`ab1a081`。

**结论：应用层实现、格式回读、默认预设、响度与 GUI 回归完成；整单尚未通过严格时间线验收。** 真实路线 V 的样本数差全部为 0，但冲激峰值提前 12／24／28／55 个源采样，超过本次 ±1 要求。直接调用核心也复现峰值提前；需要核心负责人修复后重跑，不应把应用测试通过当作本项通过。未修改 `core/`、`prototypes/`、项目 `presets/`、`tools/` 或 `qa/tools/`，未推送。

## 1. 格式、采样率、位深与元数据

GUI、CLI、API/Worker 共用 OutputSpec 与编码管线：

| 格式／format | 封装／扩展名 | 位深／编码 |
|---|---|---|
| FLAC／flac | FLAC／.flac | 16、24 bit |
| WAV／wav | RIFF/RF64 WAVE／.wav | 16、24 bit、32f；兼容原来的数值 32 |
| ALAC／alac | M4A／.m4a | 16、24 bit；CLI 必须显式 `--format alac` |
| AIFF／aiff | AIFF／.aiff；输入也接受 .aif | 16、24 bit |
| AAC-LC／m4a | M4A／.m4a | 保留，默认 256 kbps；裸 .aac 可输入 |
| MP3／mp3 | MP3／.mp3 | 保留，默认 256 kbps |

输入支持 44.1／48／88.2／96／176.4／192 kHz。导出默认 `source`（与源文件相同），另可选 44100／48000／96000。MP3 最高 48 kHz、AAC 最高 96 kHz，source 超出能力时明确失败，不用其他采样率替代请求。ALAC 的下载扩展名与 MIME 均按 M4A；FLAC 为 audio/flac，AIFF 为 audio/aiff。batch、compare、远程 convert 同样使用新的格式和源采样率解析。

16 bit 最后量化时使用 `aresample=osf=s16p:dither_method=triangular`，在重采样和长度取整修正之后加 TPDF；24 bit 默认不加，32f 不加。本机 FFmpeg 9 的 packed s16 路径在零输入测试中出现 -1/0 LSB 偏置，改用 planar s16 后读回 -1/0/+1 LSB，概率约为 1/8、3/4、1/8；planar 到 packed 的整数格式转换不改样本值。测试直接检查输出 PCM 分布。

标题、艺术家、专辑和封面通过 11 种格式／位深组合回读；FLAC、ALAC、AIFF、AAC、MP3 映射标签和 attached_pic。WAV 的文字标签由 FFmpeg 映射，封面写入标准 RIFF `id3 `/ID3v2.3 APIC 块；播放器是否展示 WAV 封面取决于其支持情况。RF64 使用 ds64 长度更新。转换仍先写临时文件，完成封面写入和回读后再原子交付。

实现和回读用例：[test_aurora_p6_formats.py](../../../tests/app/test_aurora_p6_formats.py)。

## 2. soxr 环境

- PATH 中 `/opt/homebrew/bin/ffmpeg`：9.0.2，未编入 libsoxr；实际试跑报 `Requested resampling engine is unavailable`，仅查看 filter help 无法判定能力。
- 本机已有 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`：9.0.1，带 `--enable-libsoxr`，实际试跑成功。本次重采样和完整测试显式使用此二进制，没有安装或替换系统 FFmpeg。
- 两个采样率边界都使用 `aresample=<目标率>:resampler=soxr:precision=28`；没有较低质量降级。
- 可设置 `AURORA_FFMPEG`、`AURORA_FFPROBE`。缺少 soxr 时返回 `SOXR_UNAVAILABLE`、CLI 退出码 4，保留操作说明，见[实际错误 JSON](soxr-unavailable-02.json)。

```bash
export AURORA_FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
# ffprobe 不在 PATH 时可另外指定 AURORA_FFPROBE
```

**内部按 48 kHz 处理，超过 24 kHz 的内容不做空间处理。** 真实 Rust 路线的内部数据为 float32，高采样率输入降采样后处理，导出再升至目标率；24 kHz 以上的内容会被滤掉。source 导出不代表原生高解析度空间处理。桩为诊断直通路径，可在目标采样率工作以避免无意义的往返重采样。

## 3. 时间线：样本数通过，空间冲激峰值未通过

[可复现脚本](verify_02.py)、[完整证据](evidence-02.json)、[应用回归用例](../../../tests/app/test_aurora_p6_timeline.py)。每个采样率分别使用 FLAC 24 bit 和 WAV 32f，输入长度为 2 秒加 17 个源采样，双声道同相单冲激，V2、强度 0.5，tail=trim，默认末尾淡出。下表各行的 FLAC 与 WAV 结果相同：

| 输入 → 处理 → 输出 Hz | 源样本数 | 实际处理帧数 | 输出样本数 | 样本数差 | 冲激源位置 → 输出位置 | 峰值误差（源采样） |
|---|---:|---:|---:|---:|---|---:|
| 44100 → 48000 → 44100 | 88217 | 96019 | 88217 | 0 | 22067 → 22055 | -12 |
| 88200 → 48000 → 88200 | 176417 | 96009 | 176417 | 0 | 44117 → 44093 | -24 |
| 96000 → 48000 → 96000 | 192017 | 96009 | 192017 | 0 | 48017 → 47989 | -28 |
| 192000 → 48000 → 192000 | 384017 | 96004 | 384017 | 0 | 96017 → 95962 | -55 |

输出长度锚定 `round(源样本数 × 输出采样率 / 源采样率)`。48 kHz 中间帧数先取整，再升采样可能多/少一个或更多高采样率帧；在末端按源时长补零／裁切，96 kHz 本例修正 -1，192 kHz 修正 +1，不裁掉起点静音。报告新增实际 processing_input_samples、processing_output_samples、resampler、duration_rounding_adjustment_samples，可追查这一步；不能将两轮取整后的长度误当源长度。

对照证据：

- 相同的 8 个 FLAC/WAV 文件走真实 Rust V、strength=0 的旁路，仍经过 48 kHz 和相同的 soxr 两个边界：样本数差 0，峰值位置误差全部 0。该对照只证明重采样时间原点，不冒充空间处理验收。
- 独立直接调用 RustRenderer，48 kHz 冲激位置 24017；只移除核心报告的 1229 帧延时，不用 RendererStreamProcessor，峰值为 24003，仍提前 14 帧。与[此前核心验收中的 V 峰值提前约 13 帧](../../p1/ACCEPTANCE-cursor-p1a-05.md)相符。
- 因而严格空间冲激条件是 **8/8 未通过**，并非重采样产生整体移位。应用遵循核心报告的延时，未添加经验性的 13/14 帧偏移，也未放宽本次 ±1 阈值。
- `timeline.sync_within_tolerance` 核对样本数；报告/schema 已明确它不是冲激峰值检测，residual_priming_shift_samples 也只是基于帧数的 codec 估计。

```bash
AURORA_FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
  .venv/bin/python qa/p6/codex/verify_02.py
# 当前退出码为 1，evidence-02.json 的 strict_spatial_timeline_pass=false。
```

需要核心负责人解决非旁路 V 的峰值位置／延时定义后，以此脚本重新核对；本报告不将旧验收允许的约 0.27 ms 偏移用于豁免新任务的 ±1 源采样要求。

## 4. TOML 默认预设

移除了 catalog.py 的硬编码 V2，按 Cursor TOML 头部说明读取根字段 `default_preset`，继续从预设行读取 default_strength/name/description。字段缺失时以该文件的插入顺序选择首个命名预设并发 RuntimeWarning；无效预设引用明确报错。CLI、API、GUI、构建烟测和独立客户端不再写死产品默认 V2。旧 GUI 测试的默认选择断言改为读取服务端目录；其后仍可显式选择 V2 复核历史音乐。

本分支基线中的项目 TOML 尚无该字段，所以当前默认为首项 V2，警告符合要求。没有修改项目 presets，也没有把其他分支合入本分支。

Cursor 已将配置提交在 `52fa04e`。使用该提交的 route_v.toml 只读快照，在允许的 QA runtime 目录构造独立预设目录，通过 AURORA_PRESETS_DIR 实际运行 catalog 和 Rust convert，确认默认 **V3／strength=1.0／route V／真实核心**。见[固定提交的集成证据](default-integration-02.json)，其中记录来源提交、TOML SHA-256 和应用提交。该配置合入后重启进程即可生效。当前 Cursor 工作目录已切换到 P1b，测试不依赖其可变工作区。

## 5. real_music 响度与逐位直通

复用先前 P6 的显式 V2、strength=0.5，普通 convert、48 kHz、32f 导出，无等响度匹配：

| 路径 | Integrated LUFS | True Peak dBTP | 证据 |
|---|---:|---:|---|
| 输入 | -28.0 | -10.2 | 原文件解码与 BS.1770 |
| 桩输出 | -28.0 | -10.2 | 所有 PCM 字节与输入完全一致，含末尾 |
| 直接 Rust 核心 | -25.0 | -9.4 | 不经过 RendererStreamProcessor，不加编码增益 |
| 应用 + Rust 导出 | -25.0 | -9.4 | 淡出前 959520 帧与直接核心逐位一致 |

输入与桩输出解码 PCM SHA-256 均为 `174ea367e7ab2b4f1304e1bb2ddfbc21ace139cf2c4ffabfd8970850b214380e`。**+3.0 LU 来自引擎的空间处理，应用普通导出增益为 0.0 dB。** 空间路径最后 480 帧是显式的 10 ms trim 淡出，直接核心对比单独排除这 480 帧；实测不改变本文件的上述响度结果。

修复了普通桩也被默认末尾淡出修改的问题；桩和 strength=0 现在不淡出，DelayTestRenderer 和非旁路空间路径保留原尾音策略。另用 WAV32f、FLAC24、AIFF24、ALAC24，在 48/96/192 kHz 的同采样率桩路径回读检查逐位一致。位深降低及 16 bit 强制 TPDF 属于编码量化，会改变最低有效位；逐位无增益证据选择无需这些量化改变的路径。比较的是解码 PCM，封装头和标签不参与字节比较。

试听和 compare 的等响度匹配继续显式记录 gain，不计作普通导出增益。

## 6. GUI 与文档

真实 GUI 导入 96 kHz FLAC，经 Rust 空间处理后下载并用 Python/ffprobe 回读：

| GUI 选择 | 实际编码 | 采样率 Hz | 位深 | 样本数 |
|---|---|---:|---:|---:|
| FLAC／source | flac | 96000 | 24 | 192017 |
| ALAC／44100 | alac，M4A | 44100 | 24 | 88208 |
| AIFF／48000 | pcm_s16be | 48000 | 16 | 96008 |
| WAV／96000／32f | pcm_f32le | 96000 | 32 | 192017 |

[请求与下载回读](gui-evidence-02.json)、[下载文件](downloads-02/)、[桌面截图](screenshots/hires-desktop.png)、[窄屏截图](screenshots/hires-narrow.png)。两张均已实际查看；格式、采样率和位深控件在窄屏正常显示。新增 [hires-workflow.spec.ts](../../../web/tests/hires-workflow.spec.ts) 运行真实服务、上传、提交、下载和回读，不 mock 处理结果。

更新 docs/user/quickstart.md、cli.md、api.md、troubleshooting.md、deploy.md，含输入输出格式、source 默认、位深、元数据、soxr 配置和 48 kHz 内部处理边界。故障排查也修正“峰值对齐”的过度表述，明确当前 V 的严格偏移限制。

## 7. 最终验证与交付边界

| 检查 | 结果 |
|---|---|
| `AURORA_FFMPEG=... .venv/bin/pytest -ra tests/app` | **148 passed，1 skipped**；跳过项是新版 V binding 下不适用的旧 binding 兼容测试，不是本次音频检查 |
| Ruff（src、tests/app、独立客户端） | 通过 |
| mypy（src、tests/app） | 35 个文件通过 |
| `npm --prefix web run build`（含 tsc）／lint | 通过 |
| Rust Playwright | 2 passed，0 flaky；[结果](rust-e2e-results-02.json) |
| Stub Playwright | 4 passed，0 flaky；[结果](stub-e2e-results-02.json) |
| 严格时间线脚本 | **退出 1：完整 V 峰值条件未通过**；源样本数全部通过，旁路控制全部通过 |
| `git diff --check` | 通过 |

P6-01 的既有音频、JSON 和截图保留原字节，本次证据另以 -02 命名；旧音乐 GUI 回归的生成目录改为被忽略的 QA runtime。OpenAPI 更新到本次参数与报告 schema。所有实现按子项在本分支提交，提交末尾使用 `Agent: Codex`；未推送、未改 Rust 核心、未合并 Cursor 的 TOML。

**后续唯一严格验收阻碍：路线 V 非旁路冲激峰值仍超出 ±1 源采样。** 本报告的测试通过结论仅适用于已列出的应用层行为；不能据此宣布 P6-02 全项验收通过。
