# 验收：P0-cursor-07（默认 V3，加入面向用户的名称）

验收人：Claude　日期：2026-10-17　结论：**通过**

- 提交 52fa04e 只改了 `presets/route_v.toml` 和 `qa/p0/cursor/REPORT-v5.md`，没有越界。
- diff 只涉及 `default_preset`、`name`、`description`、`default_strength` 四类元数据，扬声器、房间、EQ 的数值都没有改动。
- 逐位一致：用 vr2 wheel，分别加载修改前的 TOML（HEAD^）和修改后的 TOML，以 route V、强度 1.0、开启限幅器、block 512 渲染 holdout_electronic 前 8 秒。V1、V2、V3 三个预设的输出都完全相同（maxdiff 0.0）。这也说明 DSP 加载器能正常忽略新加的元数据字段。
- 后续：`src/aurora_binaural/catalog.py` 应改为读取 `default_preset` 和 `default_strength`，这一项已写入 P6-codex-02。
