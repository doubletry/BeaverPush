"""
FFmpeg 推流服务模块
===================

封装 FFmpeg 子进程的启动、停止、进度解析以及命令行构建逻辑。

核心组件:
    - :class:`FFmpegWorker`        — QThread 子类，管理 FFmpeg 进程生命周期
    - :func:`build_ffmpeg_command`  — 根据源类型和参数构建完整的 ffmpeg 命令行
    - :func:`friendly_error`        — 将 FFmpeg 原始错误映射为用户友好的中文提示

支持的视频源类型:
    - ``video``  : 本地视频文件（支持循环播放）
    - ``camera`` : DirectShow 摄像头
    - ``rtsp``   : RTSP 拉流再推流
    - ``screen`` : GDI 屏幕捕获（按显示器区域）
    - ``window`` : Win32 窗口捕获（rawvideo 管道 + PrintWindow/BitBlt）

架构::

    Controller
        │
        ├─▶ build_ffmpeg_command()  → list[str]
        │
        └─▶ FFmpegWorker (QThread)
              ├── status_changed  (str)    → View.set_status()
              ├── error_occurred  (str)    → View.show_error()
              ├── progress_info   (dict)   → View.set_progress()
              └── stopped         ()       → Controller._on_worker_stopped()
"""

import subprocess
import re

from PySide6.QtCore import QThread, Signal

from .ffmpeg_path import get_ffmpeg, get_ffplay
from .window_capture import WindowCaptureFeeder, ScreenCaptureFeeder, get_window_rect


def _make_even(v: int) -> int:
    """将值调整为最近的偶数（FFmpeg 要求宽高为偶数）。"""
    return v if v % 2 == 0 else v + 1


class FFmpegWorker(QThread):
    """在独立线程中运行 FFmpeg 推流进程。

    使用方式::

        worker = FFmpegWorker()
        worker.set_command(cmd)                     # 设置 ffmpeg 命令
        worker.set_preview(True, "rtsp://...")       # 可选：启用 ffplay 预览
        worker.set_window_capture(hwnd, fps=30)      # 可选：窗口捕获管道模式
        worker.start()                               # 启动线程
        ...
        worker.stop()                                # 安全停止

    Signals:
        status_changed(str):  状态文本变更（"正在启动推流..." / "推流中" / "已停止"）
        error_occurred(str):  发生错误时携带错误信息
        progress_info(dict):  FFmpeg 进度信息（frame, fps, bitrate, time, speed 等）
        stopped():            推流完全停止后触发
    """

    status_changed = Signal(str)
    error_occurred = Signal(str)
    progress_info = Signal(dict)
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._preview_process: subprocess.Popen | None = None
        self._capture_feeder: WindowCaptureFeeder | None = None
        self._screen_feeder: ScreenCaptureFeeder | None = None
        self._stop_flag = False
        self._cmd: list[str] = []
        self._preview_url: str = ""
        self._preview_enabled: bool = False
        self._window_hwnd: int = 0
        self._window_fps: int = 30
        self._screen_x: int = 0
        self._screen_y: int = 0
        self._screen_w: int = 0
        self._screen_h: int = 0
        self._screen_fps: int = 30

    def set_command(self, cmd: list[str]):
        self._cmd = cmd

    def set_preview(self, enabled: bool, rtsp_url: str = ""):
        self._preview_enabled = enabled
        self._preview_url = rtsp_url

    def set_window_capture(self, hwnd: int, fps: int = 30):
        self._window_hwnd = hwnd
        self._window_fps = fps

    def set_screen_capture(self, x: int, y: int, w: int, h: int, fps: int = 30):
        self._screen_x = x
        self._screen_y = y
        self._screen_w = w
        self._screen_h = h
        self._screen_fps = fps

    def run(self):
        self._stop_flag = False
        self.status_changed.emit("正在启动推流...")

        try:
            use_pipe = self._window_hwnd != 0 or self._screen_w != 0

            self._process = subprocess.Popen(
                self._cmd,
                stdin=subprocess.PIPE if use_pipe else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            if use_pipe and self._window_hwnd:
                self._capture_feeder = WindowCaptureFeeder(
                    self._window_hwnd, self._window_fps
                )
                self._capture_feeder.start(self._process)
            elif use_pipe and self._screen_w:
                self._screen_feeder = ScreenCaptureFeeder(
                    self._screen_x, self._screen_y,
                    self._screen_w, self._screen_h,
                    self._screen_fps,
                )
                self._screen_feeder.start(self._process)

            self.status_changed.emit("推流中")

            if self._preview_enabled and self._preview_url:
                import time
                time.sleep(2)
                self._start_preview()

            for line in iter(self._process.stderr.readline, b""):
                if self._stop_flag:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                info = self._parse_progress(line_str)
                if info:
                    self.progress_info.emit(info)

                if self._is_error(line_str):
                    self.error_occurred.emit(line_str)

            self._process.wait()

            if self._process.returncode != 0 and not self._stop_flag:
                remaining = self._process.stderr.read().decode(
                    "utf-8", errors="replace"
                )
                error_msg = self._extract_error(remaining)
                if error_msg:
                    self.error_occurred.emit(error_msg)
                else:
                    self.error_occurred.emit(
                        f"FFmpeg 退出，返回码: {self._process.returncode}"
                    )

        except FileNotFoundError:
            self.error_occurred.emit(
                "未找到 ffmpeg，请确认 FFmpeg 已安装并加入 PATH"
            )
        except PermissionError:
            self.error_occurred.emit("没有权限执行 ffmpeg")
        except Exception as e:
            self.error_occurred.emit(f"推流异常: {e}")
        finally:
            self._cleanup()
            self.status_changed.emit("已停止")
            self.stopped.emit()

    def stop(self):
        self._stop_flag = True
        if self._capture_feeder:
            self._capture_feeder.stop()
            self._capture_feeder = None
        if self._screen_feeder:
            self._screen_feeder.stop()
            self._screen_feeder = None
        self._stop_preview()
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            except Exception:
                pass

    def _start_preview(self):
        try:
            self._preview_process = subprocess.Popen(
                [
                    get_ffplay(),
                    "-rtsp_transport", "tcp",
                    "-i", self._preview_url,
                    "-window_title", "推流预览",
                    "-x", "640", "-y", "480",
                    "-fflags", "nobuffer",
                    "-flags", "low_delay",
                    "-framedrop",
                    "-an",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass

    def _stop_preview(self):
        if self._preview_process:
            try:
                if self._preview_process.poll() is None:
                    self._preview_process.terminate()
                    try:
                        self._preview_process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._preview_process.kill()
            except Exception:
                pass
            self._preview_process = None

    def _cleanup(self):
        if self._capture_feeder:
            self._capture_feeder.stop()
            self._capture_feeder = None
        if self._screen_feeder:
            self._screen_feeder.stop()
            self._screen_feeder = None
        self._stop_preview()
        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.kill()
            except Exception:
                pass
            self._process = None

    @staticmethod
    def _parse_progress(line: str) -> dict | None:
        if "frame=" not in line and "size=" not in line:
            return None
        info = {}
        patterns = {
            "frame": r"frame=\s*(\d+)",
            "fps": r"fps=\s*([\d.]+)",
            "bitrate": r"bitrate=\s*([\d.]+\s*\w+/s)",
            "time": r"time=\s*([\d:.]+)",
            "speed": r"speed=\s*([\d.]+x)",
            "size": r"size=\s*([\d.]+\s*\w+)",
        }
        for key, pattern in patterns.items():
            m = re.search(pattern, line)
            if m:
                info[key] = m.group(1).strip()
        return info if info else None

    @staticmethod
    def _is_error(line: str) -> bool:
        error_keywords = [
            "connection refused", "no route to host",
            "connection timed out", "could not open",
            "invalid data found", "server returned", "error",
        ]
        line_lower = line.lower()
        if "frame=" in line_lower or "size=" in line_lower:
            return False
        return any(kw in line_lower for kw in error_keywords)

    @staticmethod
    def _extract_error(text: str) -> str:
        lines = text.strip().split("\n")
        errors = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if any(kw in line.lower() for kw in [
                "error", "failed", "refused", "timeout", "invalid",
                "could not", "no such", "permission denied",
            ]):
                errors.append(line)
        if errors:
            return "\n".join(errors[-3:])
        if lines:
            return lines[-1]
        return ""


def build_ffmpeg_command(
    source_type: str,
    source_path: str,
    rtsp_url: str,
    loop: bool = False,
    video_codec: str = "",
    width: str = "",
    height: str = "",
    framerate: str = "",
    bitrate: str = "",
) -> list[str]:
    """根据视频源类型构建完整的 FFmpeg 推流命令行。

    Args:
        source_type: 视频源类型 (``"video"``/``"camera"``/``"rtsp"``/``"screen"``/``"window"``)
        source_path: 视频源路径。各类型的格式：

            - ``video``  : 文件绝对路径
            - ``camera`` : DirectShow 设备名
            - ``rtsp``   : RTSP 拉流 URL
            - ``screen`` : ``"offset:x,y,w,h"``（屏幕偏移与尺寸）
            - ``window`` : ``"hwnd:<句柄>"`` 或窗口标题

        rtsp_url:    RTSP 推流目标地址
        loop:        是否循环播放（仅 ``video`` 类型有效）
        video_codec: 视频编码器，空字符串表示自动选择 ``libx264``
        width:       输出宽度（空字符串表示不缩放）
        height:      输出高度
        framerate:   输出帧率
        bitrate:     输出码率（如 ``"2M"``）

    Returns:
        可直接传给 ``subprocess.Popen`` 的命令行参数列表。

    Raises:
        ValueError: 不支持的 ``source_type``。

    Note:
        屏幕捕获统一使用 ``"offset:x,y,w,h"`` 格式（包括主屏幕），
        确保只捕获选定的单个屏幕，而不是整个虚拟桌面。
    """
    cmd = [get_ffmpeg(), "-y"]

    # ---- 输入部分 ----
    if source_type == "video":
        if loop:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-re", "-i", source_path]

    elif source_type == "camera":
        if framerate:
            cmd += ["-framerate", framerate]
        cmd += ["-f", "dshow", "-i", f"video={source_path}"]

    elif source_type == "rtsp":
        cmd += ["-rtsp_transport", "tcp", "-i", source_path]

    elif source_type == "screen":
        # 屏幕捕获：使用 rawvideo 管道模式，通过 BitBlt + DrawIconEx
        # 替代 gdigrab，彻底解决鼠标闪烁问题
        # source_path 格式: "offset:x,y,w,h"
        if source_path.startswith("offset:"):
            parts = source_path.split(":", 1)[1].split(",")
            if len(parts) == 4:
                ow, oh = int(parts[2]), int(parts[3])
                w = _make_even(ow)
                h = _make_even(oh)
                fps = framerate if framerate else "30"
                cmd += [
                    "-f", "rawvideo",
                    "-pixel_format", "bgra",
                    "-video_size", f"{w}x{h}",
                    "-framerate", fps,
                    "-i", "pipe:0",
                ]
        else:
            raise ValueError("屏幕捕获源路径格式错误，应为 offset:x,y,w,h")

    elif source_type == "window":
        # 窗口捕获：rawvideo 管道
        if source_path.startswith("hwnd:"):
            hwnd = int(source_path.split(":")[1])
            _, _, w, h = get_window_rect(hwnd)
            w = _make_even(w)
            h = _make_even(h)
            fps = framerate if framerate else "30"
            cmd += [
                "-f", "rawvideo",
                "-pixel_format", "bgra",
                "-video_size", f"{w}x{h}",
                "-framerate", fps,
                "-i", "pipe:0",
            ]
        else:
            input_args = ["-f", "gdigrab"]
            if framerate:
                input_args += ["-framerate", framerate]
            else:
                input_args += ["-framerate", "30"]
            input_args += ["-i", f"title={source_path}"]
            cmd += input_args

    else:
        raise ValueError(f"不支持的视频源类型: {source_type}")

    # ---- 滤镜 ----
    filters = []
    if width and height:
        w_val = int(width) if width.isdigit() else width
        h_val = int(height) if height.isdigit() else height
        if isinstance(w_val, int):
            w_val = _make_even(w_val)
        if isinstance(h_val, int):
            h_val = _make_even(h_val)
        filters.append(f"scale={w_val}:{h_val}")

    # ---- 编码 ----
    if source_type in ("screen", "window", "camera"):
        codec = video_codec if video_codec else "libx264"
        cmd += ["-c:v", codec, "-preset", "ultrafast", "-tune", "zerolatency"]
    elif source_type == "rtsp":
        codec = video_codec if video_codec else "libx264"
        cmd += ["-c:v", codec]
        if codec == "libx264":
            cmd += ["-preset", "ultrafast", "-tune", "zerolatency"]
        if codec == "copy":
            cmd += ["-c:a", "copy"]
    else:
        codec = video_codec if video_codec else "libx264"
        cmd += ["-c:v", codec]
        if codec == "libx264":
            cmd += ["-preset", "ultrafast", "-tune", "zerolatency"]
        if codec == "copy":
            cmd += ["-c:a", "copy"]

    # ---- 输出参数 ----
    if filters:
        cmd += ["-vf", ",".join(filters)]

    if framerate and source_type not in ("camera", "screen", "window"):
        cmd += ["-r", framerate]

    if bitrate:
        cmd += ["-b:v", bitrate]

    if codec != "copy":
        cmd += ["-pix_fmt", "yuv420p"]

    cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", rtsp_url]
    return cmd


def friendly_error(msg: str) -> str:
    """将 FFmpeg 原始错误信息映射为用户友好的中文提示。

    会在原始信息前附加中文说明，方便用户排查问题。
    如果没有匹配到已知关键词，则原样返回。

    Args:
        msg: FFmpeg 输出的错误文本。

    Returns:
        包含中文说明和原始信息的字符串。
    """
    lower = msg.lower()
    mapping = [
        ("connection refused", "连接被拒绝，请检查 RTSP 服务器是否已启动。"),
        ("no route to host", "主机不可达，请检查网络连接。"),
        ("timed out", "连接超时，请检查网络。"),
        ("timeout", "连接超时，请检查网络。"),
        ("no such file", "文件不存在，请检查路径。"),
        ("does not exist", "文件不存在，请检查路径。"),
        ("permission denied", "权限不足。"),
        ("could not open", "无法打开源，请检查输入。"),
        ("invalid data", "无效的数据格式。"),
        ("error initializing output stream", "编码器初始化失败，建议宽高设为偶数。"),
        ("incorrect parameters", "编码参数不兼容。"),
    ]
    for keyword, friendly in mapping:
        if keyword in lower:
            return f"{friendly}\n\n原始信息:\n{msg}"
    return msg
