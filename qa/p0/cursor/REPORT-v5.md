# P0-cursor-07：默认预设改为 V3

日期：2026-10-17。分支 `agent/cursor/P0-05`。只改了 `presets/route_v.toml` 的目录元数据。音箱、房间、均衡和延时数字与改前相同，共 55 个 DSP 键逐值一致。

## 目录格式

文件头注释写明 Codex 预设目录要读的四个字段。两个 DSP 加载器都不使用它们。

- 根字段 `default_preset = "V3"`。产品默认是这一组。
- 每组预设的 `name` 面向用户：V3「沉浸·头顶环绕（推荐）」，V2「沉浸·人声贴近」，V1「开阔·人声稍远」。各有一句中文 `description`。
- 只有 V3 写了 `default_strength = 1.0`，对应第 3 轮试听强度。V1、V2 不写这一项，目录仍按原来的 0.5 回退。

现有 `aurora_binaural.catalog.load_catalog` 已经读到新的名称、说明和 V3 的默认强度。它还不会读根上的 `default_preset`，产品默认仍由代码里的 `DEFAULT_PRESET_ID` 决定。

## 解析与输出

同一段 8192 样本的立体声（种子 `20261016`，幅度 0.05），SADIE II，块长 256，限制器关掉。改前、改后各跑一遍。

- 原型 `map_route_v`：V1／V2／V3，强度 0、0.5、1.0，HRTF `sadie` 与 `thk_ku100`，18 组映射参数的 SHA-256 一致。
- 原型 `Processor.process_file`：V1／V2 的 0.5 与 1.0，以及 V3 的 0、0.5、1.0，7 段 float64 PCM 的 SHA-256 一致。
- `aurora_core.Renderer`（`route="V"`，含 `flush`）：同样 7 段 float32 PCM 的 SHA-256 一致。

两边都能解析这份 TOML。32 个哈希全部与修改前相同。
