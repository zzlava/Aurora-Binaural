# P1a-cursor-01：Rust 实时安全核心（路线 B 组件）与 Python 绑定

状态：已派发（2026-10-17）
执行方：Cursor（`agent`，模型 grok-4.7-high）
依据：docs/plan.md 第 6、7 节；`docs/adr/0003-p1-shared-components-before-listening.md`；`qa/p0/ACCEPTANCE-cursor-03.md`；P0 原型 `prototypes/aurora_p0/`（数值参考）

## 工作环境

- 独立克隆 `.agents/cursor/`，分支 `agent/cursor/P1a-01`（基于最新 main）。
- 只修改 `core/`、`prototypes/`（只允许增加导出参考数据的脚本）、`data/`（HRIR 预处理产物）、`qa/p1/cursor/`。
- 本机 Rust 1.99.1（rustup）。不要修改全局工具链配置；需要的组件用 `rustup component add` 安装，并在报告里注明。

## 目标

在 `core/` 建立 Rust workspace：

- `aurora-dsp`：纯 Rust 库，不依赖 Python。
- `aurora-py`：PyO3 + maturin 绑定，提供 Python 包 `aurora_core`。

实现路线 B 的完整块处理链，满足 plan 第 7 节的实时安全约束。

## 组件清单（每个组件都要有单元测试，并与原型做数值对照）

1. `Processor` trait：`prepare`／`set_params`／`process`／`latency_samples`／`tail_samples`／`flush`／`reset`（签名对齐 `src/aurora_binaural/engine.py` 的 Renderer 协议）。
2. LR4 低频分频：与原型逐样本对照，并单独测试重新合成后的平坦度（20 Hz–20 kHz，±0.5 dB）。
3. 均匀分块 FFT 卷积，并为长冲激响应预留非均匀分块的接口。用 realfft 或 rustfft；在 `prepare` 中完成全部分配。
4. HRTF 数据层
   - 用 Python 预处理脚本（放在 `prototypes/` 或 `core/tools/`）把 SOFA 转成简单的二进制 "Aurora HRIR pack"，内容包括：方向表、最小相位 HRIR、ITD、采样率、均衡方式、源文件 SHA-256。
   - Rust 加载 pack，并校验其中记录的哈希；校验失败就报错，**不允许回退**到其他数据。
5. 方向插值：最小相位幅度部分按邻近方向插值，ITD 用分数延时；方向变化时在块间做交叉淡化。
6. STFT 流式上混：固定跳长，状态在块之间延续，按相干性区分直达成分与环境成分（与原型的 D1 方法一致）；延时通过 `latency_samples` 报告。
7. 早期反射（鞋盒镜像源）与 FDN 晚期混响：所有随机量都由 seed 决定；`process` 中不分配内存。
8. 音色保护与强度映射：参数表从 TOML 加载（`presets/` 草案格式），不要硬编码在代码里；`strength=0` 时走专门的旁路分支。
9. 真峰值限制器：4 倍过采样，目标 −1 dBTP；前瞻量计入 `latency_samples`。

## 验收（全部写进 `qa/p1/cursor/REPORT.md`）

- **数值回归**：用 Rust 核心渲染 P0 的三类真实素材（路线 B，强度 0.4／0.8，KU100 和 SADIE II，正前方均衡），与原型输出比较：
  - 1/3 倍频程频响差 ≤ 0.5 dB
  - 宽带 IACC 差 ≤ 0.02
  - 如果不能逐样本一致，说明原因（例如原型并非流式）。
- **块大小一致性**：块大小 32／64／256／1024／4096，以及一次处理整段，两两之间误差 ≤ −100 dBFS。
- **实时安全**：用计数分配器（counting allocator）或等效方法证明 `process` 中没有发生内存分配；不加锁；不 panic（模糊测试或属性测试覆盖：随机块大小、静音、满幅、NaN 输入时要有确定的处理）。
- **性能**：在 Mac ARM64 上，48 kHz 立体声、块大小 256 时的单核实时倍数（目标 ≥ 20 倍实时）；用 criterion 做基准测试。
- **Python 绑定**：`maturin develop` 后，`import aurora_core` 能提供一个实现 Renderer 协议的类；在 `qa/p1/cursor/` 写一个示例脚本，演示从 Python 逐块调用。
- **Linux 构建**：如果本机有 Docker（OrbStack），在 Linux 容器里执行 `cargo test` 和 `maturin build`；没有就在报告里说明。
- 运行 `cargo fmt --check`、`cargo clippy -- -D warnings`、`cargo test`，全部通过。

## Git 规则

只在 `agent/cursor/P1a-01` 上提交，可以分多次提交，提交信息末尾加 `Agent: Cursor`。不要推送，不要修改 main。不要提交 `target/`。
