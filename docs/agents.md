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
