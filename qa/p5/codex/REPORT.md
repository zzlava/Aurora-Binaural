# P5-codex-01 执行报告

分支：`agent/codex/P5-01`；基线：`b13f388`。只在本分支提交，不推送。

**应用层任务完成。** 默认 Rust、显式桩、数据驱动预设、SADIE 分发、本机／Docker 构建与契约测试均已完成。基线核心只有路线 B；真实路线 V 集成仍需 Cursor 的核心交付，当前会明确拒绝，不会降级为 B 或桩。

## 1. 核心适配器

新增 `RustRenderer` 包装 `aurora_core.Renderer`，默认 `AURORA_ENGINE=rust`。缺少扩展时返回 `CORE_NOT_INSTALLED` 并提供本机构建提示；只有显式设置 `AURORA_ENGINE=stub` 才使用直通桩。转换、试听、Worker 共用选择逻辑，报告按实际引擎记录 `is_stub`，GUI 从服务读取引擎状态。旧版路线 B 核心拒绝路线 V，绝不替代路线。输入／输出块形状、类型、有限值、flush 长度在适配边界校验。

验证：引擎选择和既有试听测试；后续总体验证记录见报告末尾。

## 2. 构建

增加 uv `core-build` 依赖组（maturin 1.15.0）及 `scripts/build-core.sh`、`scripts/stage-core.py`。在临时副本内生成独立 `aurora_core` wheel 元数据，避免 maturin 继承应用项目名覆盖应用包；原 `core/` 不写入。

本机 `bash scripts/build-core.sh` 成功，CPython 3.12 / macOS ARM64，Rust 1.99.1 release。原生块烟测成功：4800 输入帧、4800 输出帧，延迟 1229 帧、尾巴 21600 帧，全部有限值。Docker 新增独立 Rust 编译阶段，最终镜像安装扩展；完整容器构建验证结果将在报告末尾补充。本机命令见 `deploy/README.md`。

## 3. 数据驱动预设目录

`catalog.py` 遍历只读 `presets/*.toml`，从命名表获取 ID、中文名称、版本、强度与源文件。当前 TOML 无 route/default 元数据，依据 `[layout]` 识别 V／其余识别 B，产品策略默认 V2。API、CLI、参数 JSON Schema、GUI 共用目录；GUI 不再保留独立文案或预设列表。公开 V1/V2/V3 与“经典 · 自然空间”room。旧 surround/orbit 仅保留显式请求兼容，不在默认目录／GUI 出现。

wheel 内包含同一份 TOML；`AURORA_PRESETS_DIR` 可覆盖目录，修改数据后重启进程刷新。测试保留旧预设显式桩测试；新增自定义 V7 TOML 探测与 API/CLI 目录一致性。pytest 65 项通过，Ruff/mypy 通过；前端 build/lint 通过。

## 4. 默认 HRTF 分发

应用 wheel／sdist／Docker 默认仅打包 SADIE II D1 的 SOFA、`sadie_d1_frontal.ahrp`、Apache-2.0 全文与署名。资源查找支持安装包内数据，Worker 也不再依赖当前工作目录。无 KU100 UI 选项；只读 TOML 中的 TH Köln KU100 校准表在第三方声明与部署文档中标为“仅本地研究”。

`uv build` 成功；检查实际 wheel 和 sdist 清单，SADIE SOFA／pack／许可证／预设均存在，KU100 文件数为 0，结果见 `distribution-check.json`。构建二进制留在被忽略的 `.dist/`，不提交大产物。

## 5. 原生契约与端到端转换

所有旧桩测试保留（通过 autouse fixture 显式设置 `AURORA_ENGINE=stub`）；真实扩展测试直接创建 RustRenderer，不会被 fixture 切成桩。无扩展时真实契约模块明确 skip，并提示构建命令。

原生契约覆盖：单声道／双声道、空块／1 帧／最大块／超限块／NaN、flush 精确长度与重复调用、reset 重现；strength=0 与桩逐样本相同；1／67／1000／8193 帧短流的延迟补偿；trim 与 preserve 对照原生 process+flush 输出，保留尾巴为真实非零数据；块大小 1／63／256／1024／1537 的一致性 ≤ −100 dBFS；源 SOFA 哈希错误拒绝；旧版核心拒绝路线 V。新增路径测试验证未来 `route=V` 绑定在 prepare 前收到 TOML 对应路线／文件／初始强度／seed。

Rust 内部固定 48 kHz，FFmpeg 在输入和输出边界重采样。验证 WAV／M4A 的 44.1／48／96 kHz 与 MP3 的 44.1／48 kHz；MP3 96 kHz 在参数层明确拒绝，GUI 禁用该组合。原生模块共 **32 项**，另覆盖 API → Worker → 原生核心的完整导出与源时间试听：实际报告为 Rust，长度正确，预览等响度差 ≤ 0.2 LU。

### 真实样本（main 现有路线 B）

```bash
AURORA_ENGINE=rust uv run aurora convert data/assets/holdout_electronic.wav \
  --preset room --strength 0.4 \
  --output qa/p5/codex/.runtime/route-b-electronic.wav \
  --report qa/p5/codex/route-b-e2e.json
```

- SADIE II D1，48 kHz／24-bit WAV，room，strength 0.4，seed 20261016。
- 输入、期望输出、解码回读均 **1,200,000 样本（25 s）**；差值 **0**。
- 引擎延迟 **1229 样本**；声明尾巴 **21600 样本**；trim 保持原长度。
- 输出 **−11.4 LUFS，−0.6 dBTP**（FFmpeg ebur128 真峰值）；转换约 1.14 s。报告 `is_stub=false`，核心版本 0.1.0，SOFA SHA-256 与授权记录一致。
- **现有路线 B 的 −0.6 dBTP 高于 −1 dBTP，未宣称达到路线 V 限制器验收。** 不修改只读核心；本任务按要求记录实测结果。
- 输出音频留在 `.runtime/`，仅提交结构化报告与证据。未进行人工听感验收，也未宣称已验证路线 V。

## 6. 最终验证

| 验证 | 实测结果 |
|---|---|
| `uv run pytest` | **98 passed**，含 32 项真实 Rust 测试；1 条既有 Starlette/httpx 弃用提示 |
| `uv run ruff check src tests/app` | 通过 |
| `uv run mypy` | 通过，31 个源文件 |
| `uv lock --check`／`git diff --check` | 通过 |
| `npm --prefix web ci`／build／lint | 通过 |
| `npm --prefix web run test:e2e` | **4 passed**；验证默认 V2、四张数据驱动卡片、无 orbit、试听／导出／取消／远程模式 |
| `bash scripts/build-core.sh` | macOS ARM64，CPython 3.12，Rust 1.99.1，maturin 1.15.0，release 成功 |
| `uv build --out-dir qa/p5/codex/.dist` | wheel／sdist 成功；清单、大小及哈希见 `distribution-check.json` |
| 仓库外 wheel 运行 | 使用 wheel 内预设／HRTF 完成原生转换，1,200,000 样本、差值 0 |
| 全新无核心虚拟环境 | 默认引擎退出 4／`CORE_NOT_INSTALLED`，无输出文件；显式 stub 成功且 `is_stub=true` |
| `docker build -f deploy/Dockerfile -t aurora-binaural:p5 .` | 通过；最终镜像可导入原生扩展，预设目录正确 |
| Docker 内原生转换 | 1,200,000 样本，差值 0，−0.6 dBTP；最终镜像无 cargo／rustc、无 KU100 pack |

最终镜像：`sha256:06c59c681a808566a19060c6d49b5502ad67477ea73ef2eecd5538369fb5b677`。

结构化证据：`validation.json`、`route-b-e2e.json`、`container-smoke.json`、`installed-wheel-smoke.json`、`no-core-smoke.json`、`distribution-check.json`、`e2e-results.json`。OpenAPI 和参数 schema 同步放在本目录；四张宽／窄、明／暗截图在 `screenshots/`，已查看桌面深色与窄屏浅色，布局无横向溢出。

本地 `.runtime/` 音频／数据库、`.dist/` 二进制和测试临时输出均忽略；不提交。

## 提交与边界

1. `8d87615`：Rust 适配器、显式引擎选择、界面／报告状态。
2. `d92244f`：maturin 本机构建与 Docker Rust 阶段。
3. `20b5737`：TOML 目录、默认 V2、经典 room、GUI／CLI／API 共用。
4. `19f67f7`：SADIE-only wheel／sdist 与许可。
5. `ecbf020`：真实 Rust 契约、采样率边界与编码时间线。
6. 最终报告提交：补充原生 API／Worker 测试、可见预设 ID、最终证据。

全部提交末尾为 `Agent: Codex`。没有推送、切换分支、合并 Cursor 工作或修改 `core/`、`prototypes/`、`presets/`。未做人工听感验证。后续核心若按任务书保留原构造路径参数并新增 `route="V"`，适配器会按 TOML 传入路线；最终 ABI／路线 V 音频对照仍需在核心交付后联调。
