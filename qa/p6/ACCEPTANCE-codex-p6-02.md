# 验收：P6-codex-02（FLAC/高解析度、TOML 默认预设、响度复核）

验收人：Claude　日期：2026-10-17　结论：**通过（附后续项）**

## 独立核对
- 提交范围：ab1a081..52f5353 共 44 个文件，未改动 core/、presets/、tools/、qa/tools/、prototypes/。
- 干净快照（git archive，在 Linux VM 上 uv sync，使用 Claude 的 aurora_core wheel，FFmpeg 4.4.2 带 soxr）运行 tests/app：除下面三类环境相关失败外全部通过。
  1. m4a/AAC 时间线用例：FFmpeg 4.4 的 AAC 编码器 priming 行为与 9.x 不同。这是环境依赖，不是功能缺陷，需要按 FFmpeg 版本条件处理（见后续项）。
  2. test_default_api_and_worker_render_v2：断言 core `__version__` 不为 "unknown"。Claude 的 wheel 较旧，没有 `__version__`，属于验收环境产物。
  3. test_cancel_queued_running_duplicate_and_terminal_state_invariance：3 次运行失败 1 次，lease 临时目录清理存在竞态，是既有 P3 用例的偶发失败。
- GUI 截图 hires-desktop.png 已查看：显示真实 Rust 引擎信息条，格式/采样率/位深/尾音选择正常，任务队列显示 96/48/44.1 kHz 导出结果。
- 报告内容核对：source 采样率导出、TPDF（planar s16）、元数据与封面回读、soxr 缺失时返回 SOXR_UNAVAILABLE 并以退出码 4 失败、catalog 读取 TOML 的 default_preset 字段、+3 LU 来自引擎本身（应用增益为 0.0 dB）。

## 关于冲激峰值“未通过”
路线 V 是多只虚拟扬声器叠加，再加 HRIR 与反射，冲激响应没有单一的“峰值时刻”。48 kHz 下峰值提前约 14 帧（0.29 ms），P1a-05 验收时已知。旁路（strength=0）在所有采样率下误差均为 0，说明时间原点正确。**±1 源采样的峰值要求是 Claude 写任务书时设定过严，现在撤回。**今后对空间路径的时间线要求改为：样本数一致、旁路逐位对齐，峰值偏移不超过 1 ms 并在报告中记录。是否让核心报告的延时对齐直达声峰值，列为核心侧低优先级事项。

## 后续项（P6-codex-03）
setup.sh 一键脚本；AAC 用例按 FFmpeg 版本条件处理；修复 cancel 用例竞态；把时间线验收改为上述新定义；在 GUI 中自动探测 ffmpeg-full（macOS 默认 ffmpeg 缺少 soxr）。
