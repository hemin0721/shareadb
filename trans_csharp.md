# ShareADB GUI 应用迁移计划 (C# 版本)

## 项目概述

将 Python CLI 版本的 shareadb 转换为 C# 跨平台桌面应用程序，使用现代 .NET 技术实现友好的用户界面。

## 技术方案

### 框架选择
- **核心框架**: .NET 8.0 (LTS，长期支持版本)
- **开发语言**: C# 12 (最新版本)
- **UI 框架**: 
  - **首选**: Avalonia UI (跨平台，类似 WPF，社区活跃)
  - **备选**: WinUI 3 (仅 Windows，现代化 UI)
  - **选项 3**: MAUI (跨平台，官方支持，相对较新)
- **架构模式**: MVVM (Model-View-ViewModel)
- **依赖注入**: Microsoft.Extensions.DependencyInjection
- **配置管理**: Microsoft.Extensions.Configuration
- **序列化**: System.Text.Json
- **日志**: Serilog 或 Microsoft.Extensions.Logging

### 核心依赖
- **Avalonia UI**: 跨平台 UI 框架
- **ReactiveUI**: 响应式 MVVM 框架（可选）
- **CommunityToolkit.Mvvm**: 轻量级 MVVM 工具库
- **Flurl.Curl**: HTTP 客户端（如需网络功能）
- **Serilog**: 日志记录
- **System.IO.Ports**: 串口通信（如需）
- **Process/Thread**: 系统进程和线程管理

### 方案优势
✅ **真正的跨平台**: 一套代码，支持 Windows/macOS/Linux  
✅ **原生性能**: 编译为原生代码，性能优异  
✅ **强类型系统**: 编译时类型检查，减少运行时错误  
✅ **现代化 UI**: 利用 Avalonia 实现美观、流畅的界面  
✅ **成熟生态**: NuGet 生态丰富，库和工具完善  
✅ **开发效率**: Visual Studio/VS Code 提供优秀的开发体验  
✅ **热重载**: 支持设计时数据绑定和预览  

## 功能需求清单

### 核心功能 (必须实现)
- [ ] **主窗口设计**
  - [ ] 使用 Avalonia 创建主窗口
  - [ ] 响应式布局设计（Grid, StackPanel, DockPanel）
  - [ ] 窗口状态管理（最小化/最大化/关闭）
  - [ ] 系统托盘集成（跨平台托盘支持）

- [ ] **ADB 环境配置**
  - [ ] 自动检测系统中的 ADB（PATH 环境变量）
  - [ ] 手动选择 ADB 可执行文件路径（OpenFileDialog）
  - [ ] 验证 ADB 功能（执行 `adb version` 测试）
  - [ ] 保存 ADB 路径配置（JSON 配置文件）

- [ ] **设备管理** ⭐ 核心功能
  - [ ] 设备自动检测（定期轮询，使用 CancellationTokenSource）
  - [ ] 设备列表组件（DataGrid 或 ListBox，显示序列号、型号、状态）
  - [ ] 设备状态实时更新（使用 INotifyPropertyChanged 或 ReactiveUI）
  - [ ] **新设备自动上线确认**
    - [ ] 检测到新设备时自动显示通知（Toast 或对话框）
    - [ ] 弹出确认对话框询问是否共享（MessageBox 或自定义对话框）
    - [ ] 支持全局设置：自动共享新设备 vs 手动确认
    - [ ] 提供设备预览信息（型号、序列号等）
  - [ ] **共享设备列表管理**
    - [ ] 添加/移除设备到共享列表（ObservableCollection）
    - [ ] 设备白名单/黑名单机制
    - [ ] 右键菜单（ContextMenu 或 Menu）
    - [ ] 批量操作支持（全选、反选、批量共享）
    - [ ] 保存设备共享偏好（JSON 配置文件持久化）
    - [ ] 设备状态标记：
      - 待确认（新设备，等待用户决定）
      - 已共享（正在转发）
      - 已忽略（不共享）
      - 已断开（设备离线但仍保留配置）
  - [ ] 单个设备的启动/停止控制（按钮绑定 Command）

- [ ] **TCP 代理功能**
  - [ ] 为每个设备分配唯一端口（端口池管理）
  - [ ] 端口自动递增（Forward base port: 6000, Proxy base port: 7000）
  - [ ] 使用 System.Net.Sockets.TcpListener 建立 TCP 代理服务器
  - [ ] 支持多客户端连接（异步 socket 编程）
  - [ ] 自动重连机制（设备断开后自动尝试重新连接）
  - [ ] 根据共享列表动态启动/停止代理

- [ ] **网络信息显示**
  - [ ] 自动检测本机所有可用 IP 地址（Dns.GetHostEntry）
  - [ ] 生成分享连接命令（`adb connect <IP>:<PORT>`）
  - [ ] 一键复制连接信息功能（Clipboard.SetText）
  - [ ] 按设备显示对应的连接端口和命令

- [ ] **应用设置**
  - [ ] 监听地址设置（默认 0.0.0.0）
  - [ ] 设备 TCP 端口设置（默认 5555）
  - [ ] Forward 基础端口设置（默认 6000）
  - [ ] Proxy 基础端口设置（默认 7000）
  - [ ] 轮询间隔设置（默认 5 秒）
  - [ ] 状态刷新间隔设置（默认 30 秒）
  - [ ] 新设备自动共享开关（默认 false，需要确认）
  - [ ] 保存/加载配置（JSON 配置文件）

### 增强功能 (可选实现)
- [ ] 系统托盘右键菜单（快速操作）
- [ ] 开机自动启动选项（注册表/LaunchAgents）
- [ ] 桌面通知（跨平台通知）
- [ ] 连接日志查看器（TextBlock + ScrollViewer）
- [ ] 多语言支持（i18n 资源文件）
- [ ] 暗色/亮色主题切换
- [ ] 单个设备的详细信息面板（Expander 或 TabControl）
- [ ] 端口使用情况监控（NetworkInformation）
- [ ] 连接客户端数量统计（连接计数器）
- [ ] 设备截图预览（调用 adb shell screencap，显示图片）
- [ ] 设备命名功能（为设备设置别名）
- [ ] 设备分组（组织多个设备）
- [ ] 应用更新检查（GitHub Releases API）
- [ ] 导出配置/导入配置（JSON 序列化）
- [ ] 统计信息图表（LiveCharts 或 OxyPlot）

## 架构设计

### 项目结构
```
ShareADB/
├── ShareADB.sln                      # 解决方案文件
├── ShareADB.csproj                   # 主项目文件
├── README-CSHARP.md                  # C# 版本文档
├── ├── src/
│   ├── App.axaml                     # 应用程序入口
│   ├── App.axaml.cs                  # 应用程序代码
│   ├── Program.cs                    # 主程序入口
│   ├── Models/                       # 数据模型
│   │   ├── DeviceInfo.cs             # 设备信息模型
│   │   ├── DeviceShareConfig.cs      # 设备共享配置模型
│   │   ├── DeviceShareState.cs       # 设备共享状态枚举
│   │   ├── AppSettings.cs            # 应用设置模型
│   │   └── ConnectionInfo.cs         # 连接信息模型
│   ├── ViewModels/                   # 视图模型
│   │   ├── MainViewModel.cs          # 主窗口视图模型 ⭐
│   │   ├── DeviceListViewModel.cs    # 设备列表视图模型
│   │   ├── DeviceItemViewModel.cs    # 设备项视图模型
│   │   ├── SettingsViewModel.cs      # 设置视图模型
│   │   └── NetworkInfoViewModel.cs   # 网络信息视图模型
│   ├── Views/                        # 视图
│   │   ├── MainWindow.axaml          # 主窗口
│   │   ├── MainWindow.axaml.cs       # 主窗口代码
│   │   ├── Controls/                 # 自定义控件
│   │   │   ├── DeviceList.axaml      # 设备列表控件
│   │   │   ├── DeviceList.axaml.cs
│   │   │   ├── DeviceItem.axaml      # 设备项控件
│   │   │   ├── DeviceItem.axaml.cs
│   │   │   ├── SettingsPanel.axaml   # 设置面板
│   │   │   ├── SettingsPanel.axaml.cs
│   │   │   ├── LogViewer.axaml       # 日志查看器
│   │   │   ├── LogViewer.axaml.cs
│   │   │   ├── NetworkInfo.axaml     # 网络信息显示
│   │   │   └── NetworkInfo.axaml.cs
│   │   └── Windows/                  # 窗口
│   │       ├── NewDeviceDialog.axaml # 新设备对话框 ⭐
│   │       └── NewDeviceDialog.axaml.cs
│   ├── Services/                     # 服务层
│   │   ├── IADBService.cs            # ADB 服务接口
│   │   ├── ADBService.cs             # ADB 服务实现
│   │   ├── IDeviceManager.cs         # 设备管理器接口
│   │   ├── DeviceManager.cs          # 设备管理器实现
│   │   ├── IDeviceListManager.cs     # 设备列表管理器接口 ⭐
│   │   ├── DeviceListManager.cs      # 设备列表管理器实现 ⭐
│   │   ├── ITcpProxyService.cs       # TCP 代理服务接口
│   │   ├── TcpProxyService.cs        # TCP 代理服务实现
│   │   ├── IConfigService.cs         # 配置服务接口
│   │   ├── ConfigService.cs          # 配置服务实现
│   │   ├── INetworkService.cs        # 网络服务接口
│   │   └── NetworkService.cs         # 网络服务实现
│   ├── Core/                         # 核心功能
│   │   ├── TcpProxy/                 # TCP 代理
│   │   │   ├── TcpProxyServer.cs     # TCP 代理服务器
│   │   │   ├── TcpProxyClient.cs     # TCP 代理客户端
│   │   │   └── TcpProxySession.cs    # TCP 代理会话
│   │   └── ADB/                      # ADB 集成
│   │       ├── ADBExecutor.cs        # ADB 命令执行器
│   │       ├── ADBParser.cs          # ADB 输出解析器
│   │       └── ADBDeviceDetector.cs  # ADB 设备检测器
│   ├── Utilities/                    # 工具类
│   │   ├── NetworkHelper.cs          # 网络工具
│   │   ├── PathHelper.cs             # 路径工具
│   │   ├── ProcessHelper.cs          # 进程工具
│   │   └── JsonConvert.cs            # JSON 序列化工具
│   ├── Converters/                   # 值转换器
│   │   ├── DeviceStateConverter.cs   # 设备状态转换器
│   │   ├── DeviceShareStateConverter.cs
│   │   └── BoolToVisibilityConverter.cs
│   ├── Styles/                       # 样式
│   │   ├── AppTheme.axaml            # 主题样式
│   │   └── Colors.axaml              # 颜色定义
│   └── Assets/                       # 资源文件
│       ├── Icons/                    # 图标
│       └── Images/                   # 图片
├── Config/                           # 配置文件
│   ├── appsettings.json              # 应用配置
│   └── device_shares.json            # 设备共享配置
├── Logs/                             # 日志文件
└── Tests/                            # 测试项目
    ├── UnitTests/                    # 单元测试
    └── IntegrationTests/             # 集成测试
```

### 技术架构

#### MVVM 架构
```
View (XAML)
    ↓ 数据绑定
ViewModel (C#)
    ↓ 调用服务
Service (C#)
    ↓ 系统调用
外部系统 (ADB/OS)
```

#### 核心服务设计

##### ADBService
- **职责**: 封装 ADB 命令执行
- **技术**: System.Diagnostics.Process
- **主要方法**:
  - `Task<string> DetectADBPathAsync()` - 检测 ADB 路径
  - `Task<string> ExecuteCommandAsync(string command)` - 执行 ADB 命令
  - `Task<List<DeviceInfo>> ListDevicesAsync()` - 列出设备
  - `Task ForwardPortAsync(string serial, int localPort, int remotePort)` - 端口转发
  - `Task RemoveForwardAsync(string serial, int localPort)` - 移除端口转发
  - `Task<string> ShellCommandAsync(string serial, string command)` - 执行 shell 命令
  - `Task<string> GetDevicePropertyAsync(string serial, string property)` - 获取设备属性

**实现示例**:
```csharp
public class ADBService : IADBService
{
    private readonly string _adbPath;
    private readonly ILogger<ADBService> _logger;

    public ADBService(ILogger<ADBService> logger, IConfigService config)
    {
        _logger = logger;
        _adbPath = config.GetADBPath() ?? "adb";
    }

    public async Task<string> ExecuteCommandAsync(string command, CancellationToken cancellationToken = default)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = _adbPath,
            Arguments = command,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };

        using var process = new Process { StartInfo = startInfo };
        
        var outputBuilder = new StringBuilder();
        process.OutputDataReceived += (sender, args) => outputBuilder.AppendLine(args?.Data);
        process.ErrorDataReceived += (sender, args) => 
        {
            if (!string.IsNullOrEmpty(args?.Data))
                _logger.LogWarning("ADB error: {Error}", args.Data);
        };

        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        
        await process.WaitForExitAsync(cancellationToken);
        
        return outputBuilder.ToString();
    }

    public async Task<List<DeviceInfo>> ListDevicesAsync(CancellationToken cancellationToken = default)
    {
        var output = await ExecuteCommandAsync("devices -l", cancellationToken);
        return ADBParser.ParseDevices(output);
    }
}
```

##### DeviceManager
- **职责**: 管理所有设备的生命周期
- **技术**: Timer 或 CancellationTokenSource 轮询
- **事件驱动**: .NET Events 或 ReactiveUI Observable
- **主要方法**:
  - `Task StartMonitoringAsync(CancellationToken cancellationToken)` - 开始设备监控
  - `Task StopMonitoringAsync()` - 停止设备监控
  - `Task<List<DeviceInfo>> GetDeviceStatusesAsync()` - 获取所有设备状态
  - `Task EnableDeviceAsync(string serial)` - 启用设备共享
  - `Task DisableDeviceAsync(string serial)` - 禁用设备共享
  - `event EventHandler<DeviceDetectedEventArgs> DeviceDetected` - 设备检测事件
  - `event EventHandler<DeviceStatusChangedEventArgs> DeviceStatusChanged` - 设备状态变化事件

**实现示例**:
```csharp
public class DeviceManager : IDeviceManager
{
    private readonly IADBService _adbService;
    private readonly ILogger<DeviceManager> _logger;
    private readonly DeviceListManager _deviceListManager;
    private readonly TcpProxyService _tcpProxyService;
    private CancellationTokenSource? _monitoringCts;
    private readonly Dictionary<string, DeviceInfo> _currentDevices = new();

    public event EventHandler<DeviceDetectedEventArgs>? DeviceDetected;
    public event EventHandler<DeviceStatusChangedEventArgs>? DeviceStatusChanged;

    public async Task StartMonitoringAsync(CancellationToken cancellationToken)
    {
        _monitoringCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        
        while (!_monitoringCts.Token.IsCancellationRequested)
        {
            try
            {
                var devices = await _adbService.ListDevicesAsync(_monitoringCts.Token);
                await ProcessDeviceChangesAsync(devices, _monitoringCts.Token);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Error monitoring devices");
            }

            await Task.Delay(TimeSpan.FromSeconds(5), _monitoringCts.Token);
        }
    }

    private async Task ProcessDeviceChangesAsync(List<DeviceInfo> newDevices, CancellationToken cancellationToken)
    {
        // 检测新设备
        foreach (var newDevice in newDevices)
        {
            if (!_currentDevices.ContainsKey(newDevice.Serial))
            {
                _currentDevices[newDevice.Serial] = newDevice;
                OnDeviceDetected(newDevice);
                
                // 检查是否自动共享
                if (_deviceListManager.ShouldAutoShareNewDevice(newDevice.Serial))
                {
                    await _deviceListManager.AddDeviceToShareListAsync(newDevice.Serial);
                    await _tcpProxyService.StartProxyAsync(newDevice.Serial, cancellationToken);
                }
            }
        }

        // 检测离线设备
        var offlineDevices = _currentDevices.Keys.Except(newDevices.Select(d => d.Serial)).ToList();
        foreach (var serial in offlineDevices)
        {
            var device = _currentDevices[serial];
            _currentDevices.Remove(serial);
            OnDeviceStatusChanged(device, DeviceShareState.Disconnected);
        }
    }
}
```

##### DeviceListManager ⭐
- **职责**: 管理设备列表和共享配置
- **技术**: JSON 配置文件持久化
- **主要方法**:
  - `Task LoadShareConfigAsync()` - 加载设备共享配置
  - `Task SaveShareConfigAsync()` - 保存设备共享配置
  - `Task AddDeviceToShareListAsync(string serial, string? name = null)` - 添加设备到共享列表
  - `Task RemoveDeviceFromShareListAsync(string serial)` - 从共享列表移除设备
  - `DeviceShareConfig? GetDeviceShareConfig(string serial)` - 获取设备共享配置
  - `Task SetDeviceShareStateAsync(string serial, DeviceShareState state)` - 设置设备共享状态
  - `bool ShouldAutoShareNewDevice(string serial)` - 判断是否自动共享新设备
  - `Task MarkDeviceAsIgnoredAsync(string serial)` - 标记设备为忽略
  - `ObservableCollection<DeviceInfo> DeviceShareConfigs` - 设备共享配置集合

**实现示例**:
```csharp
public class DeviceListManager : IDeviceListManager
{
    private readonly IConfigService _configService;
    private readonly ILogger<DeviceListManager> _logger;
    private string _configPath => Path.Combine(AppContext.BaseDirectory, "Config", "device_shares.json");
    
    public ObservableCollection<DeviceShareConfig> Devices { get; } = new();

    public async Task LoadShareConfigAsync()
    {
        if (!File.Exists(_configPath))
        {
            _logger.LogInformation("Device share config not found, creating new one");
            await SaveShareConfigAsync();
            return;
        }

        try
        {
            var json = await File.ReadAllTextAsync(_configPath);
            var config = JsonSerializer.Deserialize<DeviceShareConfigList>(json);
            
            Devices.Clear();
            foreach (var device in config.Devices)
            {
                Devices.Add(device);
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error loading device share config");
        }
    }

    public async Task SaveShareConfigAsync()
    {
        try
        {
            var config = new DeviceShareConfigList
            {
                Devices = Devices.ToList()
            };

            var json = JsonSerializer.Serialize(config, new JsonSerializerOptions
            {
                WriteIndented = true
            });

            var directory = Path.GetDirectoryName(_configPath);
            if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
            {
                Directory.CreateDirectory(directory);
            }

            await File.WriteAllTextAsync(_configPath, json);
            _logger.LogInformation("Device share config saved");
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error saving device share config");
        }
    }

    public async Task AddDeviceToShareListAsync(string serial, string? name = null)
    {
        var existing = Devices.FirstOrDefault(d => d.Serial == serial);
        if (existing != null)
        {
            existing.State = DeviceShareState.Shared;
            existing.LastSeen = DateTime.UtcNow.ToString("o");
        }
        else
        {
            var config = new DeviceShareConfig
            {
                Serial = serial,
                Name = name,
                State = DeviceShareState.Shared,
                LastSeen = DateTime.UtcNow.ToString("o")
            };
            Devices.Add(config);
        }

        await SaveShareConfigAsync();
    }

    public bool ShouldAutoShareNewDevice(string serial)
    {
        var settings = _configService.GetSettings();
        return settings.AutoShareNewDevices;
    }
}
```

##### TcpProxyService
- **职责**: TCP 代理服务器实现
- **技术**: System.Net.Sockets
- **主要方法**:
  - `Task StartProxyAsync(string serial, CancellationToken cancellationToken)` - 启动代理服务器
  - `Task StopProxyAsync(string serial)` - 停止代理服务器
  - `Task HandleClientAsync(TcpClient client,TcpClient target)` - 处理客户端连接
  - `Task ForwardDataAsync(Stream from, Stream to, CancellationToken cancellationToken)` - 转发数据

**实现示例**:
```csharp
public class TcpProxyService : ITcpProxyService
{
    private readonly ILogger<TcpProxyService> _logger;
    private readonly IConfigService _configService;
    private readonly IADBService _adbService;
    private readonly Dictionary<string, TcpProxyServer> _proxies = new();

    public async Task StartProxyAsync(string serial, CancellationToken cancellationToken)
    {
        if (_proxies.ContainsKey(serial))
        {
            _logger.LogWarning("Proxy already running for device {Serial}", serial);
            return;
        }

        var settings = _configService.GetSettings();
        var forwardPort = settings.ForwardBasePort + _proxies.Count;
        var proxyPort = settings.ProxyBasePort + _proxies.Count;

        // 设置 ADB 端口转发
        await _adbService.ForwardPortAsync(serial, forwardPort, settings.DeviceTCPPort);

        // 启动 TCP 代理服务器
        var proxyServer = new TcpProxyServer(
            "127.0.0.1", 
            forwardPort, 
            settings.ListenHost, 
            proxyPort,
            _logger
        );

        await proxyServer.StartAsync(cancellationToken);
        _proxies[serial] = proxyServer;

        _logger.LogInformation("Proxy started for device {Serial}: {ForwardPort} -> {ProxyPort}", 
            serial, forwardPort, proxyPort);
    }

    public async Task StopProxyAsync(string serial)
    {
        if (!_proxies.TryGetValue(serial, out var proxyServer))
        {
            _logger.LogWarning("No proxy found for device {Serial}", serial);
            return;
        }

        await proxyServer.StopAsync();
        _proxies.Remove(serial);

        await _adbService.RemoveForwardAsync(serial, proxyServer.LocalPort);
        
        _logger.LogInformation("Proxy stopped for device {Serial}", serial);
    }
}

public class TcpProxyServer
{
    private readonly string _localHost;
    private readonly int _localPort;
    private readonly string _remoteHost;
    private readonly int _remotePort;
    private readonly ILogger _logger;
    private TcpListener? _listener;
    private CancellationTokenSource? _cts;

    public TcpProxyServer(string localHost, int localPort, string remoteHost, int remotePort, ILogger logger)
    {
        _localHost = localHost;
        _localPort = localPort;
        _remoteHost = remoteHost;
        _remotePort = remotePort;
        _logger = logger;
    }

    public int LocalPort => _localPort;
    public int RemotePort => _remotePort;

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        _cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _listener = new TcpListener(IPAddress.Parse(_localHost), _localPort);
        _listener.Start();

        _logger.LogInformation("TCP proxy listening on {Host}:{Port}", _localHost, _localPort);

        try
        {
            while (!_cts.Token.IsCancellationRequested)
            {
                var client = await _listener.AcceptTcpClientAsync();
                _ = HandleClientAsync(client, _cts.Token);
            }
        }
        finally
        {
            _listener?.Stop();
        }
    }

    private async Task HandleClientAsync(TcpClient client, CancellationToken cancellationToken)
    {
        try
        {
            using var target = new TcpClient();
            await target.ConnectAsync(_remoteHost, _remotePort, cancellationToken);

            var clientStream = client.GetStream();
            var targetStream = target.GetStream();

            var clientToTarget = ForwardDataAsync(clientStream, targetStream, cancellationToken);
            var targetToClient = ForwardDataAsync(targetStream, clientStream, cancellationToken);

            await Task.WhenAll(clientToTarget, targetToClient);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error handling proxy client");
        }
        finally
        {
            client.Close();
        }
    }

    private async Task ForwardDataAsync(Stream from, Stream to, CancellationToken cancellationToken)
    {
        var buffer = new byte[8192];
        int bytesRead;

        while ((bytesRead = await from.ReadAsync(buffer, cancellationToken)) > 0)
        {
            await to.WriteAsync(buffer.AsMemory(0, bytesRead), cancellationToken);
        }
    }

    public async Task StopAsync()
    {
        _cts?.Cancel();
        _listener?.Stop();
        await Task.CompletedTask;
    }
}
```

##### ConfigService
- **职责**: 配置管理
- **技术**: Microsoft.Extensions.Configuration + JSON 文件
- **主要方法**:
  - `AppSettings GetSettings()` - 加载配置
  - `Task SaveSettingsAsync(AppSettings settings)` - 保存配置
  - `void ValidateSettings(AppSettings settings)` - 验证配置
  - `string? GetADBPath()` - 获取 ADB 路径

**实现示例**:
```csharp
public class ConfigService : IConfigService
{
    private readonly ILogger<ConfigService> _logger;
    private readonly IConfiguration _configuration;
    private string _configPath => Path.Combine(AppContext.BaseDirectory, "Config", "appsettings.json");

    public AppSettings Settings { get; private set; }

    public ConfigService(ILogger<ConfigService> logger)
    {
        _logger = logger;
        Settings = LoadDefaultSettings();
        LoadSettings();
    }

    public void LoadSettings()
    {
        try
        {
            if (File.Exists(_configPath))
            {
                var json = File.ReadAllText(_configPath);
                Settings = JsonSerializer.Deserialize<AppSettings>(json) ?? LoadDefaultSettings();
                _logger.LogInformation("Settings loaded from {Path}", _configPath);
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error loading settings, using defaults");
            Settings = LoadDefaultSettings();
        }
    }

    public async Task SaveSettingsAsync()
    {
        try
        {
            var json = JsonSerializer.Serialize(Settings, new JsonSerializerOptions
            {
                WriteIndented = true
            });

            var directory = Path.GetDirectoryName(_configPath);
            if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
            {
                Directory.CreateDirectory(directory);
            }

            await File.WriteAllTextAsync(_configPath, json);
            _logger.LogInformation("Settings saved to {Path}", _configPath);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error saving settings");
        }
    }

    private AppSettings LoadDefaultSettings()
    {
        return new AppSettings
        {
            ADBPath = "adb",
            ListenHost = "0.0.0.0",
            DeviceTCPPort = 5555,
            ForwardBasePort = 6000,
            ProxyBasePort = 7000,
            PollInterval = 5.0,
            StatusInterval = 30.0,
            MinimizeToTray = true,
            StartMinimized = false,
            AutoStart = false,
            AutoShareNewDevices = false,
            ShowNewDeviceDialog = true,
            NewDeviceDialogTimeout = 30
        };
    }

    public void ValidateSettings(AppSettings settings)
    {
        // 验证端口号范围
        if (settings.DeviceTCPPort < 1 || settings.DeviceTCPPort > 65535)
            throw new ArgumentException("Invalid DeviceTCPPort");

        if (settings.ForwardBasePort < 1 || settings.ForwardBasePort > 65535)
            throw new ArgumentException("Invalid ForwardBasePort");

        if (settings.ProxyBasePort < 1 || settings.ProxyBasePort > 65535)
            throw new ArgumentException("Invalid ProxyBasePort");

        // 验证间隔
        if (settings.PollInterval < 1.0)
            throw new ArgumentException("Invalid PollInterval");

        if (settings.StatusInterval < 1.0)
            throw new ArgumentException("Invalid StatusInterval");
    }
}
```

### 数据模型设计

#### C# 类型定义

##### DeviceShareState 枚举
```csharp
public enum DeviceShareState
{
    Pending,        // 待确认（新设备）
    Shared,         // 已共享
    Ignored,        // 已忽略
    Disconnected    // 已断开
}
```

##### DeviceInfo 模型
```csharp
public class DeviceInfo
{
    public string Serial { get; set; } = string.Empty;
    public string State { get; set; } = string.Empty; // 'device' | 'offline' | 'unauthorized' | 'unknown'
    public string? Product { get; set; }
    public string? Model { get; set; }
    public string? Device { get; set; }
    public string? TransportId { get; set; }

    [JsonIgnore]
    public bool IsOnline => State == "device";
}
```

##### DeviceShareConfig 模型
```csharp
public class DeviceShareConfig : INotifyPropertyChanged
{
    private string _serial = string.Empty;
    private string? _name;
    private DeviceShareState _state = DeviceShareState.Pending;
    private int? _forwardPort;
    private int? _proxyPort;
    private string? _lastSeen;

    public string Serial
    {
        get => _serial;
        set
        {
            if (_serial != value)
            {
                _serial = value;
                OnPropertyChanged();
            }
        }
    }

    public string? Name
    {
        get => _name;
        set
        {
            if (_name != value)
            {
                _name = value;
                OnPropertyChanged();
            }
        }
    }

    public DeviceShareState State
    {
        get => _state;
        set
        {
            if (_state != value)
            {
                _state = value;
                OnPropertyChanged();
            }
        }
    }

    public int? ForwardPort
    {
        get => _forwardPort;
        set
        {
            if (_forwardPort != value)
            {
                _forwardPort = value;
                OnPropertyChanged();
            }
        }
    }

    public int? ProxyPort
    {
        get => _proxyPort;
        set
        {
            if (_proxyPort != value)
            {
                _proxyPort = value;
                OnPropertyChanged();
            }
        }
    }

    public string? LastSeen
    {
        get => _lastSeen;
        set
        {
            if (_lastSeen != value)
            {
                _lastSeen = value;
                OnPropertyChanged();
            }
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    protected virtual void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
```

##### AppSettings 模型
```csharp
public class AppSettings : INotifyPropertyChanged
{
    private string _adbPath = "adb";
    private string _listenHost = "0.0.0.0";
    private int _deviceTCPPort = 5555;
    private int _forwardBasePort = 6000;
    private int _proxyBasePort = 7000;
    private double _pollInterval = 5.0;
    private double _statusInterval = 30.0;
    private bool _minimizeToTray = true;
    private bool _startMinimized = false;
    private bool _autoStart = false;
    private bool _autoShareNewDevices = false;
    private bool _showNewDeviceDialog = true;
    private int _newDeviceDialogTimeout = 30;

    public string ADBPath
    {
        get => _adbPath;
        set { _adbPath = value; OnPropertyChanged(); }
    }

    public string ListenHost
    {
        get => _listenHost;
        set { _listenHost = value; OnPropertyChanged(); }
    }

    public int DeviceTCPPort
    {
        get => _deviceTCPPort;
        set { _deviceTCPPort = value; OnPropertyChanged(); }
    }

    public int ForwardBasePort
    {
        get => _forwardBasePort;
        set { _forwardBasePort = value; OnPropertyChanged(); }
    }

    public int ProxyBasePort
    {
        get => _proxyBasePort;
        set { _proxyBasePort = value; OnPropertyChanged(); }
    }

    public double PollInterval
    {
        get => _pollInterval;
        set { _pollInterval = value; OnPropertyChanged(); }
    }

    public double StatusInterval
    {
        get => _statusInterval;
        set { _statusInterval = value; OnPropertyChanged(); }
    }

    public bool MinimizeToTray
    {
        get => _minimizeToTray;
        set { _minimizeToTray = value; OnPropertyChanged(); }
    }

    public bool StartMinimized
    {
        get => _startMinimized;
        set { _startMinimized = value; OnPropertyChanged(); }
    }

    public bool AutoStart
    {
        get => _autoStart;
        set { _autoStart = value; OnPropertyChanged(); }
    }

    public bool AutoShareNewDevices
    {
        get => _autoShareNewDevices;
        set { _autoShareNewDevices = value; OnPropertyChanged(); }
    }

    public bool ShowNewDeviceDialog
    {
        get => _showNewDeviceDialog;
        set { _showNewDeviceDialog = value; OnPropertyChanged(); }
    }

    public int NewDeviceDialogTimeout
    {
        get => _newDeviceDialogTimeout;
        set { _newDeviceDialogTimeout = value; OnPropertyChanged(); }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    protected virtual void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
```

### UI 组件设计

#### 主窗口 XAML (MainWindow.axaml)
```xaml
<Window xmlns="https://github.com/avaloniaui"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        xmlns:d="http://schemas.microsoft.com/expression/blend/2008"
        xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
        xmlns:vm="using:ShareADB.ViewModels"
        xmlns:controls="using:ShareADB.Views.Controls"
        mc:Ignorable="d" d:DesignWidth="1200" d:DesignHeight="800"
        x:Class="ShareADB.Views.MainWindow"
        x:DataType="vm:MainViewModel"
        Title="ShareADB" Width="1200" Height="800" MinWidth="800" MinHeight="600"
        Icon="/Assets/Icons/icon.png">
    
    <Design.DataContext>
        <vm:MainViewModel/>
    </Design.DataContext>

    <Grid>
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <!-- 顶部工具栏 -->
        <Border Grid.Row="0" Background="{DynamicResource SystemAccentColor}" Padding="16,8">
            <DockPanel>
                <TextBlock Text="ShareADB" FontSize="20" FontWeight="Bold" Foreground="White"
                           VerticalAlignment="Center" DockPanel.Dock="Left"/>
                
                <StackPanel Orientation="Horizontal" DockPanel.Dock="Right">
                    <Button Classes="ToolBarButton" Command="{Binding ShowSettingsCommand}">
                        <StackPanel Orientation="Horizontal">
                            <TextBlock Text="⚙️" FontSize="16"/>
                            <TextBlock Text="设置" Margin="8,0,0,0"/>
                        </StackPanel>
                    </Button>
                    <Button Classes="ToolBarButton" Command="{Binding MinimizeCommand}">
                        <TextBlock Text="─" FontSize="16"/>
                    </Button>
                </StackPanel>
            </DockPanel>
        </Border>

        <!-- 主内容区域 -->
        <Grid Grid.Row="1">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="3*" MinWidth="400"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="2*" MinWidth="300"/>
            </Grid.ColumnDefinitions>

            <!-- 左侧：设备列表 -->
            <controls:DeviceList Grid.Column="0" 
                               Devices="{Binding Devices}"
                               AddToShareCommand="{Binding AddToShareCommand}"
                               RemoveFromShareCommand="{Binding RemoveFromShareCommand}"
                               StartProxyCommand="{Binding StartProxyCommand}"
                               StopProxyCommand="{Binding StopProxyCommand}"/>

            <!-- 分隔线 -->
            <Border Grid.Column="1" Width="1" Background="{DynamicResource SystemChromeLowColor}" Margin="8,0"/>

            <!-- 右侧：网络信息 -->
            <controls:NetworkInfo Grid.Column="2" 
                                 ConnectionInfos="{Binding ConnectionInfos}"/>
        </Grid>

        <!-- 底部状态栏 -->
        <Border Grid.Row="2" Background="{DynamicResource SystemChromeLowColor}" Padding="16,8">
            <DockPanel>
                <TextBlock Text="{Binding StatusMessage}" Foreground="{DynamicResource SystemChromeGrayTextBrush}"
                           VerticalAlignment="Center" DockPanel.Dock="Left"/>
                <TextBlock Text="{Binding DeviceCount, StringFormat='设备: {0}'}" 
                           HorizontalAlignment="Right"/>
            </DockPanel>
        </Border>
    </Grid>
</Window>
```

#### 设备列表控件 XAML (DeviceList.axaml)
```xaml
<UserControl xmlns="https://github.com/avaloniaui"
             xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
             xmlns:d="http://schemas.microsoft.com/expression/blend/2008"
             xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
             xmlns:vm="using:ShareADB.ViewModels"
             mc:Ignorable="d" d:DesignWidth="800" d:DesignHeight="600"
             x:Class="ShareADB.Views.Controls.DeviceList"
             x:DataType="vm:DeviceListViewModel">
    
    <Grid>
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <!-- 设备过滤器 -->
        <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="8" Spacing="8">
            <TextBox Watermark="搜索设备..." Width="200" Text="{Binding SearchText, Mode=TwoWay}"/>
            <CheckBox Content="仅显示已共享" IsChecked="{Binding ShowOnlyShared, Mode=TwoWay}"/>
            <Button Content="刷新" Command="{Binding RefreshCommand}"/>
        </StackPanel>

        <!-- 设备列表 -->
        <ListBox Grid.Row="1" 
                 ItemsSource="{Binding FilteredDevices}"
                 SelectedItem="{Binding SelectedDevice}"
                 Margin="8">
            <ListBox.ItemTemplate>
                <DataTemplate>
                    <Grid Margin="8">
                        <Grid.RowDefinitions>
                            <RowDefinition Height="Auto"/>
                            <RowDefinition Height="Auto"/>
                        </Grid.RowDefinitions>

                        <!-- 设备基本信息 -->
                        <DockPanel Grid.Row="0">
                            <StackPanel Orientation="Horizontal" DockPanel.Dock="Right" Spacing="8">
                                <!-- 分享状态指示器 -->
                                <Ellipse Width="12" Height="12" Fill="{DynamicResource SystemAccentColor}">
                                    <Ellipse.Styles>
                                        <Style Selector="Ellipse[data-state='Pending']">
                                            <Setter Property="Fill" Value="Yellow"/>
                                        </Style>
                                        <Style Selector="Ellipse[data-state='Shared']">
                                            <Setter Property="Fill" Value="Green"/>
                                        </Style>
                                        <Style Selector="Ellipse[data-state='Ignored']">
                                            <Setter Property="Fill" Value="Gray"/>
                                        </Style>
                                    </Ellipse.Styles>
                                    <Ellipse.DataState>Shared</Ellipse.DataState>
                                </Ellipse>

                                <Button Content="分享" Command="{Binding $parent[UserControl].(vm:DeviceListViewModel.AddToShareCommand)}" 
                                        CommandParameter="{Binding}" IsVisible="{Binding CanShare}"/>
                                <Button Content="停止" Command="{Binding $parent[UserControl].(vm:DeviceListViewModel.StopProxyCommand)}" 
                                        CommandParameter="{Binding}" IsVisible="{Binding CanStop}"/>
                            </StackPanel>

                            <StackPanel Orientation="Vertical">
                                <TextBlock Text="{Binding Name ?? Serial}" FontWeight="SemiBold" FontSize="14"/>
                                <TextBlock Text="{Binding Serial}" Foreground="Gray" FontSize="12"/>
                            </StackPanel>
                        </DockPanel>

                        <!-- 设备详情 -->
                        <StackPanel Grid.Row="1" Orientation="Horizontal" Spacing="16" Margin="0,4,0,0">
                            <StackPanel Orientation="Horizontal">
                                <TextBlock Text="型号: " Foreground="Gray"/>
                                <TextBlock Text="{Binding Model ?? '未知'}"/>
                            </StackPanel>
                            <StackPanel Orientation="Horizontal">
                                <TextBlock Text="状态: " Foreground="Gray"/>
                                <TextBlock Text="{Binding State}"/>
                            </StackPanel>
                            <StackPanel Orientation="Horizontal" IsVisible="{Binding IsShared}">
                                <TextBlock Text="端口: " Foreground="Gray"/>
                                <TextBlock Text="{Binding ProxyPort, StringFormat='{}{0}'}"/>
                            </StackPanel>
                        </StackPanel>
                    </Grid>
                </DataTemplate>
            </ListBox.ItemTemplate>
            
            <!-- 右键菜单 -->
            <ListBox.ContextMenu>
                <ContextMenu>
                    <MenuItem Header="分享" Command="{Binding $parent[UserControl].(vm:DeviceListViewModel.AddToShareCommand)}"
                             CommandParameter="{Binding SelectedDevice}"/>
                    <MenuItem Header="撤消分享" Command="{Binding $parent[UserControl].(vm:DeviceListViewModel.RemoveFromShareCommand)}"
                             CommandParameter="{Binding SelectedDevice}"/>
                    <Separator/>
                    <MenuItem Header="复制序列号" Command="{Binding CopySerialCommand}"/>
                    <MenuItem Header="忽略此设备" Command="{Binding IgnoreDeviceCommand}"/>
                </ContextMenu>
            </ListBox.ContextMenu>
        </ListBox>

        <!-- 批量操作 -->
        <StackPanel Grid.Row="2" Orientation="Horizontal" Margin="8" Spacing="8" HorizontalAlignment="Right">
            <Button Content="全选" Command="{Binding SelectAllCommand}"/>
            <Button Content="批量分享" Command="{Binding BatchShareCommand}"/>
            <Button Content="批量忽略" Command="{Binding BatchIgnoreCommand}"/>
        </StackPanel>
    </Grid>
</UserControl>
```

#### MainViewModel 示例
```csharp
using System.Collections.ObjectModel;
using System.Reactive;
using System.Threading.Tasks;
using System.Windows.Input;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using ShareADB.Models;
using ShareADB.Services;

namespace ShareADB.ViewModels
{
    public partial class MainViewModel : ObservableObject
    {
        private readonly IDeviceManager _deviceManager;
        private readonly IConfigService _configService;
        private readonly ILogger<MainViewModel> _logger;
        private CancellationTokenSource? _monitoringCts;

        [ObservableProperty]
        private ObservableCollection<DeviceShareConfig> _devices = new();

        [ObservableProperty]
        private ObservableCollection<ConnectionInfo> _connectionInfos = new();

        [ObservableProperty]
        private string _statusMessage = "就绪";

        [ObservableProperty]
        private int _deviceCount;

        public MainViewModel(
            IDeviceManager deviceManager,
            IConfigService configService,
            ILogger<MainViewModel> logger)
        {
            _deviceManager = deviceManager;
            _configService = configService;
            _logger = logger;

            // 订阅设备事件
            _deviceManager.DeviceDetected += OnDeviceDetected;
            _deviceManager.DeviceStatusChanged += OnDeviceStatusChanged;

            // 初始化命令
            InitializeCommands();
        }

        [RelayCommand]
        private async Task ShowSettingsAsync()
        {
            // 显示设置窗口逻辑
            _logger.LogInformation("Opening settings window");
            await Task.CompletedTask;
        }

        [RelayCommand]
        private void Minimize()
        {
            // 最小化到托盘逻辑
            _logger.LogInformation("Minimizing to tray");
        }

        [RelayCommand]
        private async Task AddToShareAsync(DeviceShareConfig? device)
        {
            if (device == null) return;

            try
            {
                device.State = DeviceShareState.Shared;
                await _configService.SaveSettingsAsync();
                StatusMessage = $"设备 {device.Name ?? device.Serial} 已添加到共享列表";
                _logger.LogInformation("Device {Serial} added to share list", device.Serial);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to add device to share list");
                StatusMessage = "添加失败";
            }
        }

        [RelayCommand]
        private async Task RemoveFromShareAsync(DeviceShareConfig? device)
        {
            if (device == null) return;

            try
            {
                device.State = DeviceShareState.Ignored;
                await _configService.SaveSettingsAsync();
                StatusMessage = $"设备 {device.Name ?? device.Serial} 已从共享列表移除";
                _logger.LogInformation("Device {Serial} removed from share list", device.Serial);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to remove device from share list");
                StatusMessage = "移除失败";
            }
        }

        [RelayCommand]
        private async Task StartProxyAsync(DeviceShareConfig? device)
        {
            if (device == null) return;

            try
            {
                // 启动 TCP 代理逻辑
                await Task.Delay(100); // 模拟异步操作
                StatusMessage = $"设备 {device.Name ?? device.Serial} 代理已启动";
                _logger.LogInformation("Proxy started for device {Serial}", device.Serial);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to start proxy for device");
                StatusMessage = "启动失败";
            }
        }

        [RelayCommand]
        private async Task StopProxyAsync(DeviceShareConfig? device)
        {
            if (device == null) return;

            try
            {
                // 停止 TCP 代理逻辑
                await Task.Delay(100); // 模拟异步操作
                StatusMessage = $"设备 {device.Name ?? device.Serial} 代理已停止";
                _logger.LogInformation("Proxy stopped for device {Serial}", device.Serial);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to stop proxy for device");
                StatusMessage = "停止失败";
            }
        }

        private void OnDeviceDetected(object? sender, DeviceDetectedEventArgs e)
        {
            Dispatcher.UIThread.Post(() =>
            {
                var existing = Devices.FirstOrDefault(d => d.Serial == e.Device.Serial);
                if (existing == null)
                {
                    var newDevice = new DeviceShareConfig
                    {
                        Serial = e.Device.Serial,
                        Name = e.Device.Model,
                        State = DeviceShareState.Pending,
                        LastSeen = DateTime.UtcNow.ToString("o")
                    };
                    Devices.Add(newDevice);
                    StatusMessage = $"检测到新设备: {e.Device.Model ?? e.Device.Serial}";
                }
                DeviceCount = Devices.Count;
            });
        }

        private void OnDeviceStatusChanged(object? sender, DeviceStatusChangedEventArgs e)
        {
            Dispatcher.UIThread.Post(() =>
            {
                var device = Devices.FirstOrDefault(d => d.Serial == e.Device.Serial);
                if (device != null)
                {
                    device.State = e.NewState;
                    device.LastSeen = DateTime.UtcNow.ToString("o");
                }
                DeviceCount = Devices.Count;
            });
        }

        private void InitializeCommands()
        {
            // 初始化其他命令
        }

        public async Task InitializeAsync()
        {
            try
            {
                // 加载配置
                var settings = _configService.GetSettings();
                
                // 开始监控设备
                _monitoringCts = new CancellationTokenSource();
                await _deviceManager.StartMonitoringAsync(_monitoringCts.Token);
                
                StatusMessage = "设备监控已启动";
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to initialize");
                StatusMessage = "初始化失败";
            }
        }

        public Task ShutdownAsync()
        {
            try
            {
                _monitoringCts?.Cancel();
                _deviceManager.DeviceDetected -= OnDeviceDetected;
                _deviceManager.DeviceStatusChanged -= OnDeviceStatusChanged;
                return Task.CompletedTask;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to shutdown");
                return Task.CompletedTask;
            }
        }
    }
}
```

### 依赖注入配置

#### App.axaml.cs (依赖注入设置)
```csharp
using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Serilog;
using ShareADB.Services;
using ShareADB.ViewModels;
using ShareADB.Views;

namespace ShareADB
{
    public class App : Application
    {
        public override void Initialize()
        {
            AvaloniaXamlLoader.Load(this);
        }

        public override void OnFrameworkInitializationCompleted()
        {
            // 配置服务提供者
            var services = ConfigureServices();

            if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
            {
                var mainWindow = services.GetRequiredService<MainWindow>();
                mainWindow.DataContext = services.GetRequiredService<MainViewModel>();
                desktop.MainWindow = mainWindow;
                
                // 初始化应用
                var mainViewModel = (MainViewModel)mainWindow.DataContext;
                _ = Task.Run(() => mainViewModel.InitializeAsync());
            }

            base.OnFrameworkInitializationCompleted();
        }

        private IServiceProvider ConfigureServices()
        {
            // 配置日志
            Log.Logger = new LoggerConfiguration()
                .MinimumLevel.Debug()
                .WriteTo.Console()
                .WriteTo.File("Logs/shareadb-.txt", rollingInterval: RollingInterval.Day)
                .CreateLogger();

            var serviceProvider = new ServiceCollection()
                // 添加日志
                .AddLogging(builder => builder.AddSerilog())
                
                // 添加服务
                .AddSingleton<IConfigService, ConfigService>()
                .AddSingleton<IADBService, ADBService>()
                .AddSingleton<IDeviceManager, DeviceManager>()
                .AddSingleton<IDeviceListManager, DeviceListManager>()
                .AddSingleton<ITcpProxyService, TcpProxyService>()
                .AddSingleton<INetworkService, NetworkService>()
                
                // 添加视图模型
                .AddTransient<MainViewModel>()
                .AddTransient<DeviceListViewModel>()
                .AddTransient<SettingsViewModel>()
                .AddTransient<NetworkInfoViewModel>()
                
                // 添加视图
                .AddTransient<MainWindow>()
                .AddTransient<NewDeviceDialog>()
                
                .BuildServiceProvider();

            return serviceProvider;
        }
    }
}
```

## 实现步骤

### 阶段 1: 项目初始化 (Day 1-2)
1. 使用 .NET CLI 创建 Avalonia 项目: `dotnet new avalonia.app -n ShareADB`
2. 安装必要 NuGet 包:
   - `Avalonia`
   - `CommunityToolkit.Mvvm`
   - `Microsoft.Extensions.DependencyInjection`
   - `Microsoft.Extensions.Configuration`
   - `Microsoft.Extensions.Logging`
   - `Serilog`
   - `Serilog.Sinks.Console`
   - `Serilog.Sinks.File`
3. 配置项目结构（Models, ViewModels, Views, Services）
4. 设计主窗口布局和路由
5. 配置依赖注入容器

### 阶段 2: ADB 集成 (Day 2-3)
1. 实现 ADBService 服务类
2. 实现自动检测 ADB 功能
3. 实现 ADB 命令执行封装
4. 实现 ADB 设备列表获取
5. 实现 ADB 输出解析器
6. 单元测试 ADB 命令执行

### 阶段 3: 设备管理（核心） ⭐ (Day 3-5)
1. 实现 DeviceManager 服务类
2. **实现设备轮询检测**
3. **实现 DeviceListManager 服务**
4. **实现新设备对话框窗口**
5. **实现设备列表 XAML 控件**
6. **实现设备右键菜单**
7. **实现批量操作功能**
8. **实现设备状态实时更新**

### 阶段 4: TCP 代理 (Day 5-7)
1. 实现 TcpProxyService 服务类
2. 实现 TcpProxyServer 类
3. 实现 TCP 代理服务器逻辑
4. 实现端口分配管理
5. 实现多客户端连接支持
6. 实现数据转发逻辑
7. 性能测试和优化

### 阶段 5: 网络和配置 (Day 7-8)
1. 实现本机 IP 检测工具
2. 实现连接信息生成和显示组件
3. 实现 ConfigService
4. 实现设置界面 XAML
5. 实现设备共享配置持久化 ⭐

### 阶段 6: UI 优化和主题 (Day 8-9)
1. 实现暗色/亮色主题切换
2. 优化控件样式和交互
3. 添加过渡动画
4. 实现响应式布局
5. 系统托盘集成

### 阶段 7: 测试和调试 (Day 9-10)
1. 单元测试（xUnit）
2. 集成测试
3. 跨平台测试（Windows、macOS、Linux）
4. 单设备和多设备场景测试
5. 设备断开重连测试
6. **新设备自动上线和确认流程测试** ⭐
7. **设备添加/移除功能测试** ⭐
8. 性能测试和内存泄漏检查

### 阶段 8: 打包和发布 (Day 10-11)
1. 配置项目发布设置
2. 生成 Windows/macOS/Linux 可执行文件
3. 配置应用图标和元数据
4. 编写 README 和使用文档
5. 配置自动更新（可选）

## 关键技术点

### 1. 数据绑定
```csharp
// ViewModel 中的属性
[ObservableProperty]
private string _statusMessage = "就绪";

// XAML 中绑定
<TextBlock Text="{Binding StatusMessage}"/>
```

### 2. 命令绑定
```csharp
// ViewModel 中的命令
[RelayCommand]
private async Task ShowSettingsAsync()
{
    // 命令逻辑
}

// XAML 中绑定
<Button Content="设置" Command="{Binding ShowSettingsCommand}"/>
```

### 3. 进程管理
```csharp
// 使用 System.Diagnostics.Process
var startInfo = new ProcessStartInfo
{
    FileName = "adb",
    Arguments = "devices",
    RedirectStandardOutput = true,
    UseShellExecute = false,
    CreateNoWindow = true
};

using var process = new Process { StartInfo = startInfo };
process.Start();
await process.WaitForExitAsync();
```

### 4. TCP 服务器
```csharp
// 使用 System.Net.Sockets
var listener = new TcpListener(IPAddress.Parse("0.0.0.0"), 5555);
listener.Start();

while (true)
{
    var client = await listener.AcceptTcpClientAsync();
    _ = HandleClientAsync(client);
}
```

### 5. 持久化存储
```csharp
// 使用 System.Text.Json
var json = JsonSerializer.Serialize(settings, new JsonSerializerOptions
{
    WriteIndented = true
});

await File.WriteAllTextAsync(configPath, json);
```

### 6. 状态管理
- 使用 INotifyPropertyChanged（CommunityToolkit.Mvvm 提供源生成器）
- 使用 ObservableCollection 集合
- 通过数据绑定同步 UI

### 7. 跨平台兼容
- 路径处理: 使用 `Path.Combine()`
- 进程执行: 适配不同平台的命令路径
- 文件系统: 使用 .NET 跨平台 API

### 8. 异步编程
- 使用 async/await 模式
- 使用 CancellationToken 取消长时间运行的操作
- 避免死锁和线程池饥饿

### 9. 性能优化
- 虚拟化（VirtualizingStackPanel）
- 节流/防抖（RxThrottle）
- 内存管理（及时清理资源，使用 using 语句）

### 10. 日志记录
```csharp
// 使用 Microsoft.Extensions.Logging
_logger.LogInformation("Device {Serial} connected", serial);
_logger.LogError(ex, "Failed to start proxy");
```

## 配置文件

### appsettings.json
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

- **IDE**: 
  - Visual Studio 2022 (Windows，功能最全)
  - JetBrains Rider (跨平台，性能优异)
  - Visual Studio Code (轻量级，插件丰富)
- **调试**: Visual Studio/Rider 调试器
- **测试**: xUnit (单元测试) + SpecFlow (BDD 测试)
- **构建**: MSBuild (.NET 内置)
- **打包**: dotnet publish

## 待确认事项

1. **UI 框架**: 
   - Avalonia UI (推荐，真正的跨平台)
   - WinUI 3 (仅 Windows)
   - MAUI (跨平台，官方支持)

2. **MVVM 框架**:
   - CommunityToolkit.Mvvm (推荐，轻量，源生成器)
   - ReactiveUI (响应式编程)
   - Prism (功能强大，较重)

3. **日志框架**:
   - Serilog (推荐，结构化日志)
   - NLog (传统，稳定)
   - Microsoft.Extensions.Logging (官方)

4. **打包方式**:
   - 单文件发布 (dotnet publish -p:PublishSingleFile=true)
   - 自包含发布 (包含运行时)
   - 依赖框架发布 (需要安装 .NET 运行时)

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

- [.NET 官方文档](https://docs.microsoft.com/dotnet/)
- [Avalonia UI 官方文档](https://docs.avaloniaui.net/)
- [CommunityToolkit.Mvvm 文档](https://learn.microsoft.com/dotnet/communitytoolkit/mvvm/)
- [Microsoft.Extensions.DependencyInjection](https://docs.microsoft.com/dotnet/core/extensions/dependency-injection)
- [System.Text.Json 文档](https://docs.microsoft.com/dotnet/standard/serialization/system-text-json-overview)
- [Serilog 文档](https://serilog.net/)
- [.NET 异步编程最佳实践](https://docs.microsoft.com/dotnet/csharp/async)
- [Avalonia 示例项目](https://github.com/AvaloniaUI/Avalonia.Samples)

---
**创建时间**: 2026-01-16
**最后更新**: 2026-01-16
**版本**: v3.0 (从 Python 迁移到 C#)
**技术栈**: .NET 8.0 + C# 12 + Avalonia UI + CommunityToolkit.Mvvm + Serilog
