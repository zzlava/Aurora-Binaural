# P1a-cursor-04 验收报告

日期：2026-10-17。分支 `agent/cursor/P1a-03`。执行方 Cursor（grok-4.7-high）。

## 环境

- `rustc 1.99.1 (58cf8f9c3 2026-10-13)`，本机 rustup。没有修改全局工具链，没有写入 `rust-toolchain`。
- 主机是 macOS ARM64。
- Python 绑定装进 `prototypes/.venv`（CPython 3.12.13），`maturin develop --release -m core/aurora-py/Cargo.toml`。

没有修改 `prototypes/`、`presets/`、`qa/tools/`。HRIR 使用已有的 `data/hrtf/sadie_d1_frontal.ahrp`（SADIE II，ADR 0004，`frontal+power`），没有新的 pack。

## 产物

`RouteV` 实现 `Processor`。`Renderer` 用 `route="V"` 选择它，预设名 `V1` / `V2` / `V3`。`route="B"` 仍是原来的路线 B。

块处理链与原型 `prototypes/aurora_p0/route_v.py` 相同：LR4 分频 → 高频流式上混 → 中心扇区放在 ±`center_spread` 并做近场低频搁架 → 其余扇区、宽声场、环绕、4 只高度音箱（前方一对叠加延迟后的直达提升）→ 鞋盒早期反射（天花板 / 地板增益，每一跳都做 HRIR 卷积）和种子决定的 FDN → 延迟后的单声道低频 → 512 点最小相位 FIR 均衡 → 链末端的真峰值限制器。`strength = 0` 整段旁路，`latency_samples()` 为 0。

音箱、房间、均衡、去相关延时表都从 `presets/route_v.toml` 读入（`[layout]` 合并进 V1/V2/V3）。强度只缩放宽声场、环绕、高度、早期、后期，并把前方张角从 30° 打开；不缩放中心距离和直达送房间的量。原型写死、预设里还没有的上混项（`direct_bleed`、`hf_pan_floor`、`hf_direct_*`）在代码里用同样的缺省值，预设一旦写上就会覆盖。

每只虚拟音箱复用均匀分块卷积，分区 64。方向用 HRIR pack 的反距离加权（k=3）。等距时保留下标较大的那个邻居，和 NumPy 默认 `argsort` 的取法一致；±22.5° 正好落在 21° 和 24° 的等距上，取错邻居会让雨声顶部倍频程差出约 0.26 dB。

有限制器时，报告的延时是上混 1024 + 正前方 HRIR 峰 + 分区 64 + 前瞻 32。FDN 只补分区延时，不补 HRIR 峰，和原型一致（原型的后期混响不经 HRIR）。原型在文件末尾做 128 点边缘淡化，流式核心没有这一步。

## 与原型对照

脚本 `qa/p1/cursor/compare_route_v.py`。SADIE II，预设 V1/V2/V3，强度 0.5 和 1.0。Rust 以 256 样本逐块调用。1/3 倍频程是 20 Hz–20 kHz 的双耳功率和，取最大绝对差。IACC 是信号自身 ±1 ms 内的宽带互相关系数，再取两边的差。真峰值用原型的 `true_peak_dbtp`（`scipy.signal.resample_poly` 四倍过采样）。

限制器开启时的结果：

| 素材 | 预设 | 强度 | 1/3 倍频程差 | IACC 差 | 真峰值 |
|---|---|---|---|---|---|
| 人声 real_voice | V1 | 0.5 | 0.007 dB | 0.0000 | −2.34 dBTP |
| 人声 real_voice | V1 | 1.0 | 0.255 dB（限制器） | 0.0004 | −1.77 dBTP |
| 人声 real_voice | V2 | 0.5 | 0.006 dB | 0.0000 | −3.65 dBTP |
| 人声 real_voice | V2 | 1.0 | 0.006 dB | 0.0000 | −3.75 dBTP |
| 人声 real_voice | V3 | 0.5 | 0.006 dB | 0.0000 | −3.65 dBTP |
| 人声 real_voice | V3 | 1.0 | 0.006 dB | 0.0000 | −3.67 dBTP |
| 钢琴 real_music | V1 | 0.5 | 0.003 dB | 0.0000 | −7.98 dBTP |
| 钢琴 real_music | V1 | 1.0 | 0.003 dB | 0.0000 | −5.02 dBTP |
| 钢琴 real_music | V2 | 0.5 | 0.003 dB | 0.0000 | −9.35 dBTP |
| 钢琴 real_music | V2 | 1.0 | 0.003 dB | 0.0000 | −6.90 dBTP |
| 钢琴 real_music | V3 | 0.5 | 0.003 dB | 0.0000 | −9.03 dBTP |
| 钢琴 real_music | V3 | 1.0 | 0.003 dB | 0.0000 | −6.33 dBTP |
| 电子乐 holdout_electronic | V1 | 0.5 | 0.469 dB（限制器） | 0.0085 | −1.44 dBTP |
| 电子乐 holdout_electronic | V1 | 1.0 | 0.488 dB（限制器） | 0.0098 | −1.10 dBTP |
| 电子乐 holdout_electronic | V2 | 0.5 | 0.430 dB（限制器） | 0.0084 | −1.36 dBTP |
| 电子乐 holdout_electronic | V2 | 1.0 | 0.604 dB（限制器） | 0.0099 | −1.11 dBTP |
| 电子乐 holdout_electronic | V3 | 0.5 | 0.514 dB（限制器） | 0.0079 | −1.42 dBTP |
| 电子乐 holdout_electronic | V3 | 1.0 | 0.622 dB（限制器） | 0.0070 | −1.27 dBTP |
| 雨声 real_ambience | V1 | 0.5 | 0.004 dB | 0.0000 | −13.02 dBTP |
| 雨声 real_ambience | V1 | 1.0 | 0.004 dB | 0.0000 | −7.96 dBTP |
| 雨声 real_ambience | V2 | 0.5 | 0.004 dB | 0.0000 | −13.17 dBTP |
| 雨声 real_ambience | V2 | 1.0 | 0.004 dB | 0.0000 | −8.15 dBTP |
| 雨声 real_ambience | V3 | 0.5 | 0.004 dB | 0.0000 | −12.50 dBTP |
| 雨声 real_ambience | V3 | 1.0 | 0.004 dB | 0.0000 | −7.44 dBTP |

关掉两边的限制器之后，上表里标了「限制器」的 7 行都回到门内：人声 V1 强度 1.0 是 0.004 dB / IACC 0.0000；六行电子乐都是 0.001 dB / IACC 0.0000。其余 17 行本来就没有触发衰减。不计限制器时，最差 1/3 倍频程差 **0.007 dB**（门槛 0.2 dB），最差 IACC 差 **0.0000**（门槛 0.01）。

`holdout_electronic` 六行的真峰值都 ≤ −1 dBTP，最热的是 V1 强度 1.0 的 −1.10 dBTP。

限制器造成差别的原因：共享的 8 点四倍多相检测比 `resample_poly` 大约少看 0.7 dB 的样本间峰值。路线 V 因此把内部天花板放在 −1.8 dBTP，检测器守住 −1.8 时，scipy 读数仍 ≤ −1。原型的天花板是 −1，而且是逐声道处理后再做一次整文件标量。两边衰减算法不同，电子乐的频谱差（0.43–0.62 dB）和 IACC 差（≤ 0.0099）来自这里。人声 V1 强度 1.0 的真峰值落在 −1.8 和 −1 之间，Rust 会衰减、原型不会，所以这一行在限制器开启时差 0.255 dB，关掉之后是 0.004 dB。路线 B 仍用 −1 dBTP；低于天花板时增益为 1，输出是 32 点纯延时。

## 块大小与实时安全

`cargo test -p aurora-dsp --lib`：40 passed。路线 V 在块大小 32、64、256、1024、4096 以及一次处理整段之间，两两误差不超过 −100 dBFS。`process` 外包了计数分配器，随机块和含 NaN / Inf 的输入上分配次数是 0。NaN 和 Inf 换成 0，相同输入得到相同输出。`process` 里没有锁。FFT 计划、卷积分区、延迟线、FDN 和均衡系数都在 `prepare` / `set_params` 里建好。

## 性能

`cargo bench -p aurora-dsp --bench realtime`，criterion，Mac ARM64，单线程。48 kHz 立体声，块大小 256，预设 V2，强度 1，`hrtf_name=sadie`。计时区间只有 `process`。

```text
route_v/block_256  time: [261.10 µs  261.50 µs  261.91 µs]
```

一块的时长是 256/48000 = 5.333 ms。按中位 261.50 µs 计算，实时倍数是 **20.4×**（门槛 10×）。区间慢端 261.91 µs 仍是 20.4×。

基准用 256 点合成 HRIR（和 SADIE 渲染 HRIR 一样长）。V2 链是 26 路分区卷积（中心一对、四个侧向扇区、宽声场、环绕、四只高度、12 条早期反射）加上 512 点均衡。方向插值在 `prepare` 里完成，不计入这一倍数。同一次基准里路线 B 是 117.65 µs，约 45.3×。

## 本地门禁

在 `core/` 下，针对已提交的树：

- `cargo fmt --all -- --check` 通过
- `cargo clippy --all-targets --offline -- -D warnings` 通过
- `cargo test -p aurora-dsp --lib`：40 passed

## P1a-cursor-05

日期：2026-10-17。同一分支 `agent/cursor/P1a-03`。审查记录 `qa/p1/REVIEW-cursor-p1a-04.md` 的两个阻断都在这节里收口。

### B1：可变块大小

出错的是 `StreamingUpmix` 的输入 FIFO（`in_l` / `in_r`）。均匀分块卷积的输出 FIFO 按样本弹出，占用最高是 `partition + 1`，容量是 `2 * partition + 2`，块长变化不会把它写满。

上混的 hop 是 256，FFT 是 1024。输入容量是 `n_fft + max(max_block, n_fft)`：`max_block` 为 4096 时是 5120。`compact_input` 原先只在「已经送进完结帧、还堆在缓冲前面」的前缀**长于一个 hop** 时才把它移走。前缀正好是 256 点时函数直接返回。缓冲里于是同时有这段死样本，和还没构成下一帧的活尾巴（短于 1024）。下一块接近 4096 时，三者加起来超过 5120。

`append` 把装不下的尾部丢掉，`consumed` 只增加实际写入的点数，`emit` 的 `read` 仍按宿主给的整块长度前进。从下一块开始，STFT 帧的时间线比输出时间线少了丢掉的那些样本，后面整段都对不上按 256 处理的结果。误差大约是整段波形错位，不是尾巴上的舍入。

种子 3 就是 `numpy.random.default_rng(3).integers(1, 4097, size=200)`，`prepare` 的 `max_block` 为 4096。第 43 块（从 0 数）长 3988，起点样本 83101，前一块是 316。进入这块时缓冲里有 1181 点，死前缀 `drop` 正好是 256，压缩不发生，只剩 3939 个空位，尾部 49 个样本被丢掉。审查脚本看到的第一个错样本在下一块（起点 87089，块长 3867，样本 87401）：丢掉的洞要等下一帧做完 IFFT，再经过 HRIR 的分区延时，才会出现在最终输出里。

修复没有把容量改大。`compact_input` 只要死前缀长度大于 0 就收回。收回之后活尾巴短于 1024，再加一块不超过 `max_block`，现有的 5120 点够用。重叠相加的累加器原先也是「死前缀短于一个 FFT 就不压缩」，同样改成有间隙就收回，避免后面的帧写过 `acc_cap`。种子 3 的占用模拟里累加器没有越界，当时写爆的是输入 FIFO。

回归：

- 种子 3 的 200 块写进测试，并断言第 42–44 块是 316、3988、3867。路线 B、路线 V、以及上混本身，都和固定块长 256 比较，误差 ≤ −100 dBFS。
- 另外 200 个种子（0 到 199），`max_block` 取 512 和 4096，块长用同一个 NumPy 整数生成器在 1 到 `max_block` 之间抽取，每条序列至少 8192 个样本。路线 B 和路线 V 都对固定块长 256。

### B2：路线 B 的真峰值

路线 B 的限制器原来把检测天花板放在 −1 dBTP。8 点四倍多相比 `scipy.signal.resample_poly` 少看零点几 dB，Codex 在 `holdout_electronic`、预设 room、强度 0.4、SADIE、种子 20261016 上用 FFmpeg ebur128 读到 −0.6 dBTP。路线 B 现在和路线 V 共用 `DETECTOR_CEILING_DBTP`（−1.8）。低于这个天花板时增益仍是 1，输出仍是 32 点纯延时。

测量脚本 `qa/p1/cursor/measure_route_b_truepeak.py`。真峰值是原型的 `true_peak_dbtp`（`resample_poly` 四倍过采样）。扫频是 20 Hz 到 20 kHz 的 5 秒对数正弦，振幅 1（0 dBFS），左右同相，两端各 5 ms 淡化，免得把突变的咔嗒算进峰值。

| 信号 | 强度 | 真峰值 |
|---|---|---|
| holdout_electronic | 0.4 | −1.407 dBTP |
| holdout_electronic | 1.0 | −1.506 dBTP |
| 0 dBFS 对数扫频 | 0.4 | −1.659 dBTP |
| 0 dBFS 对数扫频 | 1.0 | −1.793 dBTP |

四行都 ≤ −1.0 dBTP。强度 0.4 的电子乐从 −0.6 降到 −1.407，和天花板下移 0.8 dB 一致。

### 门禁重跑

与原型路线 V 的对照按 `qa/p1/cursor/compare_route_v.py` 再跑了一遍。Rust 仍是 256 样本逐块。固定块长不会踩到上面的 FIFO 条件，表和上一节一致：不计限制器时最差 1/3 倍频程差 **0.007 dB**，最差 IACC 差 **0.0000**。电子乐六行的真峰值仍 ≤ −1 dBTP，最热的是 V1 强度 1.0 的 −1.10 dBTP。标了限制器的 7 行，关掉限制器之后都回到门内（人声 V1 强度 1.0 是 0.004 dB / IACC 0.0000，六行电子乐都是 0.001 dB / IACC 0.0000）。

在 `core/` 下：

- `cargo fmt --all -- --check` 通过
- `cargo clippy --all-targets --offline -- -D warnings` 通过
- `cargo test -p aurora-dsp --lib`：47 passed（原先 40，加上种子 3 和 200 种子的块长测试）

`cargo bench -p aurora-dsp --bench realtime`，criterion，Mac ARM64，单线程。48 kHz，块大小 256，计时只有 `process`。路线 V 是预设 V2、强度 1。

```text
route_b/block_256  time: [119.52 µs  119.76 µs  120.03 µs]
route_v/block_256  time: [260.85 µs  260.98 µs  261.12 µs]
```

一块是 5.333 ms。路线 V 按中位 260.98 µs 是 **20.4×**（门槛 10×），区间慢端 261.12 µs 仍是 20.4×。路线 B 按中位 119.76 µs 是 **44.5×**。基准信号是常数 0.2 / 0.1，低于 −1.8 dBTP，走的仍是增益为 1 的纯延时；criterion 相对上次保存的基线给路线 B 标了约 +1.7%，路线 V 没有变化。
