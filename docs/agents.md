# 编码代理调用方式

状态：待验证（P0-0）。在实际完成一次往返派单前，不得标记为已连接。

| 代理 | 用户说明 | 实测命令与版本 | 非交互参数 | 状态 |
|---|---|---|---|---|
| Cursor | 支持命令行 | 待记录 | 待记录 | 未验证 |
| Antigravity | 支持命令行 | 待记录 | 待记录 | 未验证 |

## 已知环境事实（2026-10-16）

- Claude 的 device_bash 运行在本机上的 Linux 虚拟机中，只能访问已连接文件夹；它看不到 macOS 上安装的 Cursor／Antigravity CLI、Homebrew 工具和 GitHub 凭据。
- 电脑控制对终端和 IDE（终端、Cursor、Antigravity、Ghostty）只开放查看与点击，不能输入文字或按键，所以 Claude 无法直接在 macOS 上运行命令。
- 结论：派单、`git push` 等需要 macOS 凭据或 CLI 的操作，要么由用户在终端执行 Claude 给出的命令，要么由用户运行一个白名单派单脚本（待用户决定，见下）。

## 待决定：派单通道

方案 A：用户手动执行。Claude 在 `docs/tasks/` 写好任务书，并给出一条完整命令。
方案 B：白名单派单脚本 `tools/dispatch.sh`，由用户在 macOS 终端启动。它监视 `.aurora/queue/`，只执行固定的几类命令：用指定任务书调用 Cursor／Antigravity CLI（在对应的 worktree 内）、`git push`。不执行队列文件里的任意命令。
- 该虚拟机内：git 2.34.1、python3、uv、ffmpeg 可用；git-lfs、gh、Rust 工具链不可用。
