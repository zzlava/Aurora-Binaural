# P5-codex-01 执行报告

分支：`agent/codex/P5-01`；基线：`b13f388`。只在本分支提交，不推送。

## 1. 核心适配器

新增 `RustRenderer` 包装 `aurora_core.Renderer`，默认 `AURORA_ENGINE=rust`。缺少扩展时返回 `CORE_NOT_INSTALLED` 并提供本机构建提示；只有显式设置 `AURORA_ENGINE=stub` 才使用直通桩。转换、试听、Worker 共用选择逻辑，报告按实际引擎记录 `is_stub`，GUI 从服务读取引擎状态。旧版路线 B 核心拒绝路线 V，绝不替代路线。输入／输出块形状、类型、有限值、flush 长度在适配边界校验。

验证：引擎选择和既有试听测试；后续总体验证记录见报告末尾。

## 2. 构建

增加 uv `core-build` 依赖组（maturin 1.15.0）及 `scripts/build-core.sh`、`scripts/stage-core.py`。在临时副本内生成独立 `aurora_core` wheel 元数据，避免 maturin 继承应用项目名覆盖应用包；原 `core/` 不写入。

本机 `bash scripts/build-core.sh` 成功，CPython 3.12 / macOS ARM64，Rust 1.99.1 release。原生块烟测成功：4800 输入帧、4800 输出帧，延迟 1229 帧、尾巴 21600 帧，全部有限值。Docker 新增独立 Rust 编译阶段，最终镜像安装扩展；完整容器构建验证结果将在报告末尾补充。本机命令见 `deploy/README.md`。

其余子项进行中。
