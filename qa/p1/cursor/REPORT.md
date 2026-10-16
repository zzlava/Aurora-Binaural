# P1a-cursor-01 验收报告

日期：2026-10-17。分支 `agent/cursor/P1a-01`。执行方 Cursor（grok-4.7-high）。

## 环境

- `rustc 1.99.1 (58cf8f9c3 2026-10-13)`，本机 rustup。没有修改全局工具链，没有写入 `rust-toolchain`。
- `rustfmt` 与 `clippy` 已随该工具链安装。本次没有再执行 `rustup component add`。
- 主机是 macOS ARM64。Docker 客户端 28.5.2，上下文为 OrbStack。
- Python 绑定装进 `prototypes/.venv`（CPython 3.12.13）。`maturin 1.15.0` 用 `uv pip` 装进这个虚拟环境，没有改系统 Python。

## 产物

`core/` 是一个 Cargo workspace：

- `aurora-dsp`：纯 Rust，不依赖 Python。入口是 `RouteB`，实现 `Processor`。
- `aurora-py`：PyO3 模块，Python 包名 `aurora_core`，类名 `Renderer`。

路线 B 的块处理链是：LR4（150 Hz）→ 固定跳长 STFT 上混（D1）→ 扇区 / 宽声像 / 环境声的均匀分块 FFT 卷积 → 鞋盒早期反射 → 种子决定的 FDN → 强度映射 → 真峰值限制器。`strength = 0` 走旁路，输出等于输入，`latency_samples()` 为 0。

HRIR 包由 `core/tools/export_hrir_pack.py` 从 SOFA 生成，写到：

- `data/hrtf/ku100_frontal.ahrp`（源 SHA-256 `d33e675d20f5456426bf79560187122e4278fecb5aaa1b2608f1727acb91e974`）
- `data/hrtf/sadie_d1_frontal.ahrp`（源 SHA-256 `e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31`）

加载时核对源文件哈希。不一致就返回错误，不回退到其他数据。

## 数值回归

脚本 `qa/p1/cursor/compare_route_b.py`。路线 B，正前方均衡（包内 `frontal+power`），强度 0.4 和 0.8，KU100 与 SADIE II。Rust 侧以 256 样本逐块调用。对照的是原型 `process_file`。

1/3 倍频程是 20 Hz–20 kHz 的双耳功率和，取最大绝对差。IACC 是 ±1 ms 内宽带互相关系数的差。

| 素材 | HRTF | 强度 | 1/3 倍频程差 | IACC 差 |
|---|---|---|---|---|
| real_voice | KU100 | 0.4 | 0.007 dB | 0.0000 |
| real_voice | KU100 | 0.8 | 0.007 dB | 0.0000 |
| real_voice | SADIE II | 0.4 | 0.007 dB | 0.0000 |
| real_voice | SADIE II | 0.8 | 0.007 dB | 0.0000 |
| real_music | KU100 | 0.4 | 0.003 dB | 0.0000 |
| real_music | KU100 | 0.8 | 0.003 dB | 0.0000 |
| real_music | SADIE II | 0.4 | 0.003 dB | 0.0000 |
| real_music | SADIE II | 0.8 | 0.003 dB | 0.0000 |
| real_ambience | KU100 | 0.4 | 0.004 dB | 0.0000 |
| real_ambience | KU100 | 0.8 | 0.004 dB | 0.0000 |
| real_ambience | SADIE II | 0.4 | 0.004 dB | 0.0000 |
| real_ambience | SADIE II | 0.8 | 0.004 dB | 0.0000 |

最差 1/3 倍频程差 0.007 dB（门槛 0.5 dB）。最差 IACC 差在四位小数下是 0.0000（门槛 0.02）。

输出不是逐样本相同，原因如下：

- 原型 `process_file` 把整段文件交给 `fftconvolve`。Rust 卷积是均匀分块重叠相加，固定分区 64 样本，输出因此多 64 样本延时。非卷积支路（低频、侧向、FDN）补上同样的 64 样本，相对对齐和原型一致。
- 真峰值限制器在信号低于 −1 dBTP 时增益精确为 1，输出是 32 样本纯延时。前瞻计入 `latency_samples`。这三段真实素材没有触发衰减，所以频谱和 IACC 不受限制器影响。
- 包里的渲染 HRIR 是 f32。原型卷积用 f64。0.007 dB 量级的差别来自这里。
- 路线 B 的卷积用的是原型均衡后的时域 HRIR（反距离加权，和 `interpolate_hrir` 一致）。最小相位幅度插值加分数 ITD 另有实现和测试。先前用最小相位加整数延时重建同一条 HRIR，在 3 秒人声上 8 kHz 附近会多出约 0.58 dB，过不了 0.5 dB 的门，所以它不进入这条渲染链。包里仍然保存最小相位 HRIR 和 ITD。

## 块大小

`cargo test -p aurora-dsp --lib` 共 31 项通过。整条链在块大小 32、64、256、1024、4096 以及一次处理整段（4096 样本）之间，两两误差不超过 −100 dBFS。上混、LR4、FDN、均匀分块卷积各自还有同样的块大小测试。上混还和 `qa/p1/cursor/reference/upmix_stems.f64` 逐样本对照（最大绝对误差 < 1e-5）。

## 实时安全

测试用计数分配器包住 `process`。计数开关是线程局部的，避免并行测试互相干扰。随机块、含 NaN / Inf 的输入上，`process` 的分配次数是 0。NaN 和 Inf 换成 0，两次相同输入得到相同输出。`process` 里没有锁，也没有会 panic 的路径。静音、满幅和随机块大小由限制器测试、旁路测试和块大小测试覆盖。

`prepare` 里完成 FFT 计划、卷积分区、延迟线和 FDN 缓冲。`set_params` 可以在换种子时重建去相关器；`process` 不分配。

## 性能

`cargo bench -p aurora-dsp --bench realtime`，criterion，Mac ARM64，单线程。48 kHz 立体声，块大小 256。测量区间：

`route_b/block_256  time: [117.41 µs  118.08 µs  118.78 µs]`

一块的时长是 256/48000 = 5.333 ms。按中位 118.08 µs 计算，实时倍数是 **45.2×**（门槛 20×）。下限 118.78 µs 仍是 44.9×。基准使用 256 点 HRIR 和与正式链相同的 15 路分区卷积，计时区间只有 `process`。

## Python 绑定

```text
VIRTUAL_ENV=prototypes/.venv maturin develop --release -m core/aurora-py/Cargo.toml
```

`import aurora_core` 提供 `Renderer`。它实现 `prepare` / `set_params` / `process` / `latency_samples` / `tail_samples` / `flush` / `reset`。`process` 接受 `(frames, channels)` 的 float32 数组，返回 `(frames, 2)`。`set_params` 接受带 `strength`（或 `effective_strength()`）、`preset` 和 `seed` 的对象。

示例：`qa/p1/cursor/demo_blocks.py`。半秒 440 Hz 立体声，块大小 256，KU100，强度 0.4：

```text
frames 24000 channels 2 latency 1145 tail 21600 peak 0.2401
```

延时 1145 = 上混 1024 + 正前方 HRIR 峰 25 + 分区卷积 64 + 限制器前瞻 32。

## Linux

OrbStack 上的 `rust:1.99` 容器（`linux/arm64`）执行了 `cargo test -p aurora-dsp --lib` 和 `maturin build -m aurora-py/Cargo.toml --release`。构建目录是容器内的 `/tmp/target`，没有写到仓库的 `target/`。

- `cargo test`：31 passed，0 failed，0.48 秒。
- `maturin build --release`：生成 `aurora_binaural-0.1.0-cp313-cp313-manylinux_2_35_aarch64.whl`。

第一次尝试用了 `bash -lc`，登录 shell 丢掉了镜像里的 `PATH`，`cargo` 找不到。第二次把 `PATH` 设成镜像默认值后通过。容器里的 Python 是 3.13，本机虚拟环境是 3.12，两边都编过扩展模块。

## 本地门禁

在 `core/` 下：

- `cargo fmt --all -- --check` 通过
- `cargo clippy --all-targets -- -D warnings` 通过
- `cargo test -p aurora-dsp --lib`：31 passed

## P1a-cursor-02：HRIR pack 改走 Git LFS

日期：2026-10-17。分支 `agent/cursor/P1a-01`。

`data/hrtf/ku100_frontal.ahrp`（11,153,925 字节）和 `data/hrtf/sadie_d1_frontal.ahrp`（36,334,725 字节）原来在 `103b079` 里是普通 Git 对象。`.gitattributes` 增加了：

```text
*.ahrp filter=lfs diff=lfs merge=lfs -text
```

这条规则现在和其他二进制规则放在一起。`git lfs migrate` 会把它追加到被改写范围里最早那个提交的 `.gitattributes` 末尾，随后的提交再把它挪到二进制规则旁边。

迁移命令：

```text
git lfs migrate import --include="*.ahrp" --include-ref=refs/heads/agent/cursor/P1a-01 --exclude-ref=refs/remotes/origin/main
```

不带 `--exclude-ref` 时，`--include-ref=refs/heads/agent/cursor/P1a-01` 会改写这个引用能到达的全部 35 个提交，`refs/heads/main` 和 `refs/heads/agent/cursor/P0-01` 也被一起更新。这两个引用已放回 `99b9055cd0965f6f087365a8b84d0f7b6bde499c` 和 `2420e16fb9bf4ad2f00083c7572bb935425a57c6`。`refs/remotes/origin/*` 没有变化。

排除 `origin/main` 之后，只改写了本分支上尚未推送的 4 个提交。最老的那个提交的父提交仍是 `fd31dadd501ac0fcbde86250235eb478c1e4717c`（`origin/main`）。

| 原提交 | 新提交 | 说明 |
|---|---|---|
| `3ecb5b2` | `3cac5c0` | `.gitattributes` 末尾加上 `*.ahrp` 规则 |
| `103b079` | `1e4e210` | 两个 pack 换成 LFS 指针 |
| `688012f` | `107c032` | 相对父提交的差异与原来相同 |
| `b6cacaf` | `ea309b0` | 把 `*.ahrp` 规则挪到其他二进制规则旁边 |

`git lfs ls-files`：

```text
0633b4df32 * data/hrtf/ku100_frontal.ahrp
9c3482d6a5 * data/hrtf/sadie_d1_frontal.ahrp
```

`1e4e210` 的 `git log --stat` 里，这两个文件各是 3 行指针：

```text
data/hrtf/ku100_frontal.ahrp    | 3 +++
data/hrtf/sadie_d1_frontal.ahrp | 3 +++
```

指针：

- `ku100_frontal.ahrp`：`oid sha256:0633b4df3254a28d2c2b6365fbab18e0bdc18a3f6b16034b40ece374d3815da6`，`size 11153925`
- `sadie_d1_frontal.ahrp`：`oid sha256:9c3482d6a53351bb9deba559ab4638cc8ba920e77731896b28cbe1e42e5526de`，`size 36334725`

工作区文件已用 `git lfs checkout` 还原，SHA-256 与上面的 oid 一致。没有推送。
