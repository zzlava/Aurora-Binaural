# 验收记录：P1a-cursor-01／02（Rust 实时核心：路线 B 组件 + Python 绑定）

验收人：Claude　日期：2026-10-17 02:45（UTC+8）
分支：`agent/cursor/P1a-01` @ `4f9a5e0`（模型 grok-4.7-high）
结论：**通过：移植忠实，工程门禁全部达标**，合并到 main。
**注意**：核心忠实复现了原型引擎，所以原型引擎在音色和展宽上的问题（见 `qa/p0/ACCEPTANCE-cursor-03.md` 的勘误）在 Rust 核心里同样存在。要等 P0-cursor-04 修完算法后，再同步到 Rust 核心。

## 独立复现（Linux 虚拟机，Claude 自装的 rustup 1.99.1，工作区副本，不含 target 和 .venv）

| 项目 | 结果 |
|---|---|
| `cargo fmt --check` | 通过 |
| `cargo clippy --all-targets -- -D warnings` | 通过 |
| `cargo test --release` | 31 passed。测试覆盖：块大小一致性、`process` 零分配（计数分配器）、NaN 处理、强度为 0 时旁路、分频器与 scipy 逐系数／逐样本一致、FDN／早期反射／上混与原型的参考数据一致、PCG64 随机序列与 numpy 一致、HRIR pack 哈希不符时拒绝加载 |
| maturin 构建 Linux wheel | 成功（cp312，manylinux_2_35_aarch64） |
| Claude 自写脚本：用 Python 逐块调用（块大小在 1–4096 之间随机）与固定 256 对比 | 三类真实素材的误差都是 −600 dBFS（逐位一致） |
| 运行代理的对照脚本（原型引擎对 Rust） | 12 组都通过：1/3 倍频程差 ≤ 0.007 dB，IACC 差为 0.0000 |
| 延时 | 1145 样本（约 23.9 ms，包括上混 STFT、分块卷积、限制器前瞻） |
| 性能 | 代理在 Mac 上报告 45.2 倍实时（块大小 256）；Claude 没有在 Mac 上复测 |
| HRIR pack | 已改用 LFS 指针（oid 与文件的 SHA-256 一致）；本分支上没有残留的大文件对象 ✅ |

## 过程中的偏差

P1a-cursor-02 的第一次迁移误改了克隆内的 `main` 和 `P0-01` 引用。代理已经恢复，并改用 `--exclude-ref` 重做。Claude 核对过：主仓库的 `main` 和 `origin/main` 都还是 `056bc21`，没有受影响。以后在任务书里使用 `lfs migrate` 时，要明确写上 `--exclude-ref`。
