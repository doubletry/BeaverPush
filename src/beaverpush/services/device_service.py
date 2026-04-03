"""
设备枚举服务模块
================

提供系统摄像头、显示器、可见窗口的枚举能力，
以及基于 ffprobe 的视频文件探测和 RTSP 可达性检测。

主要功能:
    - :func:`list_cameras`        — 通过 Qt Multimedia 列出摄像头
    - :func:`list_screens`        — 通过 QApplication.screens() 列出显示器
    - :func:`list_windows`        — 通过 Win32 EnumWindows 列出可见顶层窗口
    - :func:`probe_video_info`    — 通过 ffprobe 探测视频分辨率/编码/帧率
    - :func:`check_rtsp_reachable` — 通过 ffprobe 检测 RTSP 地址是否可达

.. note::
    :func:`list_windows` 仅支持 Windows 平台（依赖 ctypes.windll）。
"""

import ctypes
import ctypes.wintypes
import subprocess
import json
from dataclasses import dataclass

from .ffmpeg_path import get_ffprobe

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class CameraInfo:
    """摄像头设备信息。

    Attributes:
        index:       设备序号（从 0 开始）
        name:        设备描述名称
        device_path: DirectShow 设备路径（Windows 下通常与 name 相同）
    """

    index: int
    name: str
    device_path: str


@dataclass
class ScreenInfo:
    """显示器信息。

    Attributes:
        index:  显示器序号（主显示器通常为 0）
        name:   显示器标识符（如 ``\\\\.\\DISPLAY1``）
        width:  分辨率宽度（像素）
        height: 分辨率高度（像素）
        x:      虚拟桌面中的 X 偏移
        y:      虚拟桌面中的 Y 偏移
    """

    index: int
    name: str
    width: int
    height: int
    x: int
    y: int


@dataclass
class WindowInfo:
    """可见窗口信息。

    Attributes:
        hwnd:       窗口句柄 (HWND)
        title:      窗口标题
        class_name: 窗口类名
        left:       窗口矩形左边界
        top:        窗口矩形上边界
        right:      窗口矩形右边界
        bottom:     窗口矩形下边界
    """

    hwnd: int
    title: str
    class_name: str
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0


def list_cameras() -> list[CameraInfo]:
    """列出系统所有可用摄像头。

    通过 Qt Multimedia 的 :class:`QMediaDevices` 枚举视频输入设备。

    Returns:
        :class:`CameraInfo` 列表，顺序与系统枚举顺序一致。
    """
    from PySide6.QtMultimedia import QMediaDevices
    cameras = []
    for i, dev in enumerate(QMediaDevices.videoInputs()):
        cameras.append(CameraInfo(
            index=i,
            name=dev.description(),
            device_path=dev.description(),
        ))
    return cameras


def list_screens() -> list[ScreenInfo]:
    """列出系统所有显示器。

    通过 Win32 ``EnumDisplayMonitors`` + ``GetMonitorInfoW`` 枚举显示器，
    获取物理像素坐标，确保与 BitBlt 屏幕捕获坐标系一致。

    Returns:
        :class:`ScreenInfo` 列表。
    """
    screens: list[ScreenInfo] = []

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint32),
            ("rcMonitor", ctypes.wintypes.RECT),
            ("rcWork", ctypes.wintypes.RECT),
            ("dwFlags", ctypes.c_uint32),
            ("szDevice", ctypes.c_wchar * 32),
        ]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_void_p,
    )

    def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
            rc = info.rcMonitor
            screens.append(ScreenInfo(
                index=len(screens),
                name=info.szDevice.rstrip('\0'),
                width=rc.right - rc.left,
                height=rc.bottom - rc.top,
                x=rc.left,
                y=rc.top,
            ))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(
        None, None, MONITORENUMPROC(callback), 0,
    )

    # 回退到 Qt（理论上不会触发，仅为安全保护）
    if not screens:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if isinstance(app, QApplication):
            for i, screen in enumerate(app.screens()):
                geo = screen.geometry()
                ratio = screen.devicePixelRatio()
                screens.append(ScreenInfo(
                    index=i,
                    name=screen.name(),
                    width=int(geo.width() * ratio),
                    height=int(geo.height() * ratio),
                    x=int(geo.x() * ratio),
                    y=int(geo.y() * ratio),
                ))

    return screens


def list_windows() -> list[WindowInfo]:
    """列出所有可见的顶层窗口（仅 Windows 平台）。

    通过 Win32 ``EnumWindows`` 遍历顶层窗口，过滤条件：
        - 窗口可见 (``IsWindowVisible``)
        - 标题非空
        - 排除系统窗口类（Progman、Shell_TrayWnd 等）
        - 窗口尺寸大于 1×1 像素

    Returns:
        :class:`WindowInfo` 列表。
    """
    windows: list[WindowInfo] = []

    EnumWindows = ctypes.windll.user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible
    GetWindowTextW = ctypes.windll.user32.GetWindowTextW
    GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
    GetClassNameW = ctypes.windll.user32.GetClassNameW

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    GetWindowRect = ctypes.windll.user32.GetWindowRect

    SKIP_CLASSES = {
        "Progman", "WorkerW", "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
    }

    def callback(hwnd, _):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                cls_buf = ctypes.create_unicode_buffer(256)
                GetClassNameW(hwnd, cls_buf, 256)
                class_name = cls_buf.value
                if title and class_name not in SKIP_CLASSES:
                    rect = RECT()
                    GetWindowRect(hwnd, ctypes.byref(rect))
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    if w > 1 and h > 1:
                        windows.append(WindowInfo(
                            hwnd=hwnd,
                            title=title,
                            class_name=class_name,
                            left=rect.left,
                            top=rect.top,
                            right=rect.right,
                            bottom=rect.bottom,
                        ))
        return True

    EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def probe_video_info(file_path: str) -> dict:
    """使用 ffprobe 探测视频文件信息。

    Args:
        file_path: 视频文件路径。

    Returns:
        包含 ``width``, ``height``, ``codec``, ``framerate`` 键的字典；
        探测失败时返回空字典。
    """
    try:
        result = subprocess.run(
            [
                get_ffprobe(), "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                file_path,
            ],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                return {
                    "width": stream.get("width", ""),
                    "height": stream.get("height", ""),
                    "codec": stream.get("codec_name", ""),
                    "framerate": stream.get("r_frame_rate", ""),
                }
    except Exception:
        pass
    return {}


def check_rtsp_reachable(url: str, timeout: int = 5) -> tuple[bool, str]:
    """检测 RTSP 地址是否可达。

    通过 ``ffprobe -rtsp_transport tcp`` 尝试连接目标地址。

    Args:
        url:     RTSP 地址。
        timeout: 超时秒数，默认 5 秒。

    Returns:
        ``(reachable, message)`` 元组。``reachable`` 为是否可达，
        ``message`` 为中文描述。
    """
    try:
        result = subprocess.run(
            [get_ffprobe(), "-v", "error", "-rtsp_transport", "tcp", "-i", url],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        stderr = result.stderr.lower()
        if "connection refused" in stderr or "no route to host" in stderr:
            return False, "连接被拒绝或主机不可达"
        if "timeout" in stderr:
            return False, "连接超时"
        return True, "连接正常"
    except subprocess.TimeoutExpired:
        return False, "连接超时"
    except FileNotFoundError:
        return False, "未找到 ffprobe，请确认 FFmpeg 已安装"
    except Exception as e:
        return False, f"检查失败: {e}"


def get_screen_refresh_rate(x: int, y: int) -> int:
    """获取指定坐标所在屏幕的刷新率。

    Args:
        x: 屏幕坐标 X。
        y: 屏幕坐标 Y。

    Returns:
        刷新率（Hz），找不到时返回 30。
    """
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint
    app = QApplication.instance()
    if isinstance(app, QApplication):
        point = QPoint(x, y)
        for screen in app.screens():
            if screen.geometry().contains(point):
                return max(1, round(screen.refreshRate()))
    return 30
