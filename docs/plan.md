# Aurora Binaural（空间听感音频工具）：可落地方案与执行任务书

版本：v0.2.1
项目名称：Aurora Binaural（仓库 `zzlava/aurora-binaural`，CLI 命令 `aurora`，Python 包 `aurora_binaural`）
修订日期：2026-10-16，Asia/Shanghai
基于：v0.1《完整方案与 GitHub 调研》
适用范围：Mac 本地使用、命令行、图形界面、Linux 远程部署、HTTP API，并为后续实时处理预留路线

角色：

- **Claude**：方案、任务协调、独立验收、Git 仓库与备份
- **Cursor**：DSP 核心与算法相关编码
- **Antigravity**：应用层编码，包括 CLI、API、Worker、GUI 和部署

> 当前状态：方案修订完成；本地 Git 仓库已初始化（P0-0 进行中）；尚未向 Cursor 或 Antigravity 派发任务。下文的命令、目录、接口和参数都是拟定设计，不代表已经实现。所有"待验证"项必须在对应阶段用实际运行结果关闭。

---

## 0. v0.2 相对 v0.1 的主要变更

| 编号 | 变更 | 原因 |
|---|---|---|
| C1 | 角色改为 Claude（统筹、验收、Git）+ Cursor（核心 DSP）+ Antigravity（应用层） | 用户指定；两个编码代理按模块划分所有权，避免互相覆盖 |
| C2 | 新增 **STFT 直达／环境分离（上混）** 作为立体声主路线 | 只做两个虚拟扬声器，对已混好的音乐提升有限，中置会产生梳状染色；AI 分轨成本高，也不适合实时 |
| C3 | 渲染架构定为 **"直达对象 + Ambisonics 床"** 的混合结构 | 声源数量多少不影响双耳卷积的路数；环绕运动和头部追踪都只是旋转；scene 模式可以直接沿用 |
| C4 | 新增音色保护：HRTF 扩散场均衡、低频旁路 | 音乐场景里最主要的质量风险 |
| C5 | "强度"改为映射到感知参数，**禁止用干湿混合实现** | 干声与带 ITD 的双耳信号直接混合会产生梳状滤波 |
| C6 | 补全距离模型、移动声源插值方法、早期反射与 FDN 晚期混响 | v0.1 只有要求，没有算法 |
| C7 | 核心定为 **实时安全的块处理引擎**（Rust），离线 = 用同一引擎更快地跑完 | 为实时化铺路，同时一并解决块边界、尾巴和延时补偿 |
| C8 | 候选库补充 SAF／SPARTA、spaudiopy／pyfar／sofar、Apple AUSpatialMixer（作对比基线） | 调研遗漏 |
| C9 | HRTF 主候选从 MIT KEMAR 改为 KU100／SADIE II／HUTUBS 等，P0 对比后确定 | KEMAR 年代较早，采样率为 44.1 kHz |
| C10 | 交付顺序改为 核心＋CLI → API／Worker → GUI；任务系统精简 | 先过听感关，降低 V1 工程量 |
| C11 | 新增第 15 节"实时化路线" | 用户关注的长期方向 |
| C12 | 新增 Git 工作流与备份规则，由 Claude 负责 | 用户指定 |
| C13 | 项目定名 Aurora Binaural；工作文件夹、仓库名、CLI 命令名同步更新（v0.2.1） | 用户确认 |

---

## 1. 项目目标与结论

开发一个自动工具，把普通单声道或双声道音频处理成适合耳机收听、带方向感和距离感的普通双声道音频，输出 WAV、AAC／M4A 或 MP3。听众不需要专用播放器，也不需要特殊耳机。

使用场景：

- 个人批量处理音乐、配音、环境音和音效。
- 在图形界面里试听不同效果再导出。
- AI 视频制作流程通过 API 提交音频任务，把结果合入影片。
- 部署到远程服务器，供不同设备和自动化工具调用。
- **后续**：实时处理，包括插件、系统级处理、浏览器、直播链路和头部追踪。

结论：

1. 离线 V1 技术上可行。HRTF 读取、卷积、编码都有成熟组件，不需要训练模型。
2. 最大风险是**已混好音乐的听感**：音色染色、发虚、低频冲击变弱。P0 的听感关是硬性门槛。
3. 核心按实时引擎来写。V1 只做离线，但以后加实时形态时不需要重写核心。
4. 估算 18–27 人天工作量（见第 17 节）。Cursor 与 Antigravity 并行，日历时间约 3–4 周，但听感调校不能靠并行压缩。

---

## 2. 需求状态

| 类型 | 内容 |
|---|---|
| 用户已确定 | 普通音频输入，普通 WAV／AAC／MP3 输出，耳机三维听感 |
| 用户已确定 | 同时支持命令行与图形界面 |
| 用户已确定 | 支持远程部署和 API 接入，服务 AI 视频制作流程 |
| 用户已确定 | Claude 负责方案、协调、验收和 Git 备份；Cursor 与 Antigravity 完成编码 |
| 用户关注 | 空间音频算法质量；将来支持实时处理 |
| 本方案决定 | 核心为 Rust 块处理引擎，通过 Python 绑定供服务使用；混合渲染架构；STFT 上混 |
| 待 P0 验证 | 三种算法路线的听感排序；HRTF 数据集；Rust 核心在 Mac ARM64 与 Linux x86_64 上的构建；两个编码代理的本机调用方式 |
| 待实施时确定 | 远程服务器、域名、存储位置、部署凭据、GitHub 私有备份仓库 |

本文是执行基线。选型变化要写进决策记录（`docs/adr/`），不能悄悄缩减用户已确定的功能。

---

## 3. 效果边界

### 3.1 能实现

- 声源位于前方、侧面、后方或一定高度（高度感因人而异）。
- 自然房间感、较宽声场、轻度包围感。
- 声源沿轨迹平滑移动。
- 已混好立体声的中置更稳定、声场更宽，环境成分向侧后方展开。
- 以上效果固化在普通双声道文件中。

### 3.2 不能承诺

- 从已混好的歌曲恢复每件乐器的真实位置。上混只是按声像位置和相关性做统计分离，不是真正的分轨。
- 所有听众都能准确分辨前后、上下和绝对距离。通用 HRTF 存在个体差异。[定位实验论文](https://human-factors.arc.nasa.gov/publications/wenzel_1993_Localization_Head_Related.pdf)
- 固定文件在听众转头时保持声场不动。这只有实时形态（第 15 节）才能做到。
- 手机外放或普通音箱获得与耳机相同的定位效果。
- 把双耳文件自动变成 Dolby Atmos、ADM 或可交互的空间音频。

---

## 4. 版本范围

### 4.1 V1（本次交付）

- 单声道、立体声输入；WAV、AAC／M4A、MP3 输入输出。
- Rust 核心：上混、HRTF 渲染、Ambisonics 床、早期反射、FDN 混响、距离模型、运动、音色保护、响度与真峰值。
- 三个预设加一个强度控制，预设版本化。
- CLI：单文件、批处理、检查、预设列表、远程模式、GUI 启动。
- HTTP API：上传素材、异步任务、状态查询、取消、下载。
- 单节点持久化任务（SQLite）、独立 Worker。
- Web GUI：导入、试听、原音／效果等响度对比、队列、导出。
- Linux Docker 镜像、Compose 示例、OpenAPI 文档、curl／Python 示例。
- 技术、听感和服务三类验收证据。

AAC 优先使用 M4A 容器；原始 `.aac` 只作为输入，是否支持在 P0 确定。界面上要分开显示"格式"与"容器"。

### 4.2 V1.1：已有分轨的场景渲染

旁白、音乐、音效各自设置位置、轨迹、起始时间、音量和混响，按精确时长输出。直接沿用 V1 的对象 + Ambisonics 床架构。预计额外 3–5 人天，V1 引擎通过验收后再重新估算。

### 4.3 V2：实时化（见第 15 节）

插件、浏览器 WASM、Mac 系统级处理、头部追踪、直播接入。不计入 V1 工期。

### 4.4 后续研究

AI 自动分轨（离线高质量模式）、个性化 HRTF、Webhook、对象存储直传、多用户与计费、集群扩容、视频解封装与回封装。AI 分轨初步预计 15–30 人天，完成标准必须包含主观音质验证。

---

## 5. 候选项目与组件

### 5.1 v0.1 已核查的项目（结论保留）

| 项目 | 在本方案中的用途 | 主要局限 | 许可证 |
|---|---|---|---|
| [Audio8D](https://github.com/gcfernando/audio-8d) | CLI／GUI 工作流、批处理、文件保护的参考 | 声学核心是参数化近似；macOS 未测试 | MIT |
| [Spatial Studio](https://github.com/Kumarhq/spatial-studio) | Web＋API 分层、上传→处理→下载流程的参考 | 任务状态在内存里；附带的 HRTF 不随高度变化；用 RMS 冒充 LUFS；混响不可复现；立体声被合成单声道 | MIT |
| [libspatialaudio](https://github.com/videolan/libspatialaudio) | Ambisonics／对象渲染对照 | LGPL 会限制插件和 App Store 分发 | LGPL-2.1+／商业 |
| [libmysofa](https://github.com/hoene/libmysofa) | SOFA 读取（也可以用 Rust 的 sofa crate 或 Python 的 sofar） | 只是数据层 | BSD-3-Clause，数据另计 |
| [Open Headstage](https://github.com/KiloHertzian/Open-Headstage) | Rust 卷积和插件架构参考 | Mac 构建说明未完成 | Apache-2.0 |
| [Steam Audio](https://github.com/ValveSoftware/steam-audio) | 实时双耳与 Ambisonics 备选 | Mac 架构需要实测 | Apache-2.0 |
| [total-reaper-mcp](https://github.com/shiehn/total-reaper-mcp) | 只用于 DAW 实验 | 不是无界面的转换服务 | MIT |
| [IEM Plug-in Suite](https://plugins.iem.at/docs/plugindescriptions/) | 在 DAW 中做听感参照（StereoEncoder、RoomEncoder、BinauralDecoder 用的是 MagLS） | 插件形态，不用于打包 | GPL-3.0 |

### 5.2 v0.2 新增候选（P0 核实）

| 组件 | 用途 | 待核实 |
|---|---|---|
| [Spatial_Audio_Framework (SAF)](https://github.com/leomccormack/Spatial_Audio_Framework) / SPARTA | C 语言 DSP 积木：双耳化、MagLS Ambisonics 解码、SOFA | 各模块的许可证（大部分是宽松许可，个别模块可能不同）、Mac ARM64 构建 |
| spaudiopy、pyfar、sofar（Python） | P0 算法原型、客观测量、参考实现 | 版本与许可证 |
| Apple AUSpatialMixer／系统"空间化立体声" | **听感对比基线**，不打包 | 只能在 Mac 上作为参照 |
| libebur128 或 ffmpeg `ebur128` | 符合 BS.1770 的响度与真峰值测量 | 固定版本 |
| rustfft／realfft、ndarray、PyO3、maturin | Rust 核心与 Python 绑定 | Mac ARM64 与 Linux 构建 |

### 5.3 HRTF 数据集候选

| 数据集 | 特点 | 备注 |
|---|---|---|
| TH Köln Neumann KU100 | Ambisonics 双耳解码常用，音色平衡 | 授权需核实（可能是署名－相同方式共享类） |
| SADIE II（York） | 包含 KU100 与真人，48/96 kHz | 授权需核实 |
| HUTUBS | 真人数据较多，适合以后做个性化选择 | 授权需核实 |
| ARI | 真人数据多 | 授权需核实 |
| MIT KEMAR | 只作参照 | 年代早、44.1 kHz；"MIT" 是机构名，不是 MIT 软件许可证 |

规则：数据必须记录来源、版本、许可证和 SHA-256；不允许静默回退到其他数据；采样率不一致时重采样 HRIR，不重采样音频。

---

## 6. 渲染算法设计（核心）

### 6.1 总体结构

```text
输入解码（float32 PCM，内部 48 kHz）
  │
  ├─ 低频分离（交叉频率约 120–200 Hz，线性相位或互补滤波）──────────┐ 直通，居中
  │                                                                │
  └─ 中高频
       │
       ├─ [立体声] STFT 上混
       │     ├─ 直达成分：按时频点估计的方位 → 对象渲染（HRTF 直接卷积）
       │     └─ 环境成分：去相关 → 编码进 Ambisonics 床（侧后方、扩散）
       │
       ├─ [单声道／分轨对象] 位置与轨迹 → 对象渲染或编码进床
       │
       ├─ 早期反射（鞋盒镜像源，6–20 条）→ 按方向编码进床
       └─ 晚期混响（FDN，双耳相干性按频率控制）→ 双耳输出
                                     │
            Ambisonics 床（3 阶，16 通道）→ MagLS 双耳解码
                                     │
  对象双耳输出 + 床解码输出 + 混响 + 低频直通 → 求和
                                     │
            真峰值限制（−1 dBTP 目标）→ 响度测量 → 编码 → 回读检查 → 原子交付
```

### 6.2 立体声上混（直达／环境分离）

- 在 STFT 域工作，起始参数为窗长 1024、跳长 256、sqrt-Hann 窗，P0 调整。
- 对每个时频点估计：
  - **声像位置**：用左右能量比得到 panning index（参考 Avendano & Jot 2004）。
  - **相关性**：用左右互相关得到直达和环境的比例；可以和 PCA 主成分／环境分离对照。
- 声像位置映射到方位角，例如 [−1, 1] 映射到 ±30°×宽度系数。预设控制展开角度，默认不超过 ±60°。
- 中置（声像接近 0）直接渲染在 0°，不再靠 ±30° 两路 HRTF 叠出幻象中置。
- 环境成分去相关（全通滤波器组或 velvet noise）后编码到 ±90°–±150° 的扩散区域。
- **伪影控制**：掩码沿时间和频率做平滑；限制掩码的最大变化率；加入"上混程度"参数，为 0 时退化为纯虚拟扬声器模式。
- 能量守恒：没有空间处理时，直达与环境之和应能重建输入（验收项）。

### 6.3 对象渲染（HRTF 直接卷积）

- HRIR 预处理：
  1. 转成最小相位，ITD 单独提取为分数延时。
  2. 做**扩散场均衡**（按全部方向的平均功率谱求逆）；或者以正前方为参考做"正前方均衡"。P0 对比两种做法。
  3. 截断到 256–512 抽头，48 kHz。
- 运动插值：
  - ITD 用连续插值，分数延时用 Lagrange 或 Thiran 滤波实现。
  - 幅度部分按邻近方向插值（三角网格或球面加权）。
  - 滤波器更新时对相邻两块做交叉淡化，避免切换时的咔嗒声。
- 方向数据每块更新一次，块内参数线性平滑。

### 6.4 Ambisonics 床

- 3 阶 ACN／SN3D（16 通道），解码前转成 N3D 或按库约定转换，在适配层统一。
- 双耳解码用 **MagLS**（高频只匹配幅度，减少低阶 Ambisonics 的发糊和染色），可以用 SAF 的实现或对照 IEM BinauralDecoder 的参数。
- 环绕运动（`orbit`）和以后的头部追踪都用球谐旋转矩阵，按块平滑插值。

### 6.5 房间模型

- **早期反射**：鞋盒房间镜像源法，1–2 阶，6–20 条反射，带墙面吸收系数；每条反射有自己的延时、增益和方向，编码进 Ambisonics 床。
- **晚期混响**：8–16 线 FDN，正交反馈矩阵，按频带设置衰减时间（T60）；左右耳输出的双耳相干性按扩散场模型随频率变化。所有随机量（延迟线长度、去相关滤波器）由显式 seed 生成。
- 预设控制：房间大小、T60、早期反射级别、DRR。
- 备选：实测 BRIR。只作为听感参照，V1 默认不使用。

### 6.6 距离模型

| 线索 | 实现 |
|---|---|
| 直达声增益 | 1/r，参考距离 1 m，最小距离 0.25 m |
| 空气吸收 | 随距离增大的一阶或二阶低通 |
| 直达混响比（DRR） | 直达声按 1/r 衰减，混响声级保持不变 |
| 近场（< 1 m） | 可选的 ILD 增强（V1 可以先不做，记录为局限） |
| 多普勒 | V1 不做；运动速度有上限，避免可闻音高变化 |

### 6.7 音色保护

- 低频旁路：分频以下的低频居中直通，与处理后的中高频保持相位对齐（分频滤波器要互补，验收时测相位和幅度）。
- HRTF 扩散场均衡（见 6.3）。
- 自然空间预设在正前方的总体频响偏差目标控制在 ±2 dB 以内（1/3 倍频程平滑后），P0 校准。
- 预留耳机均衡接口（例如 AutoEQ 格式），V1 默认关闭。

### 6.8 强度参数映射

**禁止**用 `out = (1−s)·dry + s·wet` 这种干湿混合实现强度。

`strength ∈ [0, 1]` 映射到下列参数，每个参数各自平滑插值：

| 参数 | s = 0 | s = 1（示例，P0 校准） |
|---|---|---|
| 上混展开角 | ±30°（虚拟扬声器） | ±60° |
| 环境成分比例 | 0 | 预设最大值 |
| 早期反射级别 | 0 | 预设值 |
| DRR 偏移 | 0 dB | 预设值 |

`s = 0` 时整条空间处理链被**旁路**，输出等于输入（只允许已声明的重采样和编码差异）。

### 6.9 坐标与单位

- 和 SOFA 常用定义保持一致：前方 +X、左方 +Y、上方 +Z；方位角向左为正，仰角向上为正，单位为度；距离单位为米。
- 坐标只在 `core/coords` 里定义一次，各库的约定在适配层转换。
- 方向测试：0°、±30°、±90°、180°、仰角 ±45° 下的 ITD 符号与 ILD 符号都要有自动化检查。

### 6.10 默认预设

| 预设 ID | 界面名称 | 算法组合 | 默认强度 |
|---|---|---|---|
| `room` | 自然空间 | 上混（窄）+ 小房间早期反射 + 短 FDN + 音色保护；中置稳定 | 0.4 |
| `surround` | 包围感 | 上混（宽）+ 环境成分推向侧后方 + 中等房间 | 0.5 |
| `orbit` | 缓慢环绕 | 整个声场或单一对象做 Ambisonics 旋转；转速上限约 10°/s，可配置 | 0.3 |

预设的具体数值在 P0/P5 的听感阶段确定，存为 `presets/*.toml`，带版本号。不能把"声音更大"当成"效果更好"，所有对比都要等响度。

---

## 7. 核心引擎实现约束（实时安全）

核心是 Rust 写的块处理器。离线处理 = 一块一块地把整个文件喂给这个处理器。

```rust
// 拟定接口，P1 确定
trait Processor {
    fn prepare(&mut self, sample_rate: u32, max_block: usize, config: &RenderConfig) -> Result<()>;
    fn set_params(&mut self, params: &Params);           // 无锁传递，块内平滑
    fn process(&mut self, input: &[&[f32]], output: &mut [&mut [f32]], n: usize);
    fn latency_samples(&self) -> usize;                  // 报告自身延时
    fn tail_samples(&self) -> usize;                     // 报告混响尾巴长度
    fn reset(&mut self);
}
```

硬性规则：

- `process` 内部不分配内存、不加锁、不做 IO、不打日志、不 panic。
- 所有参数变化都要平滑，不能出现阶跃。
- 前瞻量（lookahead）有上限，延时通过 `latency_samples` 报告；离线模式据此补偿，保证时间线对齐。
- 卷积用均匀分块的 FFT 卷积（短 IR）；长 IR 用非均匀分块卷积。
- 同样的输入、参数和 seed，在任意块大小（64／256／1024／整文件）下输出一致（浮点误差在容差内）。这是验收项。
- 对外提供 C ABI 和 Python 绑定（PyO3＋maturin）。CLI、API、Worker 都只通过 Python 绑定调用核心。

P0 用 Python（numpy／scipy／spaudiopy）做算法原型；P1 由 Cursor 移植到 Rust，并用 Python 原型的输出做数值回归对照。

---

## 8. 处理链与时间同步

```text
探测 → 解码为 float32 → 重采样到 48 kHz（记录原始采样率）→ 声道映射
→ 核心渲染（补偿 latency_samples）→ 尾巴策略 → 真峰值限制
→ 响度测量（BS.1770）→ 编码 → 回读解码检查 → 报告 → 原子交付
```

- 不删除开头或中间的静音，不改速、不改调。
- `timing.mode=preserve`：输出的样本数等于解码后的输入；`tail=trim` 时在末端做短淡出，避免突然切断。
- `tail=preserve`：允许加上混响尾巴，报告里记录多出来的样本数。
- WAV 48 kHz／24 bit 是视频流程的首选中间格式；AAC／MP3 要回读验证编码延时和填充（priming、padding）。
- 不满足同步容差的格式组合要明确提示用户改用 WAV，不能谎报同步通过。

---

## 9. 命令行设计

命令名 `aurora`：

```bash
aurora convert input.mp3 --preset room --strength 0.4 --output output.wav
aurora batch ./inputs --preset surround --format m4a --output-dir ./exports
aurora presets --json
aurora inspect input.wav --json
aurora compare input.wav --preset room --output-dir ./ab    # 生成等响度的原音／效果对比
aurora gui
aurora convert input.wav --server https://audio.example.com --preset room --output out.wav
aurora job status JOB_ID --server https://audio.example.com --json
```

要求：

- 默认本地处理；指定 `--server` 后走上传和异步 API。
- stdout 输出结构化 JSON，进度和日志写到 stderr。
- API Key 只从环境变量或配置文件读取。
- CLI 帮助、GUI 选项和 API 校验都由同一个参数模型生成（Pydantic schema）。
- 批处理中每个文件独立记录结果；默认不覆盖已有文件。
- 退出码：0 成功；2 参数错误；3 输入问题；4 处理失败；5 远程错误；130 取消。

---

## 10. HTTP API 合同

| 方法与路径 | 作用 |
|---|---|
| `GET /health` | 进程存活检查 |
| `GET /ready` | 检查任务存储、Worker、HRTF 数据是否就绪 |
| `GET /v1/presets` | 返回预设、参数范围和版本 |
| `POST /v1/assets` | 流式上传素材，返回 asset ID 和探测信息 |
| `POST /v1/jobs` | 创建任务，返回 202 和 job ID |
| `GET /v1/jobs/{id}` | 查询状态、进度、结果元数据和错误 |
| `POST /v1/jobs/{id}/cancel` | 取消任务，可重复调用 |
| `GET /v1/jobs/{id}/result` | 下载结果 |
| `GET /v1/jobs/{id}/report` | 下载处理报告（JSON） |

创建任务示例：

```json
{
  "api_version": "v1",
  "mode": "enhance",
  "input": {"asset_id": "asset_example"},
  "preset": {"id": "room", "version": "1"},
  "strength": 0.4,
  "seed": 20261016,
  "output": {"format": "wav", "sample_rate": 48000, "bit_depth": 24},
  "timing": {"mode": "preserve", "tail": "trim"}
}
```

状态：`queued`、`running`、`succeeded`、`failed`、`canceled`。取消中的过渡状态作为 `running` 的一个子字段，不单独算一个状态。

- 支持 `Idempotency-Key`：同一个 key 加相同参数返回原任务；同一个 key 加不同参数返回 409。
- 只有在编码、回读、报告和原子写入全部完成后，任务才标记为成功。
- 错误返回稳定的错误码、可读信息和 `retryable` 字段。

**结果报告**至少包含：

- 输入输出的 SHA-256、格式、采样率、声道数、样本数
- 引擎版本、Git 提交号、HRTF 名称／版本／哈希
- 完整参数、预设版本、seed
- 引擎延时补偿值、尾巴策略
- 处理前后的 BS.1770 响度和真峰值
- 处理耗时、尝试次数、过期时间

---

## 11. 远程部署（精简版单节点）

- 组件：FastAPI（同时提供 API 和静态 GUI）、一个 Worker 进程、SQLite 数据库、素材与结果目录。用 Docker Compose 交付。
- 任务领取：Worker 用短事务把任务从 `queued` 改成 `running` 并写入租约时间；按时续租。启动时把租约过期的任务回收为 `queued`。重试上限为 2 次。
- 每次尝试使用独立的临时目录，结果原子移动到最终位置。
- 取消：Worker 按块检查取消标志（核心是块处理，天然能在块边界停下），并清理临时目录。
- 资源假设为 2–4 vCPU、4–8 GB 内存，P0/P5 用实测替代这个假设。
- 本地默认只监听 127.0.0.1；远程通过 HTTPS 反向代理访问，使用 API Key 鉴权。
- 上传大小、时长、超时、保留期限都可以通过环境变量配置。
- 镜像里包含固定版本的 HRTF 数据；运行时不下载任何渲染数据。

Redis、PostgreSQL、对象存储和多节点都不是 V1 的前置要求，只需保留任务存储的接口。

---

## 12. 图形界面

主流程：**导入 → 选择预设与强度 → 生成试听片段 → 等响度 A/B 对比 → 导出。**

- 拖入文件、任务列表、本地／远程模式切换。
- 试听片段和最终渲染使用同一核心、同一预设版本和 seed；运动效果按片段在原音频中的时间计算位置。
- A/B 切换做等响度匹配（BS.1770），无缝切换。
- 调试信息放在折叠的开发者区域。
- V1 不做原生 `.app`。浏览器内的实时预览属于 V2（WASM）。

---

## 13. AI 视频制作接入

```text
AI 生成配音／音乐／音效 → 上传 asset → 创建 job → 轮询状态 → 下载音频和报告 → 外部剪辑流程合入视频
```

- 首版正式接入方式是轮询；Webhook 在后续版本加入（需要签名、重试和去重）。
- 推荐中间格式是 WAV 48 kHz／24 bit，`timing.mode=preserve`。
- 已有分轨的 scene 模式在 V1.1 实现，协议沿用 v0.1 第 13.3 节的草案；坐标和单位遵循本文 6.9 节。

---

## 14. 角色、分工与协作方式

### 14.1 职责

| 角色 | 职责 | 不负责 |
|---|---|---|
| 用户 | 效果偏好、使用场景、听感最终确认、部署目标、授权决定 | — |
| **Claude** | 维护方案和 ADR；拆分任务、编写任务书；派单与跟进；代码审查；独立运行检查和验收；生成和整理验收证据；维护 `qa/` 下的验收脚本；**Git 仓库管理与备份**；向用户汇报 | 不编写产品代码（`core/`、`cli/`、`api/`、`worker/`、`web/`、`deploy/`） |
| **Cursor** | P0 算法原型；Rust DSP 核心（`core/`）；Python 绑定；核心的单元测试和数值回归测试；预设数值文件 | 应用层 |
| **Antigravity** | CLI（`cli/`）、API（`api/`）、Worker（`worker/`）、Web GUI（`web/`）、Docker 与部署（`deploy/`）、应用层测试 | 核心 DSP |

**接口边界**：Cursor 交付 Python 绑定的接口文档 `docs/core-api.md` 和一个可用的 wheel；Antigravity 只通过这个接口调用核心。接口变更必须先由 Claude 记录 ADR，并通知双方。

### 14.2 本机调用方式（P0 第一步验证）

| 代理 | 拟定调用方式 | 备用方式 |
|---|---|---|
| Cursor | Cursor CLI（命令行代理，例如 `cursor-agent`，P0 核实名称、版本和非交互参数） | Claude 通过电脑控制在 Cursor 界面里粘贴任务书 |
| Antigravity | 先查找是否有 CLI 或自动化接口 | Claude 通过电脑控制在 Antigravity 界面里粘贴任务书 |

在实际调通之前，不得声称已经连接。如果只能通过界面操作，每次派单和收取交付都要截图或保存日志，作为记录。

### 14.3 派单与交付

每份任务书存放在 `docs/tasks/<阶段>-<代理>-<编号>.md`，内容包括：目标、范围、允许修改的目录、输入输出、接口约束、需要交付的文件、检查命令和验收标准。

代理每次交付需要提供：

- 改动摘要与文件列表
- 实际执行过的命令及其退出码和日志
- 样片、参数、数据哈希、测量报告（如果涉及）
- 未完成事项和已知局限

Claude 独立复现检查，不只转述代理的报告。发现问题时，提供最小复现、期望行为和优先级，再交回对应代理修复。

### 14.4 防冲突规则

- 每个代理只在自己负责的目录里修改，并且在自己的 Git 工作树和分支上工作（见 14.5）。
- 跨目录的修改（例如 `pyproject.toml`、CI 配置）由 Claude 协调，同一时间只允许一个代理修改。
- 两个代理不能同时修改同一个分支。

### 14.5 Git 工作流与备份（Claude 负责）

**仓库初始化（P0 第 0 步）**

- 本地路径：`~/Desktop/leo/Aurora Binaural`（用户已指定；路径含空格，所有脚本必须给路径加引号）。
- `git init`，主分支为 `main`；配置 `.gitignore`（排除构建产物、虚拟环境、临时渲染结果、`.env`）。
- 用 **Git LFS** 管理 HRTF 数据（`.sofa`）、参考样片和验收音频；在 `.gitattributes` 里登记。
- GitHub **私有**仓库 `zzlava/aurora-binaural` 作为远程备份。
- 需要用户在桌面应用里授予该文件夹访问权限，并**允许删除**。Git 在提交和切换分支时要删除锁文件和被替换的文件，没有删除权限时会留下 `.lock` 文件，导致后续操作失败。

**分支与工作树**

```text
main                        只合并已经通过 Claude 验收的内容
agent/cursor/<任务编号>       Cursor 的工作分支（独立 git worktree）
agent/antigravity/<任务编号>  Antigravity 的工作分支（独立 git worktree）
qa/<任务编号>                Claude 的验收脚本和记录
```

**提交与合并规则**

- 代理在自己的分支上提交；Claude 审查和验收通过后，用 `--no-ff` 合并到 `main`，合并说明里写上验收记录的路径。
- 每个阶段验收通过后打标签：`p0-accepted`、`p1-accepted`……，以及 `v1.0.0`。
- 禁止 `force push` 到 `main`；禁止改写已经推送的历史；禁止提交密钥（提交前用 `gitleaks` 或等效工具扫描）。
- 提交信息格式：`<范围>: <摘要>`，并注明代理来源（例如 `Agent: Cursor`）。

**备份频率**

- 每次合并到 `main` 以后，立即推送 `main` 和标签。
- 工作日结束时，推送所有活跃的代理分支（只作为备份，不代表已验收）。
- 每个阶段验收后，额外生成一份 `git bundle`，放到用户指定的备份目录，作为脱离 GitHub 的离线副本。
- 每周检查一次：远程与本地是否一致、LFS 对象是否完整（`git lfs fsck`）。

**恢复演练**：P6 时从远程或 bundle 克隆到新目录，按文档构建并跑通验收，作为交付条件之一。

### 14.6 项目目录

```text
aurora-binaural/          （本地文件夹名为 "Aurora Binaural"）
  core/            Rust DSP 核心与 Python 绑定（Cursor）
  prototypes/      P0 Python 算法原型（Cursor）
  cli/             命令行（Antigravity）
  api/             HTTP API（Antigravity）
  worker/          任务执行、取消与恢复（Antigravity）
  web/             图形界面（Antigravity）
  deploy/          Dockerfile、Compose、环境变量模板（Antigravity）
  presets/         版本化预设（Cursor 提供数值，Claude 验收）
  data/            HRTF 数据（LFS）及来源与授权说明
  qa/              验收脚本、素材清单、测量与听感记录（Claude）
  docs/            方案、ADR、任务书、API 文档、用户与部署文档（Claude 主维护）
```

---

## 15. 实时化路线（V2，不计入 V1 工期）

### 15.1 前提

第 7 节的实时安全核心是唯一前提。只要 V1 遵守这些约束，实时形态就只是换一层外壳。

### 15.2 延时预算（48 kHz，估算值，V2 实测）

| 环节 | 延时 |
|---|---|
| 音频缓冲（256 帧） | 约 5.3 ms |
| HRTF／短 IR 分块卷积 | 1 个缓冲块 |
| 长混响（FDN 或非均匀分块卷积） | 不增加额外延时 |
| STFT 上混（窗长 1024） | 约 21 ms |
| **端到端** | **约 25–40 ms** |

这个延时适合听音乐、看视频和直播（直播可以在音画之间补偿偏移）；给演唱者做实时返听偏高，那种场景可以关闭上混，只用对象渲染，把延时降到 10 ms 以内。

### 15.3 实时形态（按落地难度排序）

| 形态 | 实现方式 | 待验证 |
|---|---|---|
| DAW 插件（AU／VST3／CLAP） | Rust nih-plug 或 JUCE 包装核心 | Mac 签名与 AU 验证 |
| 浏览器实时预览 | 核心编译为 WASM，在 AudioWorklet 里运行；GUI 可以实时调参 | WASM SIMD 性能 |
| Mac 系统级处理 | Core Audio process tap（macOS 14.2 及以上）或虚拟声卡截获系统音频，处理后输出到耳机 | API 权限与稳定性 |
| 头部追踪 | 读取 AirPods 姿态（CMHeadphoneMotionManager），对声场做反向旋转：床用球谐旋转，对象重新计算方向 | 姿态延时、漂移校正 |
| 直播／推流 | OBS 插件，或接入现有音视频处理管线 | 音画同步补偿 |

### 15.4 实时 AI 分轨的现实

Demucs 这类模型不适合低延时场景。轻量的实时分离模型在研究上已经有，但通常要付出几十到几百毫秒的延时，并且音质明显下降。实时路线以 STFT 上混为主；AI 分轨只作为离线的高质量模式。

### 15.5 授权对实时形态的影响

插件和 App Store 分发要避免 LGPL 静态链接和 GPL 依赖。核心依赖优先选宽松许可的库（自研代码、Steam Audio、SAF 中宽松许可的模块）；libspatialaudio 和 IEM 只作为听感和数值对照。

---

## 16. 验收标准

### 16.1 素材矩阵

- 内容：人声、流行、古典／原声、电子、环境音、短促音效、本身已有明显空间效果的素材。
- 技术边界：单声道／立体声；44.1／48／96 kHz；静音；极短片段；突发声；长文件（≥ 60 分钟）；损坏文件；非 ASCII 路径；取消；批量中部分失败。
- 素材必须有可用授权；来源、用途和哈希登记在 `qa/assets.md`。

### 16.2 算法与技术

- 输出的编码、容器、采样率和声道数与请求一致，能完整解码。
- 没有 NaN 或 Inf，没有非预期削波、可闻接缝或意外截断。
- **块大小一致性**：块大小为 64／256／1024 或整文件时，输出差异在容差以内（例如 −100 dBFS 以下）。
- **方向**：标准位置下 ITD 和 ILD 的符号正确；仰角映射有测量证据。
- **上混能量守恒**：上混后不做空间处理时，能以容差重建输入。
- **低频旁路**：分频点附近的合成幅度偏差在 ±0.5 dB 以内。
- **音色**：`room` 预设在正前方的频响偏差在 ±2 dB 以内（1/3 倍频程平滑）。
- **强度为 0**：PCM 与输入在容差内一致。
- **时间线**：`preserve` 模式下 WAV 的样本数等于输入；压缩格式回读后验证编码延时处理。
- 真峰值不超过 −1 dBTP（编码后回读测量）；响度使用 BS.1770 方法，不能用 RMS 代替。
- 同版本、同参数、同 seed 时，CLI、GUI、API 的 PCM 结果一致。
- **客观空间指标**：记录双耳互相关（IACC）随强度的变化，验证强度参数确实在改变空间感。

### 16.3 听感

- 采用类 MUSHRA 方法：包含隐藏参考（原声）、低质量锚点（简单左右声像的 8D 效果）、三种算法路线，以及 Apple 空间化立体声基线。
- 所有对比等响度。
- 分别评价空间感、主体清晰度、音色变化、运动平滑度、听觉疲劳。
- 目标是至少三位试听者、两类耳机；如果只有单人试听，必须注明是单人结果。
- 前后和高度的辨识结果只代表实际参与的试听者。
- P0 通过条件：在音乐素材上，至少一条路线在"空间感"上明显优于原声，同时"音色"和"清晰度"不明显劣于原声。

### 16.4 服务与恢复

- API 提交后立即返回；无效参数、无效素材、无效 Key、同一个 key 不同参数都得到稳定的错误。
- Worker 崩溃、API 重启、写入失败、下载过期都能复现，并且行为可以解释。
- 重试不会产生互相覆盖的结果；取消会真正停止计算。
- 在新环境里按文档能启动；真实服务器部署需要一台外部客户端的验证记录。

### 16.5 状态用语

状态分为：设计完成 → 代码实现 → 局部验证 → 完整本地验证 → 容器验证 → 真实远程部署验证 → 听感通过。没有运行过的检查不能标记为通过。每个状态变化都要对应一个 Git 提交或标签，以及 `qa/` 下的记录。

---

## 17. 阶段计划

| 阶段 | 负责 | 工作内容 | 通过条件 | 估算 |
|---|---|---|---|---:|
| **P0-0 准备** | Claude | 建仓库、LFS、GitHub 私有备份、目录骨架、调通 Cursor 和 Antigravity 的调用方式、素材清单 | 仓库已推送；两个代理都至少完成一次往返派单 | 0.5–1 |
| **P0 算法对比** | Cursor（原型）／Claude（验收） | Python 原型：A 虚拟扬声器、B STFT 上混＋HRTF、C 上混＋3 阶 Ambisonics＋MagLS；HRTF 数据集对比；扩散场均衡与正前方均衡对比；Apple 基线样片 | 等响度样片矩阵；客观指标；听感记录；选定路线与 HRTF；ADR | 3–4 |
| **P1 Rust 核心** | Cursor | 块处理器、上混、对象渲染、Ambisonics 床、房间模型、距离、音色保护、强度映射、Python 绑定 | 16.2 中全部算法项；与 P0 原型的数值回归；Mac ARM64 和 Linux 都能构建 | 5–8 |
| **P2 CLI** | Antigravity | 转换、批处理、检查、对比、JSON 输出、退出码、远程参数 | 命令能直接运行；批处理结果可被程序解析 | 1–2 |
| **P3 API／Worker／部署** | Antigravity | 素材、任务、SQLite 租约、取消、幂等、鉴权、OpenAPI、Docker | 16.4 全部；容器能启动 | 3–4 |
| **P4 GUI** | Antigravity | 文件流程、试听、等响度 A/B、队列、本地／远程模式 | 人工完整操作通过；与 CLI 结果一致 | 2–3 |
| **P5 质量与调校** | Cursor＋Antigravity／Claude（验收） | 完整素材矩阵、编码回读、同步、恢复、性能、预设调校 | 独立检查记录；问题闭环 | 3–4 |
| **P6 交付** | Claude 主导 | 固定版本、文档、示例、许可清单、恢复演练 | 从远程或 bundle 克隆到新环境能复现 | 1–2 |
| **合计** | | | | **18–27 人天** |

- P2 可以在 P1 交付绑定接口的桩（stub）后提前开始，与 P1 并行。
- **P0 是硬门槛**：如果在音乐素材上没有任何路线达到 16.3 的通过条件，先调整算法、HRTF 或预设，不推进后续阶段。届时 Claude 向用户汇报，并建议是否缩小 V1 范围（例如先只针对配音和音效）。
- 估算假设：复用现成库，不训练模型，不做完整的物理场景模拟。引擎构建失败或听感不通过时更新工期，不用删减验收项来维持进度。

---

## 18. 交付清单

- 完整源码、固定的依赖／引擎／HRTF 版本、Git 历史和阶段标签
- CLI、本地／远程 GUI、HTTP API
- Dockerfile、Compose 示例、不含真实密钥的环境变量模板
- 快速开始、参数、API、部署、故障排查、恢复文档
- curl／Python 集成示例
- 对比样片、素材来源、处理报告、技术和听感验收记录
- 性能记录（硬件、时长、格式、并发等条件）
- 第三方代码、二进制、数据集的来源与授权清单
- ADR、已知局限、V1.1／V2 计划
- 远程 Git 备份和离线 bundle

未测试的服务器部署、未实现的 scene 模式、实时形态和 AI 分轨，都不能写成 V1 已完成的能力。

---

## 19. 首轮任务书（拟定，尚未发送）

### 19.1 给 Cursor：P0 算法对比原型

```text
你负责 Aurora Binaural（空间听感音频工具）的 DSP 核心与算法原型。Claude 负责方案、协调、验收与 Git，
Antigravity 负责应用层。只在 prototypes/、core/、presets/ 内修改，在分支
agent/cursor/P0-01 的独立工作树中工作。

目标：用 Python（隔离虚拟环境，不改全局 Python）实现三条渲染路线的离线原型，
在同一组素材上做对比：
A. 立体声 → 两个 ±30° 虚拟扬声器 → HRTF + 小房间
B. STFT 直达/环境上混 → 直达成分按方位做 HRTF 渲染，环境成分去相关放到侧后方 + 房间
C. 同 B，但环境成分、反射和环绕运动都走 3 阶 Ambisonics + MagLS 双耳解码
三条路线都必须做低频旁路和 HRTF 均衡（对比扩散场均衡与正前方均衡）。
强度参数按方案 6.8 节映射，禁止用干湿混合实现。

HRTF：对比至少两个实测数据集（KU100、SADIE II 等），记录来源、许可证、版本和
SHA-256。缺少数据时直接报错，不允许回退到合成数据。

交付：
1. prototypes/ 下可复现的脚本和锁定的依赖；
2. qa/p0/ 下的等响度样片（人声、音乐、环境音 × 三条路线 × 两个强度）；
3. 客观测量：块边界、ITD/ILD 方向测试、正前方频响偏差、IACC、BS.1770 响度/真峰值；
4. 每个样片的处理参数和数据哈希；
5. 实际运行过的命令、结果和失败项；
6. 对 P1 Rust 核心接口（方案第 7 节）的修改建议。

不要把 README、静态检查或测试文件的存在视为功能验证。
不要提交到 main，不要推送，不要删除或覆盖已有文件。
```

### 19.2 给 Antigravity：P0 应用层预研

```text
你负责 Aurora Binaural（空间听感音频工具）的应用层：CLI、API、Worker、GUI、部署。Claude 负责方案、
协调、验收与 Git，Cursor 负责 DSP 核心。只在 cli/、api/、worker/、web/、deploy/
内修改，在分支 agent/antigravity/P0-01 的独立工作树中工作。

P0 只做准备工作，不实现完整功能：
1. 搭建 Python 项目骨架（pyproject、隔离环境、Ruff、mypy、pytest）；
2. 按方案第 9、10 节，用 Pydantic 定义统一的参数模型，并生成 JSON Schema；
3. 写一个桩核心（只做直通），打通 "CLI convert → 解码 → 桩核心 → 编码 → 回读 → 报告"，
   验证 WAV/M4A/MP3 的样本数和编码延时处理；
4. 验证 Mac ARM64 本地运行，以及 Linux 容器内的 ffmpeg 构建路线；
5. 交付：命令、日志、测出的编码延时，以及对 P2/P3 的修改建议。

不要实现 DSP，不要修改 core/。不要提交到 main，不要推送，不要删除或覆盖已有文件。
```

---

## 20. 启动检查表

- [x] 项目路径已确定并授予访问与删除权限
- [ ] GitHub 私有仓库 `zzlava/aurora-binaural` 创建并首次推送
- [ ] 用户指定离线 bundle 的备份目录
- [ ] Claude 核实本机是否有 Rust 工具链、Python、ffmpeg、git-lfs、gh；缺少的由用户确认后安装
- [ ] Claude 调通 Cursor 与 Antigravity 的调用方式（CLI 或界面），记录到 `docs/agents.md`
- [ ] 准备有授权的人声、音乐、环境音素材，登记到 `qa/assets.md`
- [ ] 确认试听人员和耳机（至少记录型号）
- [ ] 远程服务器确定之前，只交付可部署的产物，不虚构上线地址

---

## 21. 参考来源

v0.1 已核查（2026-10-16）：

- [Audio8D](https://github.com/gcfernando/audio-8d)、[Spatial Studio](https://github.com/Kumarhq/spatial-studio)、[libspatialaudio](https://github.com/videolan/libspatialaudio)、[libmysofa](https://github.com/hoene/libmysofa)、[Open Headstage](https://github.com/KiloHertzian/Open-Headstage)、[Steam Audio](https://github.com/ValveSoftware/steam-audio)、[total-reaper-mcp](https://github.com/shiehn/total-reaper-mcp)
- [IEM 插件说明](https://plugins.iem.at/docs/plugindescriptions/)、[EAR Production Suite](https://ear-production-suite.ebu.io/)、[Demucs](https://github.com/facebookresearch/demucs)、[FFmpeg 滤镜文档](https://www.ffmpeg.org/ffmpeg-filters.html#sofalizer)
- [MIT KEMAR 数据使用声明](https://github.com/hoene/libmysofa/blob/main/tests/LICENSE.MIT_KEMAR_pinnae)

v0.2 新增，**待 P0 核实**（版本、许可证、构建）：

- [Spatial_Audio_Framework](https://github.com/leomccormack/Spatial_Audio_Framework)
- spaudiopy、pyfar、sofar（PyPI）
- TH Köln KU100、SADIE II、HUTUBS、ARI 的 HRTF 数据集
- 方法文献：Avendano & Jot（2004）的立体声上混；Schörkhuber、Zaunschirm & Höldrich（2018）的 MagLS 双耳 Ambisonics 解码；Jot 等人的 FDN 混响；ITU-R BS.1770 响度测量

本机信息（v0.1 只读检查）：Darwin ARM64；Homebrew 版的 ffmpeg 提供 `headphone`、`afir`、`loudnorm`、`alimiter` 等滤镜，不提供 `sofalizer`。
