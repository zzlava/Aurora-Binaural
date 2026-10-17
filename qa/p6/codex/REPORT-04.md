# P6-codex-04 交付报告

日期：2026-10-17。分支：`agent/codex/P6-04`。执行方：Codex。

## 结果

应用层的 7 个子项已实现。按子项提交，提交信息均以 `Agent: Codex` 结束；没有推送，没有修改 `core/`、`presets/`、`tools/`、`qa/tools/`、`prototypes/`。

- 参数模型接受五个可选控件及 `controls` 对象，未传值来自 `[Vx.controls]`。有限越界值截断到 `[0,1]`，stderr 及 `params.control_clipping` 记录原值与最终值；NaN/Inf、未知键、相互冲突的平铺值和对象值拒绝。
- CLI `convert/preview` 新增四个高级参数，支持数值和显式 `%`；`--strength` 保持数值语法。`preview` 输出最多 20 秒的等响度 `effect.wav` / `original.wav` / `report.json`。目录输出避免覆盖输入。`presets --json` 返回每项 `default_controls`。
- API 的 job/preview 接受 `controls`，队列持久化保留截断事件；控件参与幂等摘要。OpenAPI 和参数 schema 与模型同步。
- GUI 高级面板含五个中文百分比滑块和恢复按钮；松手 300 ms 防抖。旧结果不覆盖新设置，更新期间可继续播放旧片段，完成后保留进度、播放/暂停状态和 A/B 选中侧。
- 我的预设保存基础预设版本与五个最终值。SQLite migration 1 新建 `user_presets`，保留现有素材、任务及 Worker 数据；支持列表、命名保存、重命名、删除、版本化 JSON 导入/导出，重名导入整批回滚。完整导出和批处理沿用当前选择；CLI `convert/preview/batch --user-preset 名称或ID` 读取同一本机数据库。
- 本地启动的 GUI 即使连接远程渲染服务，也使用本机 SQLite 保存我的预设；发送给远程任务的是基础预设和控件数值。直接使用远程 API 的预设端点时，存储位置为该 API 的数据库。
- route V 报告顶层 `controls` 与 `effective_params` 来自真实核心读回。route B 仅生效 strength、其余四项列入 `ignored_controls`；桩忽略全部五项，`effective_params=null`，报告中有中文说明。`params.controls` 表示归一化请求，顶层 `controls` 表示实际生效值。

## 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| pytest 全量应用层 | 202 passed / 1 skipped | [输出](controls-04/pytest.txt)、[JUnit](controls-04/pytest.xml) |
| Ruff / mypy strict | 通过 | [Ruff](controls-04/ruff.txt)、[mypy](controls-04/mypy.txt) |
| TypeScript / ESLint / Vite | 通过 | [类型检查](controls-04/typecheck.txt)、[ESLint](controls-04/eslint.txt)、[构建](controls-04/build.txt) |
| Playwright 新控件/预设，真实 Rust 服务 | 3 passed | [日志](controls-04/controls-e2e.txt)、[结构化结果](e2e-results-04.json) |
| 既有桩 GUI 流程 | 4 passed | [日志](controls-04/stub-e2e.txt) |
| 既有真实 Rust / 高解析 GUI 流程 | 2 passed | [日志](controls-04/rust-e2e.txt) |
| 5 控件 × 0/0.5/1 真渲染 | 15/15，controls 核心读回完全相同、sample_diff=0 | [完整矩阵](controls-04/matrix.json)、[运行结果](controls-04/matrix-run.txt) |
| 下载音频独立回读 | SHA-256 与下载报告一致；480000 帧 / 48 kHz / 双声道 float32 WAV | [回读证据](controls-04/download-readback.json) |
| Git 目录边界与 diff 检查 | 通过 | `git diff --check`；禁止目录没有差异 |

唯一跳过项是 P5 旧路线 B 绑定兼容检查；本机已安装新版路线 V 绑定，所以该兼容分支不适用。JUnit 输出另外记录了旧测试使用 `record_property` 与 xunit2 的提示；不影响断言。

本机原有扩展缺少 P1b 接口，已执行 `bash scripts/build-core.sh` 从临时副本构建并安装当前 Rust 扩展，默认 V3 / SADIE II D1 smoke 通过，未改核心源文件。全量回归中更新了假核心的 P1b 方法契约，以及新增折叠面板后旧测试的可见强度滑块定位。

浏览器用例实际拖动滑块并保持鼠标按下 400 ms，确认拖动期间不创建任务。松手到下一请求为 304 ms。暂停试听在替换后仍为 2.5 秒、B 侧；播放中的替换从 5.675 秒继续，A/B 两个 source 的启动时刻与偏移相同。精确数值见 [A/B 证据](controls-04/ab-evidence.json)。

预设用例实际完成保存 → 改名 → 页面刷新读回 → JSON 下载 → 删除 → JSON 导入 → 选用 → 两份真实音乐批量导出，两个报告均记录 `strength=0.65,width=0.83,height=0.42,room=0.21,voice_distance=0.12`。证据：[预设 JSON](controls-04/user-presets.json)、[批处理 1](controls-04/gui-batch-0.json)、[批处理 2](controls-04/gui-batch-1.json)、[滑块试听](controls-04/gui-preview.json)。

## 截图

| 视口 | 浅色 | 深色 |
|---|---|---|
| 桌面 1440 × 1000 | [桌面浅色](screenshots-04/desktop-light.png) | [桌面深色](screenshots-04/desktop-dark.png) |
| 窄屏 390 × 844 | [窄屏浅色](screenshots-04/narrow-light.png) | [窄屏深色](screenshots-04/narrow-dark.png) |

已检查四种全页截图：五个标签、百分比、恢复按钮、我的预设及任务下载操作完整；无横向溢出。机械设计检查的提示都位于原有 CSS 行，属于既有 DESIGN.md 字号/圆角登记差异，本次没有为此改动原界面。

## 0 / 0.5 / 1 的 render report 片段

使用 `data/assets/real_music.wav` 前 1 秒、真实 Rust 路线 V、V3、SADIE II D1。每次只改变一个控件，其余值从 TOML 默认读取。下面保留完整 `controls` 和与该控件相关的 `effective_params` 字段，未列出的内部字段见 [15 份完整报告](controls-04/matrix/strength-0.json) 与 [矩阵索引](controls-04/matrix.json)。所有值直接取自报告，未自行重算映射。

### 空间强度 `strength`

```json
[
  {"controls": {"strength": 0.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"bypass": true, "wide_lin": 0.0, "height_lin": 0.0}},
  {"controls": {"strength": 0.5, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"bypass": false, "wide_lin": 0.5610092271509817, "height_lin": 0.39716411736214075}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"bypass": false, "wide_lin": 1.1220184543019633, "height_lin": 0.7943282347242815}}
]
```

### 宽度 `width`

```json
[
  {"controls": {"strength": 1.0, "width": 0.0, "height": 0.75, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"front_az": 30.0, "wide_db": -6.0, "surround_db": -12.0}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"front_az": 45.0, "wide_db": 1.0, "surround_db": -4.0}},
  {"controls": {"strength": 1.0, "width": 1.0, "height": 0.75, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"front_az": 60.0, "wide_db": 8.0, "surround_db": 4.0}}
]
```

### 头顶感 `height`

```json
[
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.0, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"height_db": -8.0, "height_el": 40.0, "ceiling_db": 3.0}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.5, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"height_db": -4.0, "height_el": 50.0, "ceiling_db": 5.0}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 1.0, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"height_db": 0.0, "height_el": 60.0, "ceiling_db": 7.0}}
]
```

### 房间感 `room`

```json
[
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.0, "voice_distance": 0.25}, "effective_params": {"room_db": -4.0, "t60": 0.44999999999999996}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.25}, "effective_params": {"room_db": 4.0, "t60": 0.95}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 1.0, "voice_distance": 0.25}, "effective_params": {"room_db": 12.0, "t60": 1.45}}
]
```

### 人声距离 `voice_distance`

```json
[
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.0}, "effective_params": {"center_distance_m": 0.25, "direct_room_send_db": -12.0}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 0.5}, "effective_params": {"center_distance_m": 1.55, "direct_room_send_db": -4.0}},
  {"controls": {"strength": 1.0, "width": 0.5, "height": 0.75, "room": 0.5, "voice_distance": 1.0}, "effective_params": {"center_distance_m": 2.85, "direct_room_send_db": 4.0}}
]
```

## 复现与边界

```bash
bash scripts/build-core.sh
npm --prefix web run build
uv run pytest tests/app
uv run ruff check src tests/app
uv run mypy src tests/app
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run test:e2e:controls
npm --prefix web run test:e2e
npm --prefix web run test:e2e:rust
uv run python qa/p6/codex/verify_controls_04.py
```

渲染矩阵脚本将较大音频留在忽略提交的 `.runtime/`，只提交报告。Playwright 的 8777/8779 为隔离测试服务；测试结束后关闭。既有 Rust 高解析测试沿用 P6-02 的本机输入 fixture，详细复现要求见该阶段报告；本次新控件测试和矩阵脚本只依赖已跟踪的音乐素材。

本次验证为应用功能、客观读回和 Chromium 自动化，不包含新的主观听感评测、Safari/Firefox 或移动真机测试。文档入口：[快速开始](../../../docs/user/quickstart.md)、[CLI](../../../docs/user/cli.md)、[API](../../../docs/user/api.md)。
