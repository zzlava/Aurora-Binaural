# 编码代理调用方式

状态：待验证（P0-0）。在实际完成一次往返派单前，不得标记为已连接。

| 代理 | 用户说明 | 实测命令与版本 | 非交互参数 | 状态 |
|---|---|---|---|---|
| Cursor | 启动命令 `agent` | 2026.10.14-941492e（~/.local/bin/agent） | `agent -p --force --trust --model auto --output-format text "<任务书>"`（免费计划只能用 Auto 模型） | 参数已确认，待首次派单 |
| Antigravity | 启动命令 `agy` | 1.0.9（/opt/homebrew/bin/agy） | `agy --dangerously-skip-permissions --print-timeout 3h -p "<任务书>"`（`-p` 带参数，必须放在最后，否则后面的标志会被当成提示词） | 参数已确认，待首次派单 |

## 已知环境事实（2026-10-16）

- Claude 的 device_bash 运行在本机上的 Linux 虚拟机中，只能访问已连接文件夹；它看不到 macOS 上安装的 Cursor／Antigravity CLI、Homebrew 工具和 GitHub 凭据。
- 电脑控制对终端和 IDE（终端、Cursor、Antigravity、Ghostty）只开放查看与点击，不能输入文字或按键，所以 Claude 无法直接在 macOS 上运行命令。
- 结论：派单、`git push` 等需要 macOS 凭据或 CLI 的操作，要么由用户在终端执行 Claude 给出的命令，要么由用户运行一个白名单派单脚本（待用户决定，见下）。

## 已决定：派单通道（方案 B）

用户于 2026-10-16 选择白名单派单器。

- 启动（macOS 终端）：`cd ~/Desktop/leo/"Aurora Binaural" && bash tools/dispatch.sh`
- Claude 入队：`tools/enqueue.sh <action> key=value ...`
- 动作：doctor、setup-remote、push、run-agent（agent=cursor|antigravity，task=docs/tasks/*.md，branch=agent/<agent>/*）、cancel（target=<id>）
- 代理在 `.agents/<agent>/` 独立克隆中工作，只提交到自己的分支，不推送。
- 安全边界：派单器不执行队列里的任意命令；但 run-agent 会以非交互、免确认模式运行代理，代理本身可以在本机执行命令。
- 派单器脚本修改后需要用户重启才生效，修改会出现在 git 提交里。

## macOS 环境（doctor，2026-10-16）

- macOS 26.7.1；Apple Git 2.50.1；gh 2.101.0，已登录 zzlava（https）；Python 3.14.7；uv；ffmpeg 9.0.2（Homebrew）
- 缺少：git-lfs、Rust 工具链（rustup／cargo）
- 远程：origin = https://github.com/zzlava/Aurora-Binaural.git，2026-10-16 首次推送 main 成功

## LFS 与 Git 操作的分工（重要）

- Claude 所在的虚拟机没有 git-lfs。为避免 LFS 文件被写成指针文本，虚拟机里只做只读的 Git 操作（log、diff、show、fetch）和纯文本文档的提交；不在虚拟机里 checkout、merge 或 add 任何 LFS 文件。
- 涉及工作区的操作由 macOS 上的派单器完成：prepare-agent（准备代理分支）、merge（合并到 main）、tag、push、init-lfs。
- 两个代理克隆的 `lfs.storage` 指向主仓库的 `.git/lfs`，LFS 对象共用一份，取回分支时不会缺少对象。
- 代理克隆的 origin 是相对路径 `../..`（即主仓库），macOS 和虚拟机两侧都能解析。

## Cursor 账号与模型（2026-10-16）

- 账号：已切换为 Premium 账号（`agent status` 显示 sevava0405@gmail.com）。之前登录的免费账号在 P0-cursor-01 运行中途撞到用量上限。
- 模型选择（由 Claude 决定）：原计划用 `gpt-5.6-sol-high`。实测发现，这个账号本周额度只覆盖 Grok 系列：GPT-5.6 Sol、Claude Opus 5.5、Codex 5.3 都提示"不在每周用量内"，需要另外购买额度（未购买）。因此 P0-cursor-02 改用 `grok-4.7-high`。理由：推理能力强，而且和验收方 Claude 不是同一模型家族，可以减少编码与审查出现相同盲点的风险。普通任务可以降到 `gpt-5.3-codex`。每次派单的模型都记录在 run-agent 日志的第一行。

## 额度不足时的后备执行方（用户规则，2026-10-16）

- 规则：**只有 Cursor 整体额度用尽**时，才把任务交给 **Codex CLI**。仅仅是某个模型不在本周额度内，不算用尽。
- 实现：run-agent（agent=cursor）按以下顺序处理：
  1. 用指定的模型运行。
  2. 如果提示"not included in your weekly usage"（这个模型不在额度内），改用 Cursor 仍可用的 `grok-4.7-high` 再跑一次。
  3. 只有出现"hit your usage limit"这类整体用尽的提示，或者后备模型也不可用时，才在同一个克隆 `.agents/cursor/`、同一个分支上运行 `codex exec --dangerously-bypass-approvals-and-sandbox`。
- Codex 的提交信息标注 `Agent: Codex (for Cursor)`；目录所有权不变。
- 待验证：Mac 上 codex 的路径、版本、登录状态，以及 `exec` 子命令的参数，要在派单器重启后由 doctor 确认。确认之前，这条后备路径视为未验证。

## 虚拟机侧查看 Git 状态的注意事项

主仓库和代理克隆都执行过 `git lfs install --local`，虚拟机里没有 git-lfs，直接运行 `git status` 会报 filter-process 错误。只读查看时使用：
`git -c filter.lfs.process= -c filter.lfs.required=false -c filter.lfs.clean=cat -c filter.lfs.smudge=cat status`
这样查看时，wav、sofa 文件显示的 `M` 是假阳性（虚拟机看到的是真实内容，而 Git 里存的是 LFS 指针）。虚拟机里不要 add 这类文件。

## 事故记录与规则（2026-10-17）

- 事故：Claude 在虚拟机里用 `git add qa docs` 提交了 `056bc21`。因为虚拟机的 clean 过滤器被设成了 `cat`，50 个 LFS 样片（约 130 MB）被当成普通文件写进了 main，并已推送到 GitHub。修复提交把它们恢复为 LFS 指针（只改暂存区，不动工作区）。这些原始大文件仍留在已推送的历史里（是否清理见 DECISIONS-PENDING U6）。
- 规则：Claude 在虚拟机里提交时，**只 add 明确列出的文本文件路径**，不 add 目录；提交前必须运行 `tools/vm-git-guard.sh`，脚本报错就不能提交。

- 2026-10-17 P0-cursor-07：(1) 派单器会同时取走队列里的所有任务，紧挨着入队的 prepare-agent 会把同一代理后面的 run-agent 当成“正在运行”而失败。prepare-agent 必须单独入队，等它完成后再入队 run-agent。(2) 代理在克隆里的提交不会自动出现在主仓库；合并前在 VM 执行 `git fetch .agents/<ag> <sha>` 并建立分支（只涉及 ref 和文本）。(3) 用户放在仓库根目录的音频会挡住合并，已在 .gitignore 加入 `/*.wav`、`/*.flac`。(4) P1b-01 基于 71f54f7，不含 P0-07 的元数据，验收前需要先合入 main。
