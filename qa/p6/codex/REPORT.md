# P6-codex-01 应用层交付报告

执行：Codex；日期：2026-10-17（Asia/Shanghai）；分支：`agent/codex/P6-01`。
基线：`e71c1e15d022865b275165952ec5779c68a9f3ff`。本次约 10:54–11:03，低于 120 分钟上限。

结果：GUI、CLI、API、Worker 默认使用真实 Rust 路线 V／V2／SADIE II D1，经典 `room` 路线 B 保留。两份指定音乐的真实 GUI 试听、A/B 播放、完整 WAV 导出、浏览器下载及独立回读全部通过；20 分钟转换通过。每个完成的任务子项均已单独提交，没有推送。

## 1. 本机构建与默认入口

已实际执行 `bash scripts/build-core.sh`，release wheel 编译并安装到本工作区 `.venv`，`aurora_core` 版本 `0.1.0`，ARM64。构建脚本使用锁定依赖，在临时副本中构建，不向 `core/` 写入产物。脚本现在还会验证绑定支持 `route`，并实际以默认 V2 准备 Rust、处理音频块和 flush。

原应用的共享目录已经把默认预设设为 V2，并按 TOML 将 V2 映射到 `route="V"`；本次安装了实际支持 V 的核心并补齐原生回归检查，没有新增另一套预设列表。默认强度为 0.5，HRTF 为 SADIE II D1。经典 `room` 保持路线 B。

- [构建记录、绑定签名、原生二进制 SHA-256](core-build.json)
- 原生源码构建基线：`e71c1e1`，`core/` tree：`16c992340ef28b67310c70d2a1c5de22a9d72fae`
- SADIE II SOFA SHA-256：`e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31`
- `test_aurora_p6_defaults.py` 实际检查省略引擎／预设的 CLI、API 导出和 API 试听，由默认 Worker 渲染；报告均为 `is_stub=false, route=V, preset=V2`。
- 缺失核心或旧绑定不支持 V 时仍明确报错，不会静默切换路线或桩。

## 2. GUI 端到端与下载回读

通过 Playwright 驱动实际构建的 GUI 和 `aurora gui --no-browser --port 8776`，同一进程管理真实 API 与 Worker。启动时没有 `AURORA_ENGINE` 覆盖。实际文件上传，没有网络请求或渲染模拟。

对 `data/assets/real_music.wav` 和 `data/assets/holdout_electronic.wav` 分别执行：选 V2、50% 强度、seed=20261016 → 从第 1 秒生成 10 秒试听 → 真实 Web Audio 解码与播放 → 按钮及 A/B 快捷键切换 → 导出完整 WAV 48 kHz／24 bit、trim → 浏览器点击下载音频和报告 → 对下载到磁盘的文件重新完整解码、测真峰值并校验 SHA-256。

以下为最后一次浏览器验证结果；耗时是报告内的处理耗时，包括解码、渲染、编码与回读，不包括排队和用户操作。

| 素材／操作 | 输出样本帧数（每声道） | 真峰值 dBTP | 处理耗时 |
|---|---:|---:|---:|
| real_music 完整导出（20 秒） | 960,000 | −9.4 | 2,195.36 ms |
| holdout_electronic 完整导出（25 秒） | 1,200,000 | −1.4 | 2,636.92 ms |
| real_music 等响度试听（10 秒） | 480,000 | −14.1 | 1,968.64 ms |
| holdout_electronic 等响度试听（10 秒） | 480,000 | −1.8 | 2,007.33 ms |

真峰值采用 FFmpeg ebur128 的 BS.1770 测量，显示精度 0.1 dB。完整导出的输入／下载解码样本数严格相等，`timeline.sample_diff=0`，均为双声道。试听原声与效果的报告响度差均为 0.0 LU。

A/B 两路使用相同播放时钟、相同 offset、相同 480,000 帧长度；连续切换不重启源。采样窗口的原声／效果差值 RMS 分别约为 0.04116、0.22869，确认 B 侧确有真实处理结果。这是浏览器播放和同步对比的客观验证，未进行人工耳机主观听感评定。

- [实际下载文件的独立回读与哈希](download-readback.json)
- [A/B 播放时钟与 PCM 差异证据](gui-evidence.json)
- [完整导出 WAV：real_music](downloads/real_music.V2.wav)、[holdout_electronic](downloads/holdout_electronic.V2.wav)
- [浏览器下载的四份任务报告](gui-reports/)
- [浏览器验证结果](rust-e2e-results.json)、[运行输出](rust-e2e.txt)

四张截图都包含两份素材、V2 和四个成功任务。桌面视口 1440×1000，窄屏视口 390×844，均为全页截图；已实际查看浅／深色与桌面／窄屏结果，无水平溢出，真实引擎提示、预设、A/B 与下载操作可见。

| | 浅色 | 深色 |
|---|---|---|
| 桌面 | [desktop-light.png](screenshots/desktop-light.png) | [desktop-dark.png](screenshots/desktop-dark.png) |
| 窄屏 | [narrow-light.png](screenshots/narrow-light.png) | [narrow-dark.png](screenshots/narrow-dark.png) |

## 3. 真实引擎信息

首屏从 `/v1/presets` 读取引擎是否可用、实际安装的 `aurora_core` 版本与 HRTF，随预设选择显示路线／预设；切换经典 room 时显示路线 B。每个成功任务摘要使用该任务的引擎版本、实际准备的渲染路线和报告内预设，不受用户后来切换设置影响。

修正了首屏、试听成功通知、B 侧标签中写死的桩文案。真实 Rust 试听不显示直通桩；显式桩模式仍明确说明未应用 HRTF。连接切换时清空旧引擎状态。报告新增可选 `engine.route`，OpenAPI 快照更新在本次允许的 [P6 目录](openapi.json)，没有修改旧 QA 目录。

`engine.version` 来自已安装原生分发包；`engine.git_commit` 仍表示运行时应用仓库 HEAD，不代表 Rust 二进制的构建提交。原生二进制的构建基线、core tree 与哈希单独保存在 `core-build.json`。GUI 最终报告记录的应用 HEAD 为 `bde27c5`，引擎实现已在 `767dff5` 提交。

## 4. 用户文档与示例

- [快速入门](../../../docs/user/quickstart.md)：默认路线 V／V2／SADIE II、构建核心、自有音频路径、GUI 下载流程和经典 room 选项。
- [故障排查](../../../docs/user/troubleshooting.md)：加入“处理后峰值是对齐的，在起点前约 2 ms 处有一段低电平（约 −40 dB）预振铃，这是正常现象”，并引用 [P1a 验收记录](../../p1/ACCEPTANCE-cursor-p1a-05.md)。移除了“路线 V 尚未接入”的过期说明。
- CLI／API／部署说明同步写明真实 V2 默认值；Python 客户端默认 V2，强度省略时交给预设决定；视频示例也使用默认路线。
- [视频示例验证](video-default.json)：真实路线 V，288,000 样本，150 帧，封装前后 PCM 完全一致。以 −80 dBFS 检测到的约 2 ms 预振铃不被描述为采样长度错误。

## 5. 20 分钟长文件

输入由 20 秒 real_music 循环 60 次生成，双声道 PCM 16 bit／48 kHz、1200 秒。输入与输出保存在被忽略的 `.runtime/long/`，没有把数百 MB 的性能测试文件加入 Git；可运行 [benchmark_long.py](benchmark_long.py) 重建。

命令执行 `.venv/bin/aurora convert INPUT --output OUTPUT --overwrite`，没有指定 engine、route、preset 或 strength。转换输出为默认 WAV 24 bit。

| 指标 | 实测 |
|---|---:|
| 总墙钟耗时 | 69.6975 秒 |
| 报告内处理耗时 | 69.5498 秒 |
| 处理速度（音频时长／总耗时） | **17.2173× 实时** |
| 实时因子（总耗时／音频时长） | 0.05808 |
| 最大 RSS | **131,072,000 bytes = 125.00 MiB** |
| 输入／输出样本帧数 | 57,600,000／57,600,000 |
| 同步误差 | 0 样本 |
| 输出真峰值 | −9.4 dBTP |
| 退出码 | 0 |

RSS 来自 macOS `/usr/bin/time -l` 的 `maximum resident set size`（单位 bytes），不是各进程同时 RSS 的求和。墙钟耗时包含启动及完整处理／回读，不包含构造输入文件的时间。

证据：[汇总](long-benchmark.json)、[完整转换报告](long-conversion.json)、[time 原始输出](long-time.txt)。环境：Apple M4、24 GiB 内存、macOS 26.7.1、Python 3.12.13、Rust 1.99.1、FFmpeg 9.0.2，详见 [environment.json](environment.json)。

## 6. 检查与提交

| 检查 | 结果 |
|---|---|
| `bash scripts/build-core.sh` | release wheel 安装及默认 V2 原生 smoke 通过 |
| `uv run pytest` | **106 passed, 1 skipped**；跳过项为旧版仅支持 B 绑定的兼容检查，因为本机已安装新版 V 绑定；[输出](pytest.txt) |
| `uv run ruff check src tests/app` | 通过 |
| `uv run mypy src tests/app` | 33 个源文件通过 |
| `npm --prefix web run build`、`lint` | TypeScript、Vite、ESLint 通过 |
| 在 web/ 执行 `npm run test:e2e:rust`（取消引擎环境覆盖） | **1 passed**，含两份真实音频完整流程 |
| 在 web/ 执行 `npm run test:e2e` | **4 passed**，显式桩模式的上传、远程鉴权、取消和批量导出；[结果](stub-e2e-results.json) |
| `uv run python qa/p6/codex/verify_downloads.py` | 浏览器下载音频的哈希、样本数、真峰值均与报告一致 |
| 视频示例、shell 语法、`git diff --check` | 通过 |
| Impeccable 机械检查 | `main.tsx`／`types.ts` 无发现；[结果](design-detect.json)，截图实际检查完成 |

[最终检查索引](final-checks.json)。FastAPI／Starlette 测试客户端有一条既有的 httpx 弃用警告，不影响测试结果。首次全量测试发现 OpenAPI 快照仍指向 P5；已更新至允许写入的 P6 快照，并完整复跑通过。

子项提交：`6d274aa`（构建与默认入口）、`767dff5`（引擎信息）、`a06c54e`（GUI 端到端）、`95280bc`（文档）、`bde27c5`（长文件）、`fc6d5eb`（示例和 API 契约），本报告与最终证据另作最后一次提交。所有提交信息均以 `Agent: Codex` 结束。

本次仅修改任务允许的应用、Web、测试、脚本、示例、用户文档和 `qa/p6/codex/`。`core/`、`prototypes/`、`presets/`、`tools/`、`qa/tools/` 未修改。没有推送，没有进行远程部署；测试启动的 GUI／API／Worker 已退出。
