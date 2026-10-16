# 编码代理调用方式

状态：待验证（P0-0）。在实际完成一次往返派单前，不得标记为已连接。

| 代理 | 用户说明 | 实测命令与版本 | 非交互参数 | 状态 |
|---|---|---|---|---|
| Cursor | 支持命令行 | 待记录 | 待记录 | 未验证 |
| Antigravity | 支持命令行 | 待记录 | 待记录 | 未验证 |

## 已知环境事实（2026-10-16）

- Claude 的 device_bash 运行在本机上的 Linux 虚拟机中，只能访问已连接文件夹；它看不到 macOS 上安装的 Cursor／Antigravity CLI、Homebrew 工具和 GitHub 凭据。
- 因此派单需要在 macOS 终端中执行（经用户授权的电脑控制），或由用户在终端执行 Claude 给出的命令。
- 该虚拟机内：git 2.34.1、python3、uv、ffmpeg 可用；git-lfs、gh、Rust 工具链不可用。
