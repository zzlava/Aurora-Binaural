# ADR 0001：项目名称与基础架构

日期：2026-10-16
状态：已接受

## 决定

1. 项目名称 Aurora Binaural；仓库 `zzlava/aurora-binaural`（私有）；CLI 命令 `aurora`；Python 包 `aurora_binaural`。
2. 核心为实时安全的 Rust 块处理引擎，通过 PyO3 提供 Python 绑定；离线渲染 = 用同一引擎跑完整个文件。
3. 渲染架构为"直达对象 + 3 阶 Ambisonics 床 + MagLS 双耳解码"，立体声输入走 STFT 直达／环境上混。
4. 角色：Claude 负责方案、协调、验收与 Git；Cursor 负责 DSP 核心；Antigravity 负责应用层。

## 依据

见 docs/plan.md 第 0、6、7、14、15 节。

## 待验证

P0 的算法对比可能修改第 3 条；修改时新增 ADR，不直接改写本记录。
