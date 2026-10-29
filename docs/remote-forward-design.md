# 远程转发场景调研方案（网络设备直通 / 双系统 tbox 隧道）

> 状态：调研稿（仅分析，未改动代码）
> 结论优先级：先看第 1 节，再看对应场景章节。

---

## 1. 结论速览

### 1.1 现状能力

shareadb 现在只支持一种拓扑：**设备挂在本地 adb server 上（USB 或已被 `adb connect`），然后把设备的 adbd 端点重新广播出去**。完整流水线（`src/shareadb/device_manager.py`）：

| 阶段 | 动作 | 代码位置 |
| --- | --- | --- |
| 发现 | 轮询 `adb devices -l` | `device_manager.py:415` |
| 获取 | 快路径 `adb forward tcp:F tcp:5555`；失败才 `adb tcpip 5555`（重启 adbd） | `_start` / `_bring_up_forward`，`device_manager.py:196`、`:296` |
| 转发 | 固定 `adb forward tcp:F tcp:<device_tcp_port=5555>` | `device_manager.py:308` |
| 代理 | 监听 `0.0.0.0:P` → `127.0.0.1:F` | `tcp_proxy.py`、`device_manager.py:105` |
| 对外 | 远端 `adb connect <本机IP>:P` | `cli.py:254` |

关键约束：**远端 `adb connect` 连的端口必须最终落到某个 adbd 上**（远端 adb 要跟 adbd 握手）。当前实现靠 `tcpip 5555` 让设备 adbd 监听 5555，再用 forward 把 5555 隧道出来，所以整条链路是「proxy → 本地forward → 设备:5555(adbd)」。

### 1.2 两个场景与现状的差距

| 场景 | 现状行为 | 结论 |
| --- | --- | --- |
| **A. 已 `adb connect 10.0.8.231:7001` 的设备再转发** | 设备 serial 是 `10.0.8.231:7001`，shareadb 照常跑 `adb forward tcp:6000 tcp:5555`，但设备 adbd 在 **7001** 不在 5555 → 隧道指向死端口 | **不可用**（下文有可用 workaround 和正式方案） |
| **B. MTK 8676 tbox + android 双系统，切系统后要 `adb forward` 到 `127.0.0.1:xxx`** | shareadb 没有「自定义 forward 映射」和「把 forward 结果当作新 adb 端点再共享」的能力，也没有切系统后自动重建 forward 的逻辑 | **不支持**，需要新增 tunnel 编排 |

### 1.3 建议的落地方向（详见第 6 节）

1. **P0 — 新增「端点直通」模式（解决 A，也让 B 的一半自动可用）**：当设备 serial 形如 `host:port`（网络设备）时，**跳过 tcpip 和 forward**，proxy 直接指向 `host:port`。零配置、自动生效。
2. **P1 — 新增 `shareadb tunnel`（解决 B）**：声明「源设备 serial + forward 映射 + 是否 adb connect + 要共享哪些端口」，自动下发/重建 forward，切系统/掉线后自愈；并把 tunnel 产生的设备从通用发现里排除，避免 shareadb 对它误跑 `tcpip`。
3. **P2 — 任意 TCP 服务共享**：除 adb 端点外，也支持把 `127.0.0.1:8080` 这类业务端口代理出去（远端用 `adb forward` 或裸 TCP 访问）。

### 1.4 不改代码的临时可用方案

场景 A 单设备，今天就能用（原理见 3.3）：

```bash
shareadbpy --device-tcp-port 7001 --include 10.0.8.231:7001 --proxy-base-port 8000
```

场景 B，今天可以用「手工 forward + shareadb 自动接管」的半自动组合（需注意端口不冲突，见 8.2）：

```bash
# 1) 先建到 android 的隧道，并让本机 adb 认识它
adb -s <tbox-serial> forward tcp:7001 tcp:5555
adb connect 127.0.0.1:7001
# 2) shareadb 启动时排除 tbox（避免被当 USB 设备跑 tcpip），自动接管 127.0.0.1:7001
shareadbpy --exclude <tbox-serial> --proxy-base-port 8000
```

> 但 step 2 里的「自动接管 127.0.0.1:7001」**依赖 P0 落地**；在 P0 之前只能再用 `--device-tcp-port 7001 --include 127.0.0.1:7001` 拼出来，且端口冲突风险更高。

---

## 2. 术语与 adb 语义前提

理解方案前需要确认几条 adb 语义（这些是方案成立的基础）：

1. **`adb forward tcp:L tcp:R` 的 `R` 是「设备上的端口」**，即 adb server 会让设备侧去连 `127.0.0.1:R`（在设备命名空间里）。本地 `L` 是一个由 adb server 创建的监听端口，它只做**裸字节隧道**，不是 adbd 端点。
2. **`adb connect host:port` 连过去的 `port` 必须是 adbd 的监听端口**，远端 adb 会在这个连接上跑 adb 握手协议（CNXN/认证）。所以「proxy 的出口」必须最终是个 adbd。
3. **`adb tcpip N` 会让 adbd 改为监听 `0.0.0.0:N`**（通常全网卡），并重启 adbd。所以「已 `adb connect 10.0.8.231:7001`」的设备，其 adbd 就在 7001。
4. **网络设备在 `adb devices` 里的 serial 就是 `host:port`**，可据此识别，无需额外探测。
5. **adb forward 的映射随传输消失而失效**（换系统、掉线、adb server 重启后都要重建）。
6. **`adb forward` 的本地监听端口在「设备侧打不开」时仍然会 accept**：adb server 先 accept 客户端 TCP，再去设备开 socket，失败才关闭。所以**只做 `connect()` 的探活会误报健康**（见 8.1）。

---

## 3. 场景 A：`adb connect 10.0.8.231:7001` 之后如何再转发

### 3.1 你的目标

`10.0.8.231:7001` 从你这台机器可达（你已经 connect 上了），但**别的机器不可达**（要么不在同一网段，要么 231 只允许你这台访问）。你想把这台设备再广播给自己的局域网，让别人也能 `adb connect`。

### 3.2 为什么当前的 shareadb 会失败

设备 serial = `10.0.8.231:7001`，是「网络设备」，会在 `_sync_devices` 里被当成普通 ready 设备建 session。然后：

- 快路径执行 `adb -s 10.0.8.231:7001 forward tcp:6000 tcp:5555`；
- 这条 forward **注册成功**（adb 只登记映射，不校验设备侧端口是否有人听），
- 于是 shareadb 认为健康，广播 `adb connect <你>:6000`；
- 但设备 adbd 在 **7001**，`tcp:5555` 没人听 → 远端 connect 后握手失败（先连上再被 RST/挂起）。

更危险的是：一旦快路径失败，慢路径会执行 `adb tcpip 5555`，**把 231 那台设备的 adbd 监听端口从 7001 改成 5555 并重启 adbd**，直接打断你现有的 `10.0.8.231:7001` 连接和别人的使用。

### 3.3 今天可用的 workaround（不用改代码）

把「设备端 adb 端口」显式告诉 shareadb，让它 forward 到正确的端口：

```bash
shareadbpy --device-tcp-port 7001 --include 10.0.8.231:7001 --proxy-base-port 8000
```

原理：`adb -s 10.0.8.231:7001 forward tcp:6000 tcp:7001`，设备侧 7001 就是 adbd（`tcpip` 模式下 adbd 绑 `0.0.0.0`），隧道打通 → proxy 出口是真 adbd。

局限（正是要修的地方）：
- `--device-tcp-port` 是**全局**的，多台 adb 端口各不相同的网络设备没法同时共享；
- 仍然多绕了一跳「本地 forward」，没必要；
- 探活误报（8.1）依然存在；
- 用户得手工算出并填对端口。

替代（完全绕开 shareadb，纯 TCP 转发）：

```bash
socat -s tcp4-listen:8000,reuseaddr,fork tcp-connect:10.0.8.231:7001
# 远端起：adb connect <你>:8000
```

### 3.4 正式方案：端点直通（Endpoint Passthrough）

**规则：若设备 serial 匹配 `^host:port$` 且它是 adb 网络设备，则该 session 采用直通模式——不 `tcpip`、不 `forward`，proxy 目标直接设为 `host:port`。**

理由：
- adb 网络设备本身就是「TCP 可达的 adbd」，直接把这条 TCP 连接代理出去即可，出口天然是真 adbd；
- 省掉中间 forward，少一次协议跳转；
- 彻底避免对远端设备误跑 `tcpip`（安全）。

落点很小：`ADBDeviceSession` 里增加一个 `target_host/target_port` 概念（`TCPProxyServer` 本来就接任意 `target_host/target_port`，见 `tcp_proxy.py:65`），只在 `_start`/`_bring_up_forward` 里按模式分支。

配置上再加一个手动入口，覆盖「端点不在 adb devices 里」或想指定端口的情况：

```bash
--share-endpoint 10.0.8.231:7001            # 自动分配 proxy 端口
--share-endpoint 10.0.8.231:7001:8000       # 指定 proxy 端口
```

---

## 4. 场景 B：MTK 8676 双系统（tbox + android）的换系统转发

### 4.1 你描述的工作流

- 8676 板子上有 **tbox** 和 **android** 两个系统，USB/网络 adb 的「入口」通常是 tbox 的 adbd；
- 切到某个系统（例如 android）后，该系统的 adbd 不是直接可达的，**必须先用 `adb forward` 打个隧道，再连到 `127.0.0.1:xxx`**，之后才「能用」。

### 4.2 对应的标准命令序列（本机视角）

```bash
# a) 通过 tbox 的 adb 通道，把 android 侧 adbd 隧道到本机 7001
adb -s <tbox-serial> forward tcp:7001 tcp:5555
# b) 让本机 adb server 认识「android」这台设备
adb connect 127.0.0.1:7001
# c) 之后 adb -s 127.0.0.1:7001 ... 即可
```

一次「切系统」会带来三种变化，shareadb 都要能扛：

| 变化 | 影响 | 需要的行为 |
| --- | --- | --- |
| tbox 传输重建（serial 可能变/短暂消失） | 之前的 forward 全部失效 | 重新下发 forward |
| android 侧 adbd 重新上线 | `127.0.0.1:7001` 这条 connect 变 stale（offline） | 重新 `adb connect` / `reconnect` |
| adbd 端口可能随系统改变 | 需要改 forward 的目标端口 | 配置可按「系统」声明多套映射 |

### 4.3 缺口清单（当前实现没有的东西）

1. **每设备可配的 forward 映射**：现在只有全局 `--device-tcp-port` + 固定 `tcp:F -> tcp:5555` 一处映射。需要支持「设备 A 的 `tcp:L -> tcp:R`」任意条数。
2. **把 forward 的产物当作新 adb 端点再共享**：`127.0.0.1:7001` 需要被识别成一个可直通的 adb 端点（正好是 P0 的成果），并且不能对它跑 `tcpip`。
3. **`adb connect` 编排**：可选地由 shareadb 自己执行 connect（而不是靠用户手工）。
4. **声明式配置**：一张「tunnel」表，描述源设备、forward、connect、要共享的端口。
5. **生命周期/自愈**：源设备出现→建 forward；源设备消失（切系统）→拆 proxy 但**保留下发意图**；再出现→重建 forward + reconnect + 重新 proxy。
6. **避免对 tunnel 相关设备做通用管理**：把 tunnel 的 endpoint（`127.0.0.1:7001`）和源设备（tbox）从通用 auto-discovery 里排除，否则会对 tbox 误跑 `tcpip`。

### 4.4 正式方案：`shareadb tunnel`

新增子命令（或 `--config` 里的一条 `tunnels` 项）：

```bash
shareadbpy tunnel add \
  --name 8676-android \
  --source <tbox-serial> \
  --forward 7001:5555 \
  --forward 7002:8080 \
  --connect \
  --share 7101=127.0.0.1:7001 \
  --share 7102=127.0.0.1:7002
```

语义：
- `--source <serial>`：提供 adb 通道的源设备（tbox）。也支持 `--source-match` 按 product/model 正则选，应对 serial 变化。
- `--forward L:R`（可重复）：`adb -s <source> forward tcp:L tcp:R`。
- `--connect`：随后对每个 forward 的 `127.0.0.1:L` 执行 `adb connect`（幂等：已存在先 disconnect 再 connect，或直接 `adb connect` 覆盖）。
- `--share P=TARGET`（可重复）：把 `TARGET` 用 TCP proxy 暴露到本机 `0.0.0.0:P`。`TARGET` 是 `127.0.0.1:L` 时就是 adb 端点（远端 `adb connect <你>:P`）；是其它端口时就是业务 TCP 服务（远端用 `adb forward` 或裸 TCP 连）。
- 运行期：一个 tunnel watcher 轮询源设备状态与 forward 存活，掉线/换系统后自动重建（复用现有 `_run_loop` 的思路与 `RETRY_BACKOFF` 等常量）。
- 与通用设备发现互斥：tunnel 声明的 source 与 endpoint 自动进入 exclude 集合，通用 session 不去碰。

等价配置（JSON，保持零依赖，见 6.3）：

```json
{
  "tunnels": [
    {
      "name": "8676-android",
      "source": { "serial": "<tbox-serial>" },
      "forwards": [ { "local": 7001, "remote": 5555 },
                    { "local": 7002, "remote": 8080 } ],
      "connect": true,
      "shares": [ { "kind": "adb", "target": "127.0.0.1:7001", "proxy_port": 7101 },
                  { "kind": "tcp", "target": "127.0.0.1:7002", "proxy_port": 7102 } ]
    }
  ]
}
```

> 对「切系统后 tbox serial 变了」的情况，`source.match` 用 `adb devices -l` 的 `product`/`model` 字段做正则匹配更稳；如果 tbox 的 serial 稳定，直接用 serial 即可。

---

## 5. 目标能力模型（统一抽象）

把「要共享的东西」抽象成 **ShareTarget**，每条 target 自带「获取方式（acquisition）」+「出口（proxy）」：

```
ShareTarget
├─ name
├─ acquisition:
│   ├─ usb       : 设备在本地 adb server 上（USB）→ 需要 tcpip/forward 使其 TCP 可达（= 现状）
│   ├─ endpoint  : 直接给 host:port（网络设备 / forward 结果）→ 跳过 tcpip/forward  ← P0
│   └─ tunnel    : 声明 forward+connect 的编排，产物是一个 endpoint ← P1
└─ expose:
    ├─ proxy 0.0.0.0:P -> <endpoint>       (adb)  远端 adb connect <you>:P
    └─ proxy 0.0.0.0:P -> <service host:port> (tcp)  远端裸 TCP / adb forward
```

三种 acquisition 的最后一步都收敛为「一个 TCP 可达的 endpoint」，因此**代理层（`tcp_proxy.py`）和探活/自愈/访问控制可以完全复用**。这是本方案改动可控的关键。

---

## 6. 方案设计明细

### 6.1 Session 层重构（`device_manager.py`）

把 `ADBDeviceSession` 里写死的 `("127.0.0.1", self._forward_port)` 换成显式的 `_target_host/_target_port` 与 `_acquisition` 枚举：

```python
class Acquisition(Enum):
    USB = "usb"          # 现状：tcpip 兜底 + adb forward
    ENDPOINT = "endpoint" # 直通：直接代理 host:port

# _start():
if self._acquisition is Acquisition.ENDPOINT:
    await self._proxy.start()          # 不做 tcpip / forward / wait_for_device
    return
# ... 原 USB 路径不变
```

健康探活也要分模式：ENDPOINT 模式探 `_target_host:_target_port`（真实 adbd），USB 模式维持现状（见 8.1 的改进建议）。

### 6.2 发现层（`_sync_devices`）

- 识别网络设备：`_split_host_port(serial)` 命中则对该 serial 用 `Acquisition.ENDPOINT`，target = serial 拆出的 `host:port`。
- reflection 判断需要扩展：现在 `_is_reflection` 依赖 `local_ips` + `served_proxy_ports`（`device_manager.py:495`）。直通模式下多了一种真·合法情形——`127.0.0.1:<用户自己 forward 的端口>`。只要该端口**不是我们 serve 的 proxy 端口**，就不算 reflection（现逻辑已满足）；但**默认 proxy 端口从 7000 起，极易和用户 forward 的 7001 撞上** → 见 8.2，必须给出告警/自动避让。
- tunnel 的 source 与 endpoint 加入 exclude，避免通用逻辑误碰。

### 6.3 配置与 CLI

- **不引入运行时依赖**：项目承诺纯标准库。配置用 **JSON**（`--config config.json`），CLI 覆盖/追加配置项。
- 新增参数（向后兼容，默认关闭直通则行为不变）：
  - `--share-endpoint HOST:PORT[:PROXY_PORT]`（可重复）
  - `--share-tcp HOST:PORT[:PROXY_PORT]`（可重复，P2）
  - `--config PATH`（P1）
  - `--no-endpoint-passthrough`（若担心兼容性，允许关掉自动识别）
- 子命令（P1）：`shareadb tunnel add|remove|list`。因为现有 `main()` 是 `argparse` 单命令，子命令要引入 `add_subparsers`，需注意不要破坏 `shareadbpy` 默认行为（无子命令时等价于现在的启动）。

### 6.4 对外通告（`cli.py:254` 的 `_print_connection_info`）

要区分两类 share 的用法提示：

- adb 端点：`adb connect <you>:P`（现状不变）；
- 业务 TCP：`adb -s <dev> forward tcp:X tcp:P` 或直接连 `<you>:P`（新增提示）。

### 6.5 安全

- 直通模式把「远端设备的 adbd」暴露给 LAN：**远端机器的 adb 公钥必须在设备上已授权**，否则握手会被设备拒绝（或设备处于 insecure 模式才行）。文档要写清楚。
- 访问控制的 `--allow/--deny`、`kick/ban` 在直通模式同样生效（复用同一 `AccessPolicy`，因代理层未变），这是复用收益。

---

## 7. 改动清单（按文件）

| 文件 | 改动 | 阶段 |
| --- | --- | --- |
| `src/shareadb/device_manager.py` | `Acquisition` 枚举；`ADBDeviceSession` 支持 endpoint 模式；`_sync_devices` 识别网络设备；exclude 扩展 | P0 |
| `src/shareadb/adb_client.py` | 新增 `connect_host`/`disconnect_host`/`forward_custom`（自定义 L:R）、`tcpip` 仅 USB 用；`is_network_serial` 工具函数 | P0/P1 |
| `src/shareadb/cli.py` | `--share-endpoint`、`--share-tcp`、`--config`；通告文案；子命令挂载 | P0/P1 |
| `src/shareadb/config.py`（新） | JSON 配置解析与校验 | P1 |
| `src/shareadb/tunnel.py`（新） | tunnel watcher：forward 下发/重建、connect 编排、自愈 | P1 |
| `src/shareadb/admin_console.py` | `tunnels` 命令；`status` 显示 acquisition 模式 | P1 |
| `README.md` | 新章节：网络设备直通、双系统 tunnel | P0/P1 |
| `tests/` | 见第 9 节 | P0/P1 |

---

## 8. 风险与边界

### 8.1 探活误报（现存缺陷，直通模式顺带缓解）

`_default_port_probe`（`device_manager.py:34`）只做 `connect()`，而 adb forward 的本地监听**先 accept 再连设备**，所以「设备侧打不开」时探活仍返回 True。现状因此可能把死隧道报成健康。

- 直通模式：target 就是真 adbd，`connect()` 成功即代表有真监听 → 误报消失；
- USB 模式：建议探活升级为「真实往返」，例如对 serial 执行 `adb -s <serial> get-state`，或对端口做一次 adb 握手（发送 CNXN 头看是否回包）。至少要在文档里标注该局限。

### 8.2 端口冲突（重点，场景 B 必踩）

默认 `--proxy-base-port 7000`，而场景 B 里用户手工 forward 常用的本地端口就是 7001/7002。若 `127.0.0.1:7001` 恰好是我们 serve 的 proxy 端口，`_is_reflection` 会把它当自反射设备 **主动 `adb disconnect` 掉**（`device_manager.py:531`），表现为「设备刚连上就消失」。

缓解：
- 文档明确要求 tunnel 的 forward 本地端口与 proxy 端口段分离（如 forward 用 7000 段、proxy 用 8000 段）；
- 代码在分配前增加冲突检测并告警；
- P1 的 tunnel 自动分配 forward 端口时避开已用 proxy 端口。

### 8.3 切勿对网络设备跑 `tcpip`

直通模式必须彻底跳过 `tcpip`，否则会改掉远端 adbd 端口并重启它，打断所有使用者。这是把「网络设备误判为 USB 设备」这类 bug 从「功能不对」升级为「破坏性」的关键点，需在代码里用显式分支 + 测试锁死。

### 8.4 换系统时 serial 变化

8676 切系统后 tbox 的 serial/product 可能变化，`--source <serial>` 会失配。用 `--source-match`（正则匹配 `adb devices -l` 的 product/model）兜底；必要时允许 `--source` 给多个候选并取第一个 ready 的。

### 8.5 非 adb 服务的远端用法

业务端口（如 8080）共享出去后，远端不能 `adb connect`。要么裸 TCP 直连，要么远端本地 `adb -s <已连设备> forward tcp:8080 tcp:8080`。通告文案要区分，避免用户误用。

### 8.6 adb server 只有一个

shareadb 与本地 scrcpy/Android Studio 共用同一个 adb server。tunnel 反复 `adb connect`/`forward` 会改动 server 状态，可能影响本地工具；沿用现有 `--exclude` 与「自愈防抖」思路（`FLAP_RECHECK_DELAY`）降低波动。

### 8.7 Windows

`socat` 不可用，纯 Python 代理本身跨平台（项目卖点），本方案不引入外部依赖即保持该优势。

---

## 9. 验证计划

**单元测试（无真机，用 `tests/fakes.py` 的 fake adb）**
- 直通模式：网络设备 serial → 断言**不发生** `tcpip`、**不发生** `forward`，proxy target == `host:port`；
- 端口冲突：forward 本地端口 == proxy 端口时给出告警且不误 disconnect；
- tunnel：源设备出现→下发 forward+connect+proxy；源设备消失→拆 proxy 保留意图；再出现→重建（断言 forward 被重新下发）；
- 配置解析：JSON 校验、非法项报错；
- 回归：现有 USB 路径行为不变（`test_device_manager.py` 全绿）。

**真机矩阵**
1. 单网络设备：`adb connect 10.0.8.231:7001` → shareadb 直通 → 另一台机器 `adb connect <you>:P` 成功 `adb shell`；
2. 多网络设备且 adb 端口不同：同时直通，端口不串；
3. 8676 双系统：tbox USB 接入 → tunnel 建 forward+connect → 共享 → **切系统** → 观察 forward 重建、connect 恢复、远端无明显中断（或按预期短暂中断后自愈）；
4. 冲突场景：故意让 forward 端口落在 proxy 段，确认有告警而非静默 disconnect；
5. 访问控制：直通模式下 `ban/kick` 生效（复用路径回归）。

---

## 10. 分期落地建议

- **P0（建议先做，收益最大、改动最小）**：端点直通（自动识别网络设备 + `--share-endpoint`）。直接解决场景 A，并让「手工 forward + connect」的半自动方案在场景 B 稳定可用（配合 `--exclude <tbox>` 与端口分离）。
- **P1**：`shareadb tunnel` + JSON 配置 + watcher 自愈。彻底自动化场景 B。
- **P2**：任意 TCP 服务共享 + 探活升级（`get-state` 往返）。

---

## 11. 待你确认的问题

1. 场景 B 里 `127.0.0.1:xxx` 是**adb 端点**（远端 `adb connect`）还是**业务服务端口**（如某 SDK 的 8080）？这决定 P0 是否够用、还是要立刻上 P2。
2. 8676 切系统后，**tbox 的 serial/product 是否变化**？决定 `--source` 用固定 serial 还是正则匹配。
3. 是否需要 shareadb **自己执行 `adb connect`**，还是接受「用户手工 connect、shareadb 只负责共享」？后者能让 P0 就覆盖大半个场景 B。
4. 是否需要「一份配置长期守护」（daemon/开机自启），还是每次手工起？
