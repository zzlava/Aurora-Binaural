# P5-codex-01 执行报告

分支：`agent/codex/P5-01`；基线：`b13f388`。只在本分支提交，不推送。

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

Rust 内部固定 48 kHz，FFmpeg 在输入和输出边界重采样。验证 WAV／M4A 的 44.1／48／96 kHz 与 MP3 的 44.1／48 kHz；MP3 96 kHz 在参数层明确拒绝，GUI 禁用该组合。原生采样率／编码与块契约模块 30 项；应用全套在新增最后一项前为 95 项通过，最终汇总见下。

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

进行中。
