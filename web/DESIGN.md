---
name: Aurora Binaural 工作台
description: 中文音频处理工作台，连接、导入、等响度 A/B 试听与完整导出。
colors:
  bg: "#f5f6f2"
  surface: "#fff"
  subtle: "#edf1eb"
  ink: "#213a34"
  muted: "#5c6f66"
  line: "#d5ded5"
  accent: "#28634c"
  accent-hover: "#1b4d39"
  accent-soft: "#e3eee5"
  amber-bg: "#fbf1da"
  amber-ink: "#6d4d13"
  error-bg: "#f9e9e4"
  error-ink: "#963c29"
  focus: "#327754"
  dark-bg: "#121f1c"
  dark-surface: "#192b25"
  dark-subtle: "#21372e"
  dark-ink: "#e4eee4"
  dark-muted: "#adc1b3"
  dark-line: "#385046"
  dark-accent: "#b5dcab"
  dark-accent-hover: "#d0ecc9"
  dark-accent-soft: "#304c3c"
  dark-amber-bg: "#3a321f"
  dark-amber-ink: "#efd197"
  dark-error-bg: "#432920"
  dark-error-ink: "#ffb9a4"
  dark-focus: "#b5dcab"
typography:
  display:
    fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "clamp(25px, 3vw, 34px)"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "-0.035em"
  headline:
    fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "18px"
    fontWeight: 650
    letterSpacing: "-0.02em"
  title:
    fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "14px"
    fontWeight: 650
  body:
    fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "13px"
    lineHeight: 1.7
  label:
    fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "12px"
    fontWeight: 550
  control:
    fontFamily: '"PingFang SC", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "13px"
    fontWeight: 600
  mono:
    fontFamily: "ui-monospace, monospace"
    fontSize: "10px"
    lineHeight: 1.7
rounded:
  field: "6px"
  control: "7px"
  import: "8px"
  workspace: "12px"
  badge: "4px"
  round: "50%"
spacing:
  gap-small: "8px"
  gap: "12px"
  gap-large: "20px"
  pane: "24px"
  section: "32px"
  page-wide: "44px"
  page-mid: "28px"
  page-small: "20px"
  page-narrow: "16px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-secondary-hover:
    backgroundColor: "{colors.subtle}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "8px 10px"
    height: "42px"
    width: "100%"
  workspace:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.workspace}"
  preset-option:
    rounded: "{rounded.control}"
    padding: "13px 10px 13px 12px"
  preset-option-selected:
    backgroundColor: "{colors.accent-soft}"
  mode-option-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "7px 13px"
  ab-option:
    backgroundColor: "{colors.subtle}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  ab-option-selected:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
  job-status:
    backgroundColor: "{colors.subtle}"
    textColor: "{colors.muted}"
    rounded: "{rounded.badge}"
    padding: "3px 8px"
  job-status-succeeded:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
  job-status-failed:
    backgroundColor: "{colors.error-bg}"
    textColor: "{colors.error-ink}"
  import-zone:
    backgroundColor: "{colors.bg}"
    rounded: "{rounded.import}"
    padding: "23px 12px"
  import-zone-dragging:
    backgroundColor: "{colors.accent-soft}"
---

# Design System: Aurora Binaural 工作台

## Overview

**Creative North Star: "音频工作台"**

Mode: Operate。中文界面以连接、导入、设置、试听对比和导出为主线。桌面将素材放在左侧，设置、试听和导出放在右侧；窄屏按任务顺序纵向排列。等响度 A/B 是工作区的中心，共享播放时钟，切换保持进度；任务队列在下方，报告与下载紧邻对应任务。

这是依据明确 P4 功能任务书完成的代码驱动界面。本记录提取当前 `src/style.css` 和 `src/main.tsx`，延续原有方向契约；“音频工作台”是既有方向的描述性名称，未新增视觉选择。没有图像设计稿，也不声称获得人类视觉批准。系统中文字体、内联功能图标与本地资源构成完整界面，无需外部图片或字体。

首屏按连接的引擎显示实际状态：Rust 模式显示路线、预设、SADIE II D1 与核心版本，桩模式显著说明直通、未应用 HRTF。开发者 JSON 与日志默认折叠。验收证据存放在 `../qa/p4/antigravity/`，浏览器用例在 `tests/`；本次文档提取未新增运行验证，也不声称听感验收、P1 渲染、Safari / Firefox 或真机验收。

**Key Characteristics:**

- 淡暖底色、深绿文字与绿色操作强调，同一语义覆盖浅色和深色。
- 分隔线与表面明度组织密集操作，容器无装饰阴影。
- 原生表单、明确选中态、键盘焦点与可见任务状态。
- A/B 共享进度，数字时间与进度使用等宽数字。

## Colors

绿色承担操作与选中语义，中性色承担阅读和分组，琥珀色说明引擎状态，暖红色说明错误。前置 tokens 是规范值，CSS 自定义属性是实现来源。

### Primary

- **工作绿**（`accent`、`accent-hover`、`accent-soft`）：主要按钮、选中的 A/B 音源、范围控件、完成状态与柔和的选中底色。
- **焦点绿**（`focus`）：交互元素的键盘焦点外框；深色模式使用对应的浅绿焦点值。

### Secondary

- **琥珀提示**（`amber-bg`、`amber-ink`）：首屏直通桩说明的底色与文字。
- **暖红错误**（`error-bg`、`error-ink`）：操作错误、失败任务和错误详情。

### Neutral

- **页面底色**（`bg`）：整体页面与导入区底色。
- **工作表面**（`surface`）：工作区、表单和普通按钮，也作为实色选中按钮的文字色。
- **轻层底色**（`subtle`）：未选中的 A/B 按钮、普通状态和开发者数据背景。
- **主文字 / 辅助文字**（`ink`、`muted`）：标题与主要内容、说明与元数据的两级关系。
- **分隔线**（`line`）：容器边界、分栏、字段边框和队列行。

无前缀的颜色记录浅色模式；`dark-` 前缀记录同名属性在深色模式的值。组件通过根元素上的同名 CSS 自定义属性跟随主题切换。sidecar 的八阶色带是展示面板所需的合成色带，不是已采用的界面色阶。

**The Semantic State Rule.** 绿色、琥珀色和暖红色保留既有状态含义；状态同时使用文字表达。

## Typography

**Display / Body Font:** PingFang SC、Microsoft YaHei、system-ui、sans-serif。

**Label Font:** 与正文相同；开发者数据使用 ui-monospace、monospace。

字体保持系统中文界面的清楚与紧凑。标题通过字号、字重和略收紧的字距建立层级，辅助数据以小字号和次级颜色退后。

### Hierarchy

- **Display**：页面主标题，流式字号与明确行高；在最窄断点固定为 25px。
- **Headline**：素材、空间设置、任务队列等章节标题。
- **Title**：播放器、完整导出和开发者数据分组标题。
- **Body**：页面引导文本；说明区按场景使用 10–13px 与 1.5–1.9 行高。
- **Label**：字段标签；控件文字使用单独的 `control` 角色。
- **Mono**：折叠开发者数据。时间、强度和任务百分比在普通字体中启用 `tabular-nums`。

**The Stable Numbers Rule.** 时间、强度和进度数值沿用等宽数字，更新时保持可扫读的位置关系。

## Layout

页面容器最大宽度 1440px，居中。桌面主内容水平留白 44px，顶栏高 86px；主内容顶部留白 34px。工作区是单一边框容器，列宽为 `minmax(240px, 0.75fr) minmax(0, 1.9fr)`，以垂直分隔线区分素材与编辑区。素材区内边距为 25px 24px，编辑区为 25px 28px 26px。

在 1100px 及以下，页面水平留白为 28px，素材列为 255px；试听按钮与导出按钮各自移到字段网格的下一行。在 760px 及以下，工作区变为单列，素材与编辑区用水平线分隔；页面水平留白为 20px，顶栏高 72px，任务和开发者数据纵向排列。在 480px 及以下，主内容水平留白为 16px，工作区内部水平留白为 17px，预设选项纵排，远程字段逐行排列，任务操作铺满可用宽度。页面最小宽度为 320px。

常用间距以 8px、12px、20px 与 24px 组织；工作区到队列的间隔为 32px。字段与标签间隔 7px。长文件名、错误和 JSON 允许换行，网格内内容使用可收缩列。窄屏保持原任务顺序，不把操作隐藏到另一套导航中。

## Elevation & Depth

当前系统没有阴影。层次来自页面底色、工作表面、轻层底色、选中底色和单像素分隔线。错误与引擎提示使用各自的语义底色，不依靠悬浮或模糊效果。

**The Flat Workspace Rule.** 工作区与控件保持平面；使用已有表面、边界和选中态表达层次。

## Shapes

工作区采用较宽的圆角，输入框、提示区和按钮采用紧凑圆角；具体值由 `rounded` tokens 记录。导入区为虚线边界，常规表面与字段为单像素实线。状态标签使用较小圆角，播放按钮为圆形。分栏容器裁切到自身圆角，文件格式小标识与快捷键键帽使用 3px 圆角。

## Components

### Buttons

按钮清楚、紧凑，主操作有稳定的实色重量。普通按钮最小高度为 42px；主题按钮和模式选项为紧凑变体，任务按钮最小高度为 38px。主按钮使用强调色与工作表面文字色，普通按钮使用工作表面，轻按钮保持透明背景。悬停时改变背景，普通按钮的边框改为辅助文字色；主按钮使用专属悬停色。禁用按钮与字段透明度为 0.48，光标为不可用。

按钮、输入框、下拉框、链接与折叠摘要共用 3px 焦点外框和 3px 偏移。按钮背景过渡为 `120ms ease-out`。`prefers-reduced-motion: reduce` 下关闭过渡并使用即时滚动。

### Inputs / Fields

原生文本、URL、密码、数值输入与下拉框统一为 42px 高、单像素边框和 8px 10px 内边距。占位文字使用辅助文字色；范围控件使用主题强调色，最小高度 30px。预设保留原生 radio；选中标签同时改变边框与底色，辅助说明紧邻预设名称。480px 及以下由横排改为纵排。

### Mode Control

本地 / 远程服务模式是带单像素边框、3px 内边距的按钮组。选中按钮以主文字色填充并使用工作表面文字色，`aria-pressed` 与视觉态一致。页眉保留品牌、文字连接状态与主题切换按钮；当前界面没有独立页面导航。

### Cards / Containers

工作区是主要容器，以表面和边框组织两个操作区域。导入区使用页面底色、虚线边框和居中说明；拖入状态切换为选中底色与强调色边界。素材项被选中时使用同一柔和选中底色，文件名允许换行。

### A/B Player

播放器以顶部分隔线和底部分隔线嵌入编辑区。A / B 等宽按钮并列，选中一侧使用强调色；快捷键直接显示在按钮中。B 侧仅在已载入试听报告确认为桩时显示直通桩标签。圆形播放按钮、共享进度条、数值时间和音量形成一行，窄屏只压缩间距与音量宽度。切换只调整两侧增益；现有方向契约规定 8ms 音频增益渐变以避免不连续，同时保持相位与播放进度。

### Queue / Status Chips

任务队列用横线分隔各行，不增加卡片阴影。状态标签使用文字与底色共同区分普通、完成和失败；进度条高 4px，百分比用等宽数字。下载、报告、载入试听和取消操作紧邻对应任务。加载、空、错误与取消状态沿用现有状态文案；详细错误与开发者 JSON / 日志通过原生折叠区展示。

## Do's and Don'ts

### Do:

- **Do** 保留浅色 / 深色同名语义映射，并通过根元素 CSS 自定义属性消费颜色。
- **Do** 保持导入、设置、A/B 对比、导出、任务队列的操作顺序。
- **Do** 保留可见焦点、原生字段、按下状态和文字状态提示。
- **Do** 将长文件名、错误和开发者数据换行在可用空间内。
- **Do** 在首屏显示真实引擎信息，并仅在桩模式显示桩说明和 B 侧桩标签。

### Don't:

- **Don't** 将展示面板合成色带当作新增界面 token。
- **Don't** 给现有平面工作区添加装饰阴影或外部图片、字体依赖。
- **Don't** 用颜色单独表达连接、选中或任务结果。
- **Don't** 将默认 HRTF 配置目标表现为桩引擎已应用 HRTF。
- **Don't** 把本次文档提取描述为人类视觉批准、听感验收或新增运行验证。
