# P5-codex-01 执行报告

分支：`agent/codex/P5-01`；基线：`b13f388`。只在本分支提交，不推送。

## 1. 核心适配器

新增 `RustRenderer` 包装 `aurora_core.Renderer`，默认 `AURORA_ENGINE=rust`。缺少扩展时返回 `CORE_NOT_INSTALLED` 并提供本机构建提示；只有显式设置 `AURORA_ENGINE=stub` 才使用直通桩。转换、试听、Worker 共用选择逻辑，报告按实际引擎记录 `is_stub`，GUI 从服务读取引擎状态。旧版路线 B 核心拒绝路线 V，绝不替代路线。输入／输出块形状、类型、有限值、flush 长度在适配边界校验。

验证：引擎选择和既有试听测试；后续总体验证记录见报告末尾。

其余子项进行中。
