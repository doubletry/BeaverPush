# BeaverPush — 河狸推流（多路 RTSP 推流客户端）

[English](README.md)

基于 **PySide6 + MVC 架构** 的 Windows 多路 RTSP 推流桌面客户端。

## 功能特性

- 🎥 **5 种视频源：** 本地视频、摄像头、RTSP 拉流转推、屏幕捕获、窗口捕获
- 📡 **多路同时推流**，每路独立控制启停
- 🎨 **Catppuccin Mocha** 暗色主题
- 🔧 **编码参数可配：** 编码器（h264/h265/NVENC）、分辨率、帧率、码率
- 💾 **配置自动持久化**（JSON 格式）
- 👁️ **ffplay 实时预览**
- 🖥️ **系统托盘** 最小化
- 🔒 **服务器地址锁定**，防止误修改
- 🔄 **本地视频循环播放**
- 🖱️ **可编辑通道名称**，点击标题即可修改
- 🔑 **自动检测客户端 ID**，使用主板 UUID
- ✅ **输入校验**，客户端 ID 和流名称仅允许 ASCII 安全字符
- 🔄 **自动重连**，RTSP 源支持可配置的重连间隔
- 💾 **连接测试成功后自动保存配置**

## 下载安装

从 [GitHub Releases](https://github.com/doubletry/BeaverPush/releases) 下载最新安装包。安装包已内置 FFmpeg，无需额外配置。

## 开发环境搭建

### 前置依赖

- **Python** ≥ 3.12
- **FFmpeg** / **ffprobe** / **ffplay** 在 `PATH` 中（或放置在 `ffmpeg/` 子目录下）
- **uv** 包管理器

### 安装与运行

```bash
# 安装依赖
uv sync

# 运行应用
uv run beaverpush
# 或
uv run python -m beaverpush.main
```

### 运行测试

```bash
uv run pytest
```

### 从源码构建

构建独立可执行文件和 Windows 安装包：

```powershell
# 编译 + 打包安装程序（需要安装 Inno Setup 6）
.\build.ps1 -Version "1.0.0"
```

构建脚本使用 **Nuitka** 编译独立可执行文件（`dist/main.dist/BeaverPush.exe`），并通过 **Inno Setup** 生成安装包（`dist/BeaverPushSetup.exe`）。

## 使用说明

1. 填写 RTSP 服务器地址（如 `rtsp://192.168.1.100:8554`）
2. 设置客户端 ID，用于区分不同推流端（留空时自动使用主板 UUID）
3. 点击 **添加通道** 创建推流通道
4. 选择视频源类型并配置参数
5. 点击 **开始推流**（流名称留空时自动分配 `stream1`、`stream2` 等）

### 视频源类型

| 类型 | 说明 |
|------|------|
| 本地视频 | 推送视频文件（支持循环播放） |
| 摄像头 | 通过 DirectShow 捕获本地摄像头 |
| RTSP 拉流 | 从 RTSP 源拉流并转推 |
| 屏幕捕获 | 捕获显示器/屏幕区域 |
| 窗口捕获 | 捕获指定应用程序窗口 |

### 高级设置

在通道卡片上切换 **高级模式** 可配置：
- **编码器：** libx264、h264_nvenc、hevc_nvenc、copy
- **分辨率：** 宽 × 高（自动调整为偶数）
- **帧率** 和 **码率**（单位固定为 `M`；码率留空则不限制）

## 项目结构

```
src/beaverpush/
├── main.py                      # 应用入口
├── models/
│   ├── config.py                # JSON 配置持久化 (AppConfig, StreamConfig)
│   └── stream_model.py          # StreamState 推流状态枚举
├── views/
│   ├── theme.py                 # Catppuccin Mocha 主题 + QSS
│   ├── stream_card.py           # 推流通道卡片组件
│   └── main_window.py           # 主窗口（工具栏 + 滚动卡片列表）
├── controllers/
│   ├── app_controller.py        # 应用生命周期、配置管理、设备枚举
│   └── stream_controller.py     # 单路推流 FFmpeg 生命周期管理
└── services/
    ├── device_service.py        # 设备枚举（摄像头/屏幕/窗口）
    ├── ffmpeg_service.py        # FFmpeg 进程管理 + 命令构建
    ├── ffmpeg_path.py           # FFmpeg 可执行文件路径解析
    ├── log_service.py           # 基于 Loguru 的日志服务
    └── window_capture.py        # Win32 窗口/屏幕捕获 (PrintWindow/BitBlt)
```

## 架构

```
┌───────────────────────────────────────────────┐
│                     Views                      │
│  MainWindow ◄──── StreamCardView (×N)          │
│  (Qt 信号)        (Qt 信号)                     │
└──────┬──────────────────┬──────────────────────┘
       │                  │
       ▼                  ▼
┌──────────────┐  ┌─────────────────┐
│AppController │  │StreamController │  ← Controllers
│ (全局管理)    │  │ (单路推流)       │
└──────┬───────┘  └────────┬────────┘
       │                   │
       ▼                   ▼
┌───────────────────────────────────────────────┐
│              Models + Services                 │
│  config · stream_model · device_service        │
│  ffmpeg_service · ffmpeg_path · window_capture │
└───────────────────────────────────────────────┘
```

- **Views** — 仅负责 UI 展示，通过 Qt 信号通知用户操作
- **Controllers** — 连接信号、调用 Services、通过 `set_*` 方法更新视图
- **Services** — 封装纯业务逻辑（FFmpeg 进程、设备枚举、窗口捕获）
- **Models** — 定义数据结构和持久化

## CI/CD

推送版本标签（如 `v1.0.0`）时自动触发 GitHub Actions 构建流程：

1. 配置 Python 3.12 + uv 环境
2. 下载 FFmpeg 二进制文件
3. 安装 Inno Setup 6
4. 使用 Nuitka 编译并打包安装程序
5. 执行静默安装验证测试

## 许可协议

[MIT](LICENSE)
