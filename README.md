# ShareADB

<!-- Badges -->
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey.svg)](https://github.com/hemin0721/shareadb)

**Share local Android Debug Bridge (ADB) devices with remote users via TCP proxy**

[GitHub Repository](https://github.com/hemin0721/shareadb) | 
[Issues](https://github.com/hemin0721/shareadb/issues) | 
[Documentation](#使用说明)

---

## 功能需求

共享本地adb设备给其他用户使用。

## 需要考虑的问题

1. 多设备管理: 支持同时连接和管理多个adb设备。每个设备可以分配不同的端口号进行adb连接。
2. 跨平台支持, 需要支持windows, linux等操作系统
3. 一旦开启后，会记录已经开启的连接，如果设备断开后，可以自动进行重连(周期比如间隔5s检测设备是否已经连接上了, 用于维护连接的稳定性). 通知链接的断开和重新连接状态.
4. 可以针对单个已经连接的设备进行操作.

## 基础功能的实现步骤

检测adb，如果没有安装adb命令，那么则需要提示用户安装或者输入adb路径

当单个设备时
```bash
adb shell setprop persist.adb.tcp.port 5555
adb shell stop adbd
adb shell start adbd
adb forward tcp:6000 tcp:5555
```

多个设备希望端口号可以慢慢自增

最终还需要在本地启动一个代理，允许其他设备连接当前设备的IP来访问adb功能.

类似:
```bash
socat -s tcp4-listen:7000,reuseaddr,fork tcp-connect:127.0.0.1:6000
```

不过不建议直接使用socat, 而是使用python利用socket来实现一个属于自己的代理，这样可以实现跨平台.

## Requirements

### Python Version
- Python 3.8 or higher
- Supports Python 3.8, 3.9, 3.10, 3.11, 3.12

### System Requirements
- ADB (Android Debug Bridge) installed and accessible in PATH
- Linux, macOS, or Windows operating system
- Network connectivity for remote connections

### Dependencies
**No external dependencies required!** This package uses only Python standard library modules.

## Installation

### From PyPI (Recommended)
```bash
pip install shareadbpy
```

### From Source
```bash
git clone https://github.com/hemin0721/shareadb.git
cd shareadb
pip install -e .
```

### Verify Installation
```bash
shareadbpy --help
```

## Features

- ✅ **Multi-Device Support**: Manage multiple ADB devices simultaneously
- ✅ **Auto-Reconnect**: Automatic device detection and reconnection (configurable interval)
- ✅ **Cross-Platform**: Works on Linux, macOS, and Windows
- ✅ **Pure Python**: No external dependencies, uses only standard library
- ✅ **TCP Proxy**: Built-in TCP proxy for remote access
- ✅ **Port Auto-Assignment**: Automatic port allocation for multiple devices
- ✅ **Local IP Detection**: Automatically detects and displays local IP addresses
- ✅ **Connection Monitoring**: Real-time device status and connection information
- ✅ **Selective Sharing**: Share specific devices using `--include` parameter
- ✅ **Access Control**: Allow-list / deny-list (`--allow` / `--deny`) with IP and CIDR support
- ✅ **Connection Management**: Runtime admin console to list, kick and ban clients

## 项目结构

```
pyproject.toml        # 项目元数据与命令行入口
src/shareadb/         # Python 源码目录
  __init__.py
  access.py           # 连接访问控制策略（黑白名单，支持 IP/CIDR）
  adb_client.py       # 封装 adb 命令的异步客户端
  admin_console.py    # 运行时管理控制台（clients/kick/ban/unban）
  cli.py              # 命令行入口，负责参数解析与事件循环
  device_manager.py   # 设备会话管理、端口分配与自动重连逻辑
  tcp_proxy.py        # 基于 asyncio 的 TCP 代理实现（含连接跟踪）
```

## 使用说明

1. 确保本机已安装 adb 并可以在命令行中访问，或在执行时使用 `--adb-path` 指定 adb 的完整路径。
2. 在仓库根目录执行：

```bash
python -m shareadb.cli --poll-interval 5 --status-interval 30
```

常用参数：
- `--listen-host`: 代理监听的地址，默认 `0.0.0.0`
- `--forward-base-port`: adb forward 起始端口，默认 `6000`
- `--proxy-base-port`: TCP 代理起始端口，默认 `7000`
- `--include`: 仅管理指定序列号的设备，例如 `--include SERIAL1 SERIAL2`
- `--exclude`: 完全不碰的设备序列号（不执行 tcpip、不建 forward），例如本地正在用 scrcpy 的设备 `--exclude SERIAL`
- `--allow`: 白名单模式，只允许列出的客户端 IP/CIDR 连接（本机回环默认放行），例如 `--allow 192.168.1.0/24 10.0.0.5`
- `--deny`: 黑名单，拒绝列出的客户端 IP/CIDR（优先级高于 `--allow`），例如 `--deny 172.16.0.0/16`
- `--blocklist-file`: 可选的黑名单文件（每行一个 IP/CIDR，支持 `#` 注释），启动时加载，运行时执行 `ban`/`unban` 会自动回写该文件

访问控制规则同时支持 IPv4 与 IPv6；既有白名单又有黑名单时，黑名单优先。

3. 程序会每隔 `poll-interval` 秒检测设备状态：
   - 新设备上线时自动配置 `adb tcp`、建立端口转发并启动本地代理；
   - 设备断开时释放 forward 与代理，下次检测到设备重新连接时继续复用历史端口；
   - `status-interval` 控制定期输出设备状态日志，设置为 `0` 可关闭。
   - 设备连接成功后，会自动显示本机的所有可访问 IP 地址和对应的连接指令。

远端用户只需连接到 `listen-host:proxy_port` 即可访问对应设备的 adb。每台设备都会分配一对唯一的 forward 与代理端口，并在设备重连后保持一致。

## 与本地 adb 共存（scrcpy / Android Studio 等）

本地工具（scrcpy、Android Studio、Chrome 调试等）和 shareadb 共用同一个 adb server 和设备的 USB 通道。shareadb 在设计上尽量避免干扰本地使用：

- **设备已在 TCP 模式时不重启 adbd**：建立共享时先尝试直接建立端口转发，只有转发失败（设备确实处于 USB 模式）才执行 `adb tcpip`。设备设置了 `persist.adb.tcp.port`（或之前开启过共享）后，重启、插拔、自愈恢复都不会再重启 adbd，本地 scrcpy 的连接不受影响。
- **首次开启 TCP 模式时** adbd 必须重启一次，本地 adb 连接会瞬断几秒——建议先启动 shareadb，等设备共享就绪后再启动 scrcpy。
- **自愈防抖**：端口转发探测偶发失败不会立刻拆除重建（避免反复重启 adbd），先复测确认。
- **按设备执行 adb 命令**：`adb reconnect offline` 等命令只作用于共享的设备，不影响本机其他设备。
- **自动清理回环设备条目**：如果在本机执行了 `adb connect <本机IP>:<代理端口>`，会在 adb server 里残留一个"两条设备"的条目，导致 scrcpy 报 multiple devices 无法启动；shareadb 检测到后会自动 `adb disconnect` 清理。
- **`--exclude` 彻底隔离**：不希望被共享管理的设备用 `--exclude SERIAL` 排除，shareadb 不会对它执行任何命令。

## 连接管理

服务运行期间，可以直接在终端输入管理命令（stdin 不可用时会自动关闭控制台，不影响访问控制规则）：

```
help              显示命令帮助
status            查看设备共享状态
clients           列出当前连接的客户端（IP、设备、连接时长、上下行流量）
kick <ip|cidr>    断开匹配的连接（对方仍可重连）
ban <ip|cidr>     拉黑并立即断开（之后的连接也会被拒绝）
unban <ip|cidr>   解除拉黑
policy            查看当前黑白名单及被拒绝的连接尝试统计
```

例如有人在持续连接你的设备，直接执行：

```
ban 10.0.0.9
```

该 IP 的现有连接会被立即断开，之后的连接尝试也会被拒绝并记录。若启动时指定了 `--blocklist-file`，拉黑名单会持久化到文件，重启后依然生效。

被拒绝的连接会以 WARNING 级别记录日志（同一来源做了限频，避免刷屏），可通过 `policy` 命令查看各来源的尝试次数。

## 连接信息示例

当设备成功连接后，程序会自动输出类似以下信息：

```
============================================================
ADB devices are now ready for remote connection!
Remote users can connect using:

Device: abc123def456
  Model: Pixel 6
  Proxy port: 7000
  Connection commands:
    adb connect 10.0.xx.xx:7000
    adb connect 192.168.1.100:7000

Note: Use the appropriate IP address that is accessible from the remote machine
============================================================
```

无需手动查找本机 IP，程序会自动检测所有可用的网络接口 IP 地址，方便远端用户选择合适的地址进行连接。

## 打包和发布

### 构建分发包

```bash
# 安装构建工具
pip install build twine

# 构建分发包
python3 -m build
```

构建完成后，会在 `dist/` 目录下生成 `.tar.gz` 和 `.whl` 文件。

### 上传到 PyPI

首先确保你已经注册了 PyPI 账号并配置了账号信息：

```bash
# 上传到测试 PyPI（用于测试）
python3 -m twine upload --repository testpypi dist/*

# 上传到正式 PyPI
python3 -m twine upload dist/*
```

### 从 PyPI 安装

```bash
# 从正式 PyPI 安装
pip install shareadbpy

# 从测试 PyPI 安装
pip install --index-url https://test.pypi.org/simple/ shareadbpy
```

安装后可以直接使用命令：

```bash
shareadbpy --poll-interval 5 --status-interval 30
```

## 已测试场景

- Ubuntu 下的单设备移除和恢复
- Ubuntu 下的双设备移除和恢复
- 使用 `--include` 参数在双设备环境下只共享指定设备
- 设备已在 TCP 模式时共享/重连全程不重启 adbd（本地 scrcpy 不受影响）
- 仅在设备处于 USB 模式时回退执行一次 `adb tcpip`
- 端口探测瞬时失败被防抖拦截，不触发拆建循环
- `--exclude` 排除的设备不被执行任何 adb 命令
- 回环设备条目自动 `adb disconnect` 清理
- `--deny` 拒绝指定 IP/CIDR 的连接，`--allow` 白名单模式仅放行指定网段
- 运行时 `kick`/`ban`/`unban` 命令断开与拉黑客户端，`ban` 后重启仍被拒绝（配合 `--blocklist-file`）
- 基础功能在 Linux 系统下运行正常

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Support

If you encounter any issues or have questions:

- 🐛 [Report a bug](https://github.com/hemin0721/shareadb/issues)
- 💡 [Request a feature](https://github.com/hemin0721/shareadb/issues)
- 📧 Email: hemin0721@gmail.com

## Acknowledgments

- Built with pure Python using asyncio
- No external dependencies
- Cross-platform support (Linux, macOS, Windows)

---

**Made with ❤️ by [hemin0721](https://github.com/hemin0721)**
