# 编码代理调用方式

状态：待验证（P0-0）。在实际完成一次往返派单前，不得标记为已连接。

| 代理 | 用户说明 | 实测命令与版本 | 非交互参数 | 状态 |
|---|---|---|---|---|
| Cursor | 启动命令 `agent` | 待 doctor 记录 | `agent -p --force --output-format text "<任务书>"`（待 --help 确认） | 未验证 |
| Antigravity | 启动命令 `agy` | 待 doctor 记录 | 待 --help 确认 | 未验证 |

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
