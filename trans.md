# ShareADB GUI 应用迁移计划 (Electron 版本)

## 项目概述

将 Python CLI 版本的 shareadb 转换为 Electron 跨平台桌面应用程序，使用现代 Web 技术实现友好的用户界面。

## 技术方案

### 框架选择
- **核心框架**: Electron 28+ (最新稳定版)
- **开发语言**: TypeScript (类型安全)
- **前端框架**: React 18+ + Vite (快速开发、热更新)
- **UI 组件库**: 
  - 首选: Ant Design (桌面应用友好，组件丰富)
  - 备选: Material-UI 或 shadcn/ui
- **样式方案**: Tailwind CSS 或 Styled Components
- **构建工具**: Vite 5+ (快速构建)
- **进程通信**: Electron IPC (主进程与渲染进程通信)

### 核心依赖
- **依赖较少**: Electron 自带 Node.js 环境
- **ADB 调用**: 通过 Node.js `child_process` 执行 Python 脚本或直接调用 ADB 命令
- **网络通信**: 使用 Node.js `net` 模块实现 TCP 代理
- **无原生依赖**: 无需安装额外的系统库

### 方案优势
✅ **真正的跨平台**: 一套代码，自动打包成 Windows/macOS/Linux 应用  
✅ **无需运行时**: 打包后独立运行，用户无需安装 .NET 或 Python  
✅ **现代 UI**: 利用 Web 技术实现美观、响应式界面  
✅ **热更新**: 开发时支持热重载，开发体验好  
✅ **生态丰富**: npm 生态丰富，组件库成熟  
✅ **调试方便**: Chrome DevTools 内置，调试体验佳  

## 功能需求清单

### 核心功能 (必须实现)
- [ ] **主窗口设计**
  - [ ] 使用 Electron 创建主窗口
  - [ ] 响应式布局设计
  - [ ] 窗口状态管理（最小化/最大化/关闭）
  - [ ] 系统托盘集成（最小化到托盘）

- [ ] **ADB 环境配置**
  - [ ] 自动检测系统中的 ADB（PATH 环境变量）
  - [ ] 手动选择 ADB 可执行文件路径
  - [ ] 验证 ADB 功能（执行 `adb version` 测试）
  - [ ] 保存 ADB 路径配置（electron-store 持久化）

- [ ] **设备管理** ⭐ 核心功能
  - [ ] 设备自动检测（定期轮询）
  - [ ] 设备列表组件（序列号、型号、状态）
  - [ ] 设备状态实时更新（WebSocket 或 IPC 通信）
  - [ ] **新设备自动上线确认**
    - [ ] 检测到新设备时自动显示通知
    - [ ] 弹出确认对话框询问是否共享
    - [ ] 支持全局设置：自动共享新设备 vs 手动确认
    - [ ] 提供设备预览信息（型号、序列号等）
  - [ ] **共享设备列表管理**
    - [ ] 添加/移除设备到共享列表
    - [ ] 设备白名单/黑名单机制
    - [ ] 右键菜单（共享、忽略、取消共享）
    - [ ] 批量操作支持（全选、反选、批量共享）
    - [ ] 保存设备共享偏好（持久化配置）
    - [ ] 设备状态标记：
      - 待确认（新设备，等待用户决定）
      - 已共享（正在转发）
      - 已忽略（不共享）
      - 已断开（设备离线但仍保留配置）
  - [ ] 单个设备的启动/停止控制

- [ ] **TCP 代理功能**
  - [ ] 为每个设备分配唯一端口
  - [ ] 端口自动递增（Forward base port: 6000, Proxy base port: 7000）
  - [ ] 使用 Node.js `net` 模块建立 TCP 代理服务器
  - [ ] 支持多客户端连接
  - [ ] 自动重连机制（设备断开后自动尝试重新连接）
  - [ ] 根据共享列表动态启动/停止代理

- [ ] **网络信息显示**
  - [ ] 自动检测本机所有可用 IP 地址
  - [ ] 生成分享连接命令（`adb connect <IP>:<PORT>`）
  - [ ] 一键复制连接信息功能
  - [ ] 按设备显示对应的连接端口和命令

- [ ] **应用设置**
  - [ ] 监听地址设置（默认 0.0.0.0）
  - [ ] 设备 TCP 端口设置（默认 5555）
  - [ ] Forward 基础端口设置（默认 6000）
  - [ ] Proxy 基础端口设置（默认 7000）
  - [ ] 轮询间隔设置（默认 5 秒）
  - [ ] 状态刷新间隔设置（默认 30 秒）
  - [ ] 新设备自动共享开关（默认 false，需要确认）
  - [ ] 保存/加载配置（electron-store）

### 增强功能 (可选实现)
- [ ] 系统托盘右键菜单（快速操作）
- [ ] 开机自动启动选项
- [ ] 桌面通知（设备连接/断开通知）
- [ ] 连接日志查看器
- [ ] 多语言支持（中文/英文 i18n）
- [ ] 暗色/亮色主题切换
- [ ] 单个设备的详细信息面板
- [ ] 端口使用情况监控
- [ ] 连接客户端数量统计
- [ ] 设备截图预览（调用 adb shell screencap）
- [ ] 设备命名功能（为设备设置别名）
- [ ] 设备分组（组织多个设备）
- [ ] 应用更新检查（electron-updater）
- [ ] 导出配置/导入配置
- [ ] 统计信息图表（设备连接时长、使用次数等）

## 架构设计

### 项目结构
```
shareadb-electron/
├── package.json                 # 项目配置
├── tsconfig.json               # TypeScript 配置
├── vite.config.ts              # Vite 配置
├── electron.vite.config.ts     # Electron Vite 配置
├── .gitignore
├── ├── src/
│   ├── main/                   # 主进程
│   │   ├── index.ts            # 主进程入口
│   │   ├── ipc/
│   │   │   ├── handlers/       # IPC 处理器
│   │   │   │   ├── device.ts   # 设备相关 IPC
│   │   │   │   ├── adb.ts      # ADB 相关 IPC
│   │   │   │   ├── config.ts   # 配置相关 IPC
│   │   │   │   └── network.ts  # 网络相关 IPC
│   │   ├── services/
│   │   │   ├── ADBService.ts   # ADB 命令执行服务
│   │   │   ├── DeviceManager.ts     # 设备管理器
│   │   │   ├── DeviceListManager.ts # 设备列表和共享管理 ⭐
│   │   │   ├── TcpProxyService.ts   # TCP 代理服务
│   │   │   └── ConfigService.ts     # 配置管理服务
│   │   ├── windows/
│   │   │   ├── main.ts        # 主窗口创建
│   │   │   ├── settings.ts    # 设置窗口
│   │   │   └── newDevice.ts   # 新设备对话框窗口 ⭐
│   │   └── preload/
│   │       └── index.ts        # 预加载脚本
│   ├── renderer/               # 渲染进程
│   │   ├── main.tsx            # React 入口
│   │   ├── App.tsx             # 主应用组件
│   │   ├── components/         # React 组件
│   │   │   ├── DeviceList.tsx      # 设备列表组件 ⭐
│   │   │   ├── DeviceItem.tsx      # 设备项组件
│   │   │   ├── SettingsPanel.tsx   # 设置面板
│   │   │   ├── LogViewer.tsx       # 日志查看器
│   │   │   ├── NetworkInfo.tsx     # 网络信息显示
│   │   │   └── TrayMenu.tsx        # 托盘菜单
│   │   ├── hooks/              # React Hooks
│   │   │   ├── useDevices.ts       # 设备数据 Hook
│   │   │   ├── useADB.ts           # ADB Hook
│   │   │   └── useConfig.ts        # 配置 Hook
│   │   ├── stores/             # 状态管理
│   │   │   └── deviceStore.ts      # 设备状态管理
│   │   ├── types/              # TypeScript 类型
│   │   │   ├── device.ts           # 设备类型定义
│   │   │   ├── config.ts           # 配置类型定义
│   │   │   └── ipc.ts              # IPC 类型定义
│   │   ├── utils/              # 工具函数
│   │   │   ├── network.ts          # 网络工具
│   │   │   └── format.ts           # 格式化工具
│   │   └── assets/             # 静态资源
│   │       └── icons/
│   ├── preload/                # 预加载脚本
│   └── shared/                 # 共享代码
│       └── types.ts
├── resources/                  # 资源文件
│   └── icons/
├── scripts/                    # 构建脚本
│   ├── build.js
│   └── package.js
└── README-ELECTRON.md          # Electron 版本文档
```

### 技术架构

#### 进程通信架构
```
渲染进程 (React) 
    ↓ IPC 调用
预加载脚本 (expose API)
    ↓ IPC 通信
主进程 (Node.js)
    ↓ 调用服务
服务层 (Services)
    ↓ 系统调用
外部系统 (ADB/OS)
```

#### 核心服务设计

##### ADBService
- **职责**: 封装 ADB 命令执行
- **技术**: Node.js `child_process.exec` / `execSync`
- **主要方法**:
  - `detectADBPath()` - 检测 ADB 路径
  - `executeCommand()` - 执行 ADB 命令
  - `listDevices()` - 列出设备
  - `forwardPort()` - 端口转发
  - `removeForward()` - 移除端口转发
  - `shellCommand()` - 执行 shell 命令

##### DeviceManager
- **职责**: 管理所有设备的生命周期
- **技术**: Node.js `setInterval` 轮询
- **事件驱动**: 发布-订阅模式
- **主要方法**:
  - `startMonitoring()` - 开始设备监控
  - `stopMonitoring()` - 停止设备监控
  - `getDeviceStatuses()` - 获取所有设备状态
  - `enableDevice()` - 启用设备共享
  - `disableDevice()` - 禁用设备共享

##### DeviceListManager ⭐
- **职责**: 管理设备列表和共享配置
- **技术**: electron-store 持久化
- **主要方法**:
  - `loadShareConfig()` - 加载设备共享配置
  - `saveShareConfig()` - 保存设备共享配置
  - `addDeviceToShareList()` - 添加设备到共享列表
  - `removeDeviceFromShareList()` - 从共享列表移除设备
  - `getDeviceShareState()` - 获取设备共享状态
  - `setDeviceShareState()` - 设置设备共享状态
  - `shouldAutoShareNewDevice()` - 判断是否自动共享新设备
  - `markDeviceAsIgnored()` - 标记设备为忽略

##### TcpProxyService
- **职责**: TCP 代理服务器实现
- **技术**: Node.js `net.createServer`
- **主要方法**:
  - `start()` - 启动代理服务器
  - `stop()` - 停止代理服务器
  - `handleClient()` - 处理客户端连接
  - `forwardData()` - 转发数据

##### ConfigService
- **职责**: 配置管理
- **技术**: electron-store
- **主要方法**:
  - `loadSettings()` - 加载配置
  - `saveSettings()` - 保存配置
- `validateSettings()` - 验证配置

### 数据模型设计

#### TypeScript 类型定义

##### Device Types
```typescript
export enum DeviceShareState {
  Pending,        // 待确认（新设备）
  Shared,         // 已共享
  Ignored,        // 已忽略
  Disconnected    // 已断开
}

export interface ADBDeviceInfo {
  serial: string;
  state: 'device' | 'offline' | 'unauthorized' | 'unknown';
  product?: string;
  model?: string;
  device?: string;
  transport_id?: string;
}

export interface DeviceShareConfig {
  serial: string;
  name?: string;  // 用户自定义名称
  state: DeviceShareState;
  forwardPort?: number;
  proxyPort?: number;
  lastSeen?: string;  // ISO 8601 日期字符串
}

export interface DeviceStatus extends ADBDeviceInfo {
  shareState: DeviceShareState;
  forwardPort?: number;
  proxyPort?: number;
  lastError?: string;
}
```

##### Config Types
```typescript
export interface AppSettings {
  ADBPath: string;
  ListenHost: string;
  DeviceTCPPort: number;
  ForwardBasePort: number;
  ProxyBasePort: number;
  PollInterval: number;
  StatusInterval: number;
  MinimizeToTray: boolean;
  StartMinimized: boolean;
  AutoStart: boolean;
  Theme: 'Light' | 'Dark' | 'Auto';
  AutoShareNewDevices: boolean;  // ⭐ 新增
  ShowNewDeviceDialog: boolean;   // ⭐ 新增
  NewDeviceDialogTimeout: number; // ⭐ 新增
}
```

### UI 组件设计

#### 主界面布局
```tsx
<div className="main-container">
  <Header>
    <Title>ShareADB</Title>
    <SettingsButton />
    <MinimizeButton />
  </Header>
  
  <DeviceListPanel>
    <DeviceFilter />
    <DeviceList>
      <DeviceItem />
      <DeviceItem />
      <DeviceItem />
    </DeviceList>
    <BatchActions>
      <Button>全选</Button>
      <Button>批量共享</Button>
      <Button>批量忽略</Button>
    </BatchActions>
  </DeviceListPanel>
  
  <NetworkInfoPanel>
    <IPAddresses />
    <ConnectionCommands />
  </NetworkInfoPanel>
  
  <LogViewer />
</div>
```

#### 设备列表项组件
```tsx
<DeviceItem>
  <DeviceInfo>
    <DeviceIcon state={device.state} />
    <DeviceName>{device.name}</DeviceName>
    <DeviceSerial>{device.serial}</DeviceSerial>
  </DeviceInfo>
  
  <DeviceStatus>
    <StatusBadge state={device.shareState} />
    <ConnectionString>{connectionCommand}</ConnectionString>
  </DeviceStatus>
  
  <DeviceActions>
    <ShareToggleButton />
    <ContextMenuTrigger />
  </DeviceActions>
</DeviceItem>
```

### 界面交互流程

#### 新设备检测流程
```
1. DeviceManager 检测到新设备
2. 触发 'device-detected' IPC 事件
3. 渲染进程接收到事件
4. 检查 AutoShareNewDevices 设置
   - 如果为 true：自动添加到共享列表
   - 如果为 false：显示 NewDeviceDialog (新窗口)
5. 用户在对话框中选择：
   - "共享"：添加到共享列表并启动代理
   - "忽略"：标记为忽略状态，不启动代理
   - "取消"：保持待确认状态
6. 通过 IPC 保存用户选择到配置文件
7. 更新设备列表 UI
```

#### 设备列表右键菜单
```
添加到共享列表 / 从共享列表移除
查看详细信息
设备重命名
复制序列号
---------
启动共享
停止共享
---------
忽略此设备（不再提示）
```

## 实现步骤

### 阶段 1: 项目初始化 (Day 1-2)
1. 使用 electron-vite 创建 Electron + React + TypeScript 项目
2. 安装必要依赖（electron-store, antd, 等）
3. 配置开发环境（热更新、TypeScript、ESLint）
4. 设计主窗口布局和路由

### 阶段 2: ADB 集成 (Day 2-3)
1. 实现 ADBService 服务类
2. 实现自动检测 ADB 功能
3. 实现 ADB 命令执行封装
4. 实现 ADB 设备列表获取
5. 实现测试 IPC 通道

### 阶段 3: 设备管理（核心） ⭐ (Day 3-5)
1. 实现 DeviceManager 服务类
2. **实现设备轮询检测**
3. **实现 DeviceListManager 服务**
4. **实现新设备对话框窗口**
5. **实现设备列表 React 组件**
6. **实现设备右键菜单**
7. **实现批量操作功能**
8. **实现设备状态实时更新（IPC + React Hooks）**

### 阶段 4: TCP 代理 (Day 5-7)
1. 实现 TcpProxyService 服务类
2. 实现 TCP 代理服务器
3. 实现端口分配管理
4. 实现多客户端连接支持
5. 实现数据转发逻辑
6. 性能测试和优化

### 阶段 5: 网络和配置 (Day 7-8)
1. 实现本机 IP 检测工具
2. 实现连接信息生成和显示组件
3. 实现 ConfigService (electron-store)
4. 实现设置界面组件
5. 实现设备共享配置持久化 ⭐

### 阶段 6: UI 优化和主题 (Day 8-9)
1. 实现暗色/亮色主题切换
2. 优化组件样式和交互
3. 添加过渡动画
4. 实现响应式布局
5. 系统托盘集成

### 阶段 7: 测试和调试 (Day 9-10)
1. 单元测试（Jest/Vitest）
2. 集成测试
3. 跨平台测试（Windows、macOS、Linux）
4. 单设备和多设备场景测试
5. 设备断开重连测试
6. **新设备自动上线和确认流程测试** ⭐
7. **设备添加/移除功能测试** ⭐
8. 性能测试和内存泄漏检查

### 阶段 8: 打包和发布 (Day 10-11)
1. 配置 electron-builder
2. 生成 Windows/macOS/Linux 安装包
3. 配置应用图标和元数据
4. 编写 README 和使用文档
5. 配置自动更新（可选）

## 关键技术点

### 1. Electron IPC 通信
```typescript
// 主进程
ipcMain.handle('device:list', async () => {
  return deviceManager.getDeviceStatuses();
});

// 预加载脚本
contextBridge.exposeInMainWorld('electronAPI', {
  listDevices: () => ipcRenderer.invoke('device:list')
});

// 渲染进程
const devices = await window.electronAPI.listDevices();
```

### 2. 进程管理
- 使用 `child_process.spawn` 执行 ADB 命令
- 进程错误捕获和超时处理
- 进程资源清理

### 3. TCP 服务器
```typescript
const server = net.createServer((clientSocket) => {
  clientSocket.on('data', (data) => {
    // 转发到目标设备
    targetSocket.write(data);
  });
});
```

### 4. 持久化存储
```typescript
import Store from 'electron-store';
const store = new Store();
store.set('settings', settings);
```

### 5. 状态管理
- 使用 React Context + Hooks
- 或使用 Zustand/Jotai 等轻量状态库
- 通过 IPC 同步主进程状态

### 6. 跨平台兼容
- 路径处理: 使用 `path.join()`
- 进程执行: 适配不同平台的命令路径
- 权限处理: 读写文件权限

### 7. 性能优化
- 虚拟滚动（设备列表）
- 节流/防抖（状态更新）
- 内存管理（及时清理资源）

## 配置文件

### settings.json (electron-store)
```json
{
  "ADBPath": "adb",
  "ListenHost": "0.0.0.0",
  "DeviceTCPPort": 5555,
  "ForwardBasePort": 6000,
  "ProxyBasePort": 7000,
  "PollInterval": 5.0,
  "StatusInterval": 30.0,
  "MinimizeToTray": true,
  "StartMinimized": false,
  "AutoStart": false,
  "Theme": "Light",
  "AutoShareNewDevices": false,
  "ShowNewDeviceDialog": true,
  "NewDeviceDialogTimeout": 30
}
```

### device_shares.json
```json
{
  "devices": [
    {
      "serial": "abc123def456",
      "name": "我的手机",
      "state": 1,
      "forwardPort": 6000,
      "proxyPort": 7000,
      "lastSeen": "2026-01-16T16:00:00Z"
    },
    {
      "serial": "xyz789",
      "name": "",
      "state": 2,
      "forwardPort": null,
      "proxyPort": null,
      "lastSeen": "2026-01-16T15:30:00Z"
    }
  ]
}
```

## 开发工具推荐

- **IDE**: VSCode (Electron 生态支持最好)
- **调试**: Chrome DevTools (Electron 内置)
- **测试**: Vitest (单元测试) + Playwright (E2E 测试)
- **构建**: electron-vite (开发体验好)
- **打包**: electron-builder (成熟的打包工具)

## 待确认事项

1. **UI 组件库**: 
   - Ant Design (推荐，桌面友好)
   - Material-UI
   - shadcn/ui (更现代)

2. **状态管理**:
   - React Context + Hooks (轻量)
   - Zustand (简单好用)
   - Redux Toolkit (功能强大)

3. **主题架构**:
   - Ant Design 主题定制
   - CSS 变量 + Tailwind
   - CSS Modules

4. **打包方式**:
   - NSIS (Windows)
   - DMG (macOS)
   - AppImage/DEB/RPM (Linux)

5. **增强功能**:
   - 哪些可选功能是必须的？
   - 是否需要多语言支持？
   - 是否需要自动更新功能？

## 时间估算（仅供参考）

- 阶段 1: 1-2 天
- 阶段 2: 1-2 天
- 阶段 3: 2-3 天
- 阶段 4: 2-3 天
- 阶段 5: 1-2 天
- 阶段 6: 1-2 天
- 阶段 7: 1-2 天
- 阶段 8: 1-2 天

总计: 约 2 周全职开发

## 参考资源

- [Electron 官方文档](https://www.electronjs.org/docs)
- [Electron Vite](https://electron-vite.org/)
- [React 官方文档](https://react.dev/)
- [Ant Design](https://ant.design/)
- [electron-store](https://github.com/sindresorhus/electron-store)
- [electron-builder](https://www.electron.build/)
- [Electron 最佳实践](https://www.electronjs.org/docs/latest/tutorial/best-practices)

---
**创建时间**: 2026-01-16
**最后更新**: 2026-01-16
**版本**: v2.0 (从 C# 迁移到 Electron)
**技术栈**: Electron + React + TypeScript + Vite + Ant Design
