# P4-codex-01 + F-P3-1 执行报告

**已完成。** Web GUI、完整应用流程、接桩试听、F-P3-1 和多阶段 Docker 构建均已实现并实测。应用测试 **58 passed**，Playwright **4 passed / 0 flaky**，Ruff、mypy、TypeScript、前端 lint 全部通过。

- 日期：2026-10-17（Asia/Shanghai）；开始 07:40，最终验收约 08:06，低于 150 分钟上限。
- 克隆 / 分支：`.agents/codex/` / `agent/codex/P4-01`；起点 `ae602303f978add67110ba67da43a2178f4b2fcf`。
- 只修改允许目录；未修改 main，未推送。每次提交正文末尾为 `Agent: Codex`。
- 核心仍为 **直通桩**；本次结论只代表应用流程可用，不是 P1 空间音效或人工听感验收。

## 交付内容

### GUI 与启动

- `web/`：Vite 8.3.4 + TypeScript 6.0.3 + Preact 11.0.1；本机无 pnpm，使用 npm。依赖精确版本及 `package-lock.json` 已提交，`web/dist/` 不提交。
- 中文流程：多文件选择 / 拖放 → 预设、强度 → 试听区间 → A/B → 完整导出（WAV 24-bit / M4A / MP3，44.1 / 48 / 96 kHz，尾音策略）。
- 任务队列显示状态、处理进度、取消、错误详情、结果音频与 JSON 报告下载，摘要含采样率、样本数、响度、HRTF、预设版本、强度与 seed。
- 双 AudioBuffer 使用同一个 AudioContext 时钟、同一开始时间与 offset；两路同时运行，A/B 只做 8ms 增益交接。Space 播放 / 暂停，A / B 切换，在播放器按钮保留焦点时也有效；输入框保留原生键盘操作。
- 本地 / 远程、浅色 / 深色、390px 窄屏；所有异步操作有加载 / 错误状态，开发者 JSON 与请求日志默认折叠。
- 显著展示“效果引擎：桩（直通），P1 核心接入后替换”及默认 HRTF SADIE II；报告也明确标记配置目标尚未应用。
- `aurora gui` 启动 API 与 Worker，固定绑定 `127.0.0.1`、默认选择空闲端口，就绪后打开浏览器；支持 `--port`、`--no-browser`。Ctrl-C 后 Worker 心跳与进程正常回收。
- 本地生成一次性凭据，经同源 / Host / 回环检查后的 `/gui/session` 在内存中使用。远程 Key 只保留在 sessionStorage / 内存中，以 Authorization header 传递，不写 URL、localStorage 或日志。跨域连接使用显式 `AURORA_CORS_ORIGINS`。

### 试听接口与交付

- `POST /v1/previews`：`asset_id`、`start_seconds`、`duration_seconds`（>0、≤20）、`preset`、`strength`、`seed`；202 返回普通队列 job。
- 复用持久任务、幂等 / 冲突、租约、重试、块边界取消、超时、报告和过期清理。
- `/v1/jobs/{id}/result` 为效果；新增 `/v1/jobs/{id}/original` 为等响度原声；两侧均为 stereo / 48 kHz / float32 WAV。
- 从素材原点推进同一渲染器，丢弃前缀，只保留选定区间；保留运动相位、seed 和历史状态。时间变化测试核心已与完整渲染同位置区间做波形回归。
- 按 BS.1770 测量、匹配并回读验证：同长度，正常可测信号差 ≤0.2 LU；匹配时真峰值上限 -1 dBTP。低于测量门限的两侧显式标记；只有一侧静音时任务失败并说明无法匹配。
- 原声、效果、报告在同一租约提交中交付。注入原声文件提交失败时，全部输出回滚，尝试目录清空。报告保留真实源素材 SHA-256，原声片段另有独立探测信息。
- `/v1/jobs` 不允许借内部 preview 字段创建编码规格不一致的试听；统一经 `/v1/previews` 规范化参数。
- OpenAPI 快照为本目录 `openapi.json`，P3 合同测试迁移引用该快照，历史 P3 证据文件未改。

### F-P3-1

- 租约恢复先隔离过期尝试，清理再回收精确命名的旧尝试目录，包括 SIGKILL 后遗留的空目录 / 部分文件。
- 事务内核对当前尝试，保护活跃重试；忽略符号链接和其他命名目录。删除失败留待下轮重试。
- 覆盖真实 Worker SIGKILL → 租约过期 → attempt 2 成功 → 无旧目录残留，以及空目录 / 非空目录、活跃重试、符号链接保护。

### Docker 与本地资源

- Node 构建前端 → Python 运行镜像；两个基础镜像固定 tag + digest。FastAPI 提供本地静态资源，未用 CDN。
- `deploy/Dockerfile.dockerignore` 限制构建上下文；运行镜像只打包 SADIE II D1 SOFA，排除 TH Köln KU100 与其衍生数据。
- SADIE 文件 SHA-256 已核对为 `e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31`；附 `THIRD_PARTY_NOTICES.md`、Apache-2.0 全文、Preact MIT。Preact 许可证也包含在静态 `licenses/` 资源中。
- 最终本地镜像 ID：`sha256:49667ee6288e6d449934f2edf7ee4a644a95da6bbe26cef80d65e3d11b02be6e`。
- 独立 API / Worker 容器 + 临时卷实测：静态 JS / 许可证、ready、401 鉴权、试听双文件 / 响度、完整导出、日志无临时 Key、镜像仅 SADIE 及许可文件。测试容器与卷已删除，未发布镜像。

## 实际验收命令与退出码

以下命令在仓库根目录执行，均为最终或对应功能点实际运行记录。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `uv run pytest` | 0 | 58 passed，14.05s；1 条现有 Starlette TestClient 弃用警告 |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run mypy` | 0 | 25 source files 无问题 |
| `npm --prefix web run typecheck` | 0 | `tsc --noEmit`，含 src、Playwright 测试与配置 |
| `npm --prefix web run lint` | 0 | ESLint 通过 |
| `npm --prefix web run build` | 0 | tsc + Vite；JS 38.15 kB / gzip 14.97 kB，CSS 14.20 kB / gzip 3.73 kB |
| `npm --prefix web run test:e2e` | 0 | 4 passed，22.8s；0 skipped / unexpected / flaky |
| `uv run pytest tests/app/test_aurora_p4_gui.py tests/app/test_aurora_p4_previews.py` | 0 | 11 passed，含真实启动退出、配对文件提交失败回滚、单侧静音 |
| `uv run pytest tests/app/test_aurora_p4_cleanup.py tests/app/test_aurora_p3.py -k 'cleanup or sigkill'` | 0 | 4 passed，含真实 SIGKILL 恢复 |
| `docker build -t aurora-binaural:codex-p4 -f deploy/Dockerfile .` | 0 | 多阶段构建，Node 阶段实际运行 npm ci / build |
| `uv run python qa/p4/antigravity/container_smoke.py` | 0 | 独立容器实测通过，见 container-smoke.json |
| `docker compose -f deploy/compose.yaml config --quiet` | 0 | Compose 配置校验通过 |
| `uv run pytest tests/app/test_aurora_p3.py::TestApiContractAuthAndOpenApi::test_openapi_export_and_artifact_parity` | 0 | 最后补全原声二进制响应及错误码文档后，快照复核 1 passed |
| `git diff --check` | 0 | 无空白错误 |

## 端到端与截图

测试源代码：`web/tests/workflow.spec.ts`。真实 Chromium + 本地 GUI 服务 + 第二 origin 远程服务；逐块延时只用于让运行中取消可复现，运行数据隔离。

1. 上传 → 1s 原始位置起、3s 试听 → A/B / 快捷键 / 暂停位置稳定 → 完整导出 → 实际读取下载 WAV。WebAudio 探针验证切换期间仅有两次 source.start，时间、offset、解码长度与采样率相等。
2. 错误远程 Key 的中文提示；Key 仅在 sessionStorage，URL / localStorage / 开发者日志 / console 无 Key；清除 Key；正确 Key 在第二 origin 实际导出成功。
3. 运行中的任务取消 → canceled，下载按钮不存在。
4. 多文件导入、损坏音频错误可恢复、有效素材保留、批量导出两个结果。

重复选择当前本地 / 远程模式不会清空工作区；真正切换连接仍按已提示的规则重置本页。

证据：

- `e2e-results.json`：Playwright JSON 结果。
- `screenshots/desktop-light.png` / `desktop-dark.png`：1440px。
- `screenshots/narrow-light.png` / `narrow-dark.png`：390px。
- 四张均为真实完成状态 full-page 截图，两轮各逐张打开验证，无空白 / 裁切 / 横向溢出。
- `design-detector.json`：机械检查无发现。`web/DESIGN.md` 与 `web/.impeccable/design.json` 记录实际 CSS tokens、组件和断点，独立文档核对通过，未改界面。
- `UI-REVIEW.md`：独立复核先提出两个 P2 交互修复，修复后的 verdict 为 **ship，范围仅限该两项均 resolved**，不冒充新的全界面审核或人工听感验收。

## 中间失败及修正

- 新 API 首次联合回归：1 failed / 16 passed，原因是历史 P3 固定 OpenAPI 快照不含新增合同；改为 P4 快照后通过。
- 首轮 E2E：1 passed / 3 failed；人工逐块延时超出默认 5s 断言窗口，后续任务等待前序完成。使用每次运行隔离的数据目录、20s 有界状态等待和较小测试延时后通过，没有增加测试重试次数。
- 初次脚手架有相对路径错误，CSS 尚未落到目标目录时 build 返回 1；修正后通过。tests/ 尚未建立时 lint 返回 2，测试目录完成后通过。临时 formatter 路径错误已更正。
- 初次类型检查曾暴露生成器关闭类型与多余 ignore，已改为真实 Generator 标注并移除冗余注释；最终 mypy 通过。

## 使用

```sh
npm --prefix web ci
npm --prefix web run build
uv run aurora gui
```

固定端口 / 自动化：`uv run aurora gui --port 8765 --no-browser`。远程 / Docker 说明见 `deploy/README.md`。

## 已知局限

- P1 核心、真实空间听感与 SADIE HRTF 应用不在本次范围；界面 / 报告已显著标注。极短或低于 BS.1770 门限的片段不能给出有效响度差。
- 靠后位置的试听必须推进整个前缀，保证未来核心状态一致；因此起点越靠后可能越慢，内存只保留最长 20s 的两侧音频。
- 浏览器刷新或真正切换连接会清空本页列表；服务端 SQLite 队列和结果仍保留，job ID 可从已下载报告 / 开发者 JSON 留存并使用 CLI 查询。
- 浏览器下载采用带鉴权 fetch + Blob，大文件需要相应浏览器内存。本次未做 2 小时或 500 MiB 浏览器压力测试。
- 浏览器验收为 macOS ARM64 Chromium（含 390px 视口），未做 Safari / Firefox / 真机 / 辅助技术 / 人工听感验证；容器为本机 OrbStack Linux ARM64，未部署公网 HTTPS。
- 源码启动前需构建 web/dist；普通 Python wheel 不自动内嵌前端，独立安装使用 AURORA_WEB_DIR 指向已构建资源。Docker 已包含构建产物。
- Docker Python 运行依赖沿用项目范围声明安装；本机 uv 按 uv.lock 验收，前端由 npm lockfile 固定。未声称整个系统镜像字节级可重现。

**未完成的必需项：无。**
