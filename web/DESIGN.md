# Aurora Binaural 工作台

## Direction and surface contract

Mode: Operate. 中文音频工作台，素材在左，设置、试听与导出在右；窄屏按实际任务顺序纵向排列。首屏说明桩引擎的真实状态，连接后立即导入素材。A/B 为工作区中心：共享时钟，切换仅调整两侧增益。导出任务位于下方，报告与下载紧邻对应任务。

This is a code-led implementation of the explicit P4 task brief. No image comp or visual approval is claimed. No external imagery or fonts are required by this operational surface; all resources are local.

## Tokens

- Light: background `#f5f6f2`, surface `#fff`, ink `#213a34`, secondary text `#5c6f66`, accent `#28634c`, selected `#e3eee5`, divider `#d5ded5`.
- Dark: background `#121f1c`, surface `#192b25`, ink `#e4eee4`, secondary `#adc1b3`, accent `#b5dcab`, selected `#304c3c`, divider `#385046`.
- Stub notice: light `#fbf1da` / `#6d4d13`; dark `#3a321f` / `#efd197`.
- System Chinese typography: PingFang SC / Microsoft YaHei / system-ui. Page title 25–34px, section 18px, controls 12–13px; numeric time/progress uses tabular figures.
- Layout: maximum 1440px, desktop horizontal padding 44px, 1100px breakpoint 28px, mobile 16–20px. Workspace columns approximately 28/72; switches to one column at 760px. 480px preset controls stack.
- Primary workspace radius 12px, controls 6–7px; 1px dividers, no ornamental shadows. Focus ring 3px with 3px offset. Button minimum 42px except compact secondary controls.
- Behavior: native form controls, pressed states, visible loading/error/empty states, reduced-motion override. Only background hover transitions (120ms); A/B audio gain ramps 8ms to avoid discontinuity while preserving phase.

## Quality bar and evidence

Deliver the complete P4 flow with real server/worker and decoded audio. Four full-page screenshots at 1440px and 390px in both themes, no horizontal overflow. E2E verifies upload, source-position preview, A/B and shortcuts, full export/download, explicit wrong-key errors, cross-origin remote success, running cancellation and batch import. Developer details are folded by default. Engine and default HRTF provenance stay explicit.

Evidence lives in `qa/p4/antigravity/`; browser tests in `web/tests/`. No claim of human listening acceptance, P1 rendering, Safari/Firefox or real-device testing.
