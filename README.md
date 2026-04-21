# BeaverPush — Multi-Channel RTSP Streaming Client

[中文文档](README_zh.md)

A multi-channel RTSP streaming desktop client built with **PySide6 + MVC architecture** on Windows.

## Features

- 🎥 **6 video source types:** local video files, cameras, RTSP pull-to-push, screen capture, window capture, Hikvision industrial cameras
- 📡 **Multi-channel streaming** with independent start/stop control per channel
- 🔐 **v2 authenticated streaming:** supports window-to-web v2 username + API key authentication, three-level stream path `username/machine/channel`
- 🎨 **Catppuccin Mocha** dark theme
- 🔧 **Configurable encoding:** codec (h264/h265/NVENC), resolution, framerate, bitrate
- 💾 **Auto-persistent configuration** (JSON)
- 👁️ **Live preview** via ffplay
- 🖥️ **System tray** minimize support
- 🔒 **Server lock** to prevent accidental RTSP address changes
- 🔄 **Loop playback** for local video files
- 🖱️ **Editable channel names** with click-to-edit titles
- 🔑 **Auto-detect machine name** using motherboard UUID
- ✅ **Input validation** for username, machine name, and stream names (ASCII-safe characters only)
- 🔄 **Auto-reconnect** with configurable interval for RTSP sources and Hikvision industrial cameras
- 💾 **Auto-save on successful connection test**

## Download

Download the latest installer from [GitHub Releases](https://github.com/doubletry/BeaverPush/releases). The installer bundles FFmpeg — no additional setup required.

## Development Setup

### Prerequisites

- **Python** ≥ 3.12
- **FFmpeg** / **ffprobe** / **ffplay** in `PATH` (or place them in a `ffmpeg/` subdirectory)
- **uv** package manager

### Install & Run

```bash
# Install dependencies
uv sync

# Run the application
uv run beaverpush
# or
uv run python -m beaverpush.main
```

### Run Tests

```bash
uv run pytest
```

### Build from Source

Build a standalone executable and Windows installer:

```powershell
# Build executable + installer (requires Inno Setup 6)
.\build.ps1 -Version "1.0.0"
```

The build script uses **Nuitka** to compile a standalone executable (`dist/main.dist/BeaverPush.exe`) and **Inno Setup** to create the installer (`dist/BeaverPushSetup.exe`).

## Usage

1. Enter the RTSP server address (e.g. `rtsp://192.168.1.100:8554`)
2. Enter your **Username** (your account on window-to-web) and **Auth Secret** (API key generated on the window-to-web web UI)
3. Set a **Machine Name** to identify this streaming device (auto-detected from motherboard UUID if left empty)
4. Click **Add Channel** to create a streaming channel
5. Select a video source type and configure parameters
6. Click **Start** to begin streaming (stream names default to `stream1`, `stream2`, etc. if left empty)

The stream path follows a three-level structure: `{username}/{machine}/{channel}`, e.g. `alice/pc1/stream1`.

### Video Source Types

| Source | Description |
|--------|-------------|
| Local Video | Stream a video file (supports loop playback) |
| Camera | Stream from a DirectShow camera device |
| RTSP | Pull from an RTSP source and re-push |
| Screen | Capture a display/monitor region |
| Window | Capture a specific application window |
| Hikvision Industrial Camera | Capture from a Hikvision industrial camera by serial number |

### Advanced Settings

Toggle **Advanced** mode on a channel card to configure:
- **Codec:** libx264, h264_nvenc, hevc_nvenc, copy
- **Resolution:** Width × Height (auto-adjusted to even numbers)
- **Framerate** and **Bitrate** (fixed `M`; leave bitrate empty for no bitrate limit)

## Project Structure

```
src/beaverpush/
├── main.py                      # Application entry point
├── models/
│   ├── config.py                # JSON config persistence (AppConfig, StreamConfig)
│   └── stream_model.py          # StreamState enum
├── views/
│   ├── theme.py                 # Catppuccin Mocha theme + QSS
│   ├── stream_card.py           # Stream channel card widget
│   └── main_window.py           # Main window (toolbar + scrollable card list)
├── controllers/
│   ├── app_controller.py        # App lifecycle, config, device enumeration
│   └── stream_controller.py     # Single channel FFmpeg lifecycle
└── services/
    ├── device_service.py        # Device enumeration (cameras/screens/windows)
    ├── ffmpeg_service.py        # FFmpeg process management + command building
    ├── ffmpeg_path.py           # FFmpeg executable path resolution
    ├── log_service.py           # Loguru-based logging
    └── window_capture.py        # Win32 window/screen capture (PrintWindow/BitBlt)
```

## Architecture

```
┌───────────────────────────────────────────────┐
│                     Views                      │
│  MainWindow ◄──── StreamCardView (×N)          │
│  (Qt signals)     (Qt signals)                 │
└──────┬──────────────────┬──────────────────────┘
       │                  │
       ▼                  ▼
┌──────────────┐  ┌─────────────────┐
│AppController │  │StreamController │  ← Controllers
│ (global)     │  │ (per channel)   │
└──────┬───────┘  └────────┬────────┘
       │                   │
       ▼                   ▼
┌───────────────────────────────────────────────┐
│              Models + Services                 │
│  config · stream_model · device_service        │
│  ffmpeg_service · ffmpeg_path · window_capture │
└───────────────────────────────────────────────┘
```

- **Views** — UI rendering only; emit Qt signals for user actions
- **Controllers** — Connect signals, call services, update views via `set_*` methods
- **Services** — Pure business logic (FFmpeg process, device enumeration, window capture)
- **Models** — Data structures and persistence

## CI/CD

Automated builds are triggered by pushing a version tag (e.g. `v1.0.0`). The GitHub Actions workflow:

1. Sets up Python 3.12 + uv
2. Downloads the pinned FFmpeg n8.1 shared binaries used for packaging
3. Installs Inno Setup 6
4. Compiles with Nuitka and packages the installer
5. Runs a silent install verification test

## License

[MIT](LICENSE)
