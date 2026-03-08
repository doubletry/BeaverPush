# PushClient — RTSP 推流客户端

基于 **PySide6 QWidgets + MVC 架构** 的多路 RTSP 推流桌面客户端。

## 功能

- 🎥 支持 5 种视频源：本地视频、摄像头、RTSP 拉流、屏幕捕获、窗口捕获
- 📡 多路同时推流，每路独立控制启停
- 🎨 Catppuccin Mocha 暗色主题
- 🔧 编码参数可配（编码器、分辨率、帧率、码率）
- 💾 配置自动持久化（JSON）
- 👁️ 可选 ffplay 实时预览
- 🖥️ 系统托盘最小化

## 前置依赖

- **Python** ≥ 3.12
- **FFmpeg** / **ffprobe** / **ffplay** 在 `PATH` 中
- **Poetry** 包管理器

## 安装与运行

```bash
# 安装依赖
poetry install

# 运行
poetry run push-client
# 或
poetry run python -m push_client.main
```

## 项目结构

```
src/push_client/
├── main.py                     # 应用入口
├── models/                     # 数据层
│   ├── config.py               #   配置持久化 (JSON)
│   └── stream_model.py         #   推流状态枚举
├── views/                      # 视图层 (QWidgets)
│   ├── theme.py                #   Catppuccin Mocha 主题 + QSS
│   ├── stream_card.py          #   推流通道卡片组件
│   └── main_window.py          #   主窗口 (工具栏 + 滚动列表)
├── controllers/                # 控制层
│   ├── app_controller.py       #   全局控制 (托盘/配置/设备枚举/退出)
│   └── stream_controller.py    #   单路推流控制 (FFmpeg 生命周期)
└── services/                   # 服务层 (纯业务逻辑)
    ├── device_service.py       #   设备枚举 (摄像头/屏幕/窗口)
    ├── ffmpeg_service.py       #   FFmpeg 进程管理 + 命令构建
    └── window_capture.py       #   Win32 窗口捕获 (PrintWindow/BitBlt)
```

## 架构

```
┌─────────────────────────────────────────────┐
│                    Views                     │
│  MainWindow ◄──── StreamCardView (×N)        │
│  (信号 emit)      (信号 emit)                │
└──────┬──────────────────┬────────────────────┘
       │                  │
       ▼                  ▼
┌──────────────┐  ┌────────────────┐
│AppController │  │StreamController│  ← Controllers
│ (全局管理)    │  │ (单路推流)      │
└──────┬───────┘  └───────┬────────┘
       │                  │
       ▼                  ▼
┌─────────────────────────────────────────────┐
│              Models + Services               │
│  config.py  stream_model.py  device_service  │
│  ffmpeg_service  window_capture              │
└─────────────────────────────────────────────┘
```

- **Views** 只负责 UI 展示，通过 Qt 信号通知用户操作
- **Controllers** 连接信号、调用 Services、更新 Views 的 `set_*` 方法
- **Services** 封装纯业务逻辑（FFmpeg 进程、设备枚举、窗口捕获）
- **Models** 定义数据结构和持久化

## 许可

MIT