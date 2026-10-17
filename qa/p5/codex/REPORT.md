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

其余子项进行中。
