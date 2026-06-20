"""
services/ffmpeg_worker.py -- FFmpeg 推流进程管理
===============================================

从 ffmpeg_service.py 提取的 FFmpegWorker QThread 类。
负责 FFmpeg 子进程的启动、停止、进度解析以及预览管理。
"""

from __future__ import annotations

import re
import subprocess
import threading
import time

from PySide6.QtCore import QThread, Signal

from .ffmpeg_path import get_ffplay
from .window_capture import WindowCaptureFeeder, ScreenCaptureFeeder
from .hikcamera_capture import HikCameraFeeder
from .log_service import logger
from .rtsp_url import mask_sensitive_cmd

# Windows-only subprocess flag; on Unix the attribute does not exist and falls back to 0.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_STARTUP_TIMEOUT_SECONDS = 8.0
RTSP_STARTUP_TIMEOUT_SECONDS = 12.0
READY_LINE_KEYWORDS = (
    "press [q] to stop",
    "output #0, rtsp",
)


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
    preview_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._preview_process: subprocess.Popen | None = None
        self._capture_feeder: WindowCaptureFeeder | None = None
        self._screen_feeder: ScreenCaptureFeeder | None = None
        self._hik_feeder: HikCameraFeeder | None = None
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
        self._hik_sn: str = ""
        self._hik_w: int = 0
        self._hik_h: int = 0
        self._hik_fps: int = 30
        self._hik_use_sdk_decode: bool = True
        self._preview_monitor_thread: threading.Thread | None = None
        self._streaming_announced = False
        self._source_type: str = "video"
        self._startup_timeout_seconds = DEFAULT_STARTUP_TIMEOUT_SECONDS
        self._startup_watchdog_thread: threading.Thread | None = None

    def set_source_type(self, source_type: str):
        self._source_type = source_type
        if source_type == "rtsp":
            self._startup_timeout_seconds = RTSP_STARTUP_TIMEOUT_SECONDS
        else:
            self._startup_timeout_seconds = DEFAULT_STARTUP_TIMEOUT_SECONDS

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

    def set_hik_capture(
        self,
        sn: str,
        width: int,
        height: int,
        fps: int = 30,
        *,
        use_sdk_decode: bool = True,
    ):
        """配置海康工业相机捕获参数（在 ``run()`` 之前调用）。"""
        self._hik_sn = (sn or "").strip()
        self._hik_w = int(width)
        self._hik_h = int(height)
        self._hik_fps = fps if fps > 0 else 30
        self._hik_use_sdk_decode = bool(use_sdk_decode)

    def start_preview_now(self, rtsp_url: str):
        """在推流过程中动态开启预览。"""
        self._preview_url = rtsp_url
        self._preview_enabled = True
        self._start_preview()
        self._start_preview_monitor()

    def stop_preview_now(self):
        """在推流过程中动态关闭预览。"""
        self._preview_enabled = False
        self._stop_preview()

    def run(self):
        self._stop_flag = False
        self._streaming_announced = False
        self.status_changed.emit("正在启动推流...")
        logger.debug("FFmpeg 启动命令: {}", mask_sensitive_cmd(self._cmd))

        try:
            use_pipe = (
                self._window_hwnd != 0
                or self._screen_w != 0
                or bool(self._hik_sn)
            )

            self._process = subprocess.Popen(
                self._cmd,
                stdin=subprocess.PIPE if use_pipe else None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
            self.status_changed.emit("等待数据...")
            self._start_startup_watchdog()

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
            elif use_pipe and self._hik_sn:
                self._hik_feeder = HikCameraFeeder(
                    self._hik_sn, self._hik_w, self._hik_h, self._hik_fps,
                    use_sdk_decode=self._hik_use_sdk_decode,
                )
                self._hik_feeder.set_error_callback(self.error_occurred.emit)
                try:
                    self._hik_feeder.start(self._process)
                except Exception as exc:
                    logger.exception("海康相机启动失败")
                    self.error_occurred.emit(str(exc))
                    try:
                        self._process.terminate()
                    except Exception:
                        pass

            if self._preview_enabled and self._preview_url:
                time.sleep(2)
                self._start_preview()

            assert self._process.stderr is not None
            for line in iter(self._process.stderr.readline, b""):
                if self._stop_flag:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                info = self._parse_progress(line_str)
                if info:
                    self._mark_streaming()
                    self.progress_info.emit(info)
                elif self._is_ready_line(line_str):
                    self._mark_streaming()

                if self._is_error(line_str):
                    self.error_occurred.emit(line_str)

            self._process.wait()

            if self._process.returncode != 0 and not self._stop_flag and self._process.stderr:
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
            logger.error("ffmpeg 可执行文件未找到")
            self.error_occurred.emit(
                "未找到 ffmpeg，请确认 FFmpeg 已安装并加入 PATH"
            )
        except PermissionError:
            logger.error("ffmpeg 执行权限不足")
            self.error_occurred.emit("没有权限执行 ffmpeg")
        except Exception as e:
            logger.exception("FFmpeg 推流异常")
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
        if self._hik_feeder:
            self._hik_feeder.stop()
            self._hik_feeder = None
        self._stop_preview()
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
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
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass

    def _stop_preview(self):
        if self._preview_process:
            try:
                if self._preview_process.poll() is None:
                    self._preview_process.terminate()
            except Exception:
                pass
            self._preview_process = None

    def _start_preview_monitor(self):
        """启动守护线程监控 ffplay 进程，关闭时发出 preview_closed 信号。"""
        proc = self._preview_process
        if not proc:
            return

        def _watch():
            try:
                proc.wait()
            except Exception:
                pass
            # 仅当预览仍处于启用状态时才发信号（用户主动停止时已置 False）
            if self._preview_enabled:
                self._preview_enabled = False
                # 主窗口可能已被关闭、Worker QObject 已销毁，
                # 此时直接 emit 会触发 RuntimeError 让进程崩溃；吞掉即可。
                try:
                    self.preview_closed.emit()
                except RuntimeError:
                    pass

        t = threading.Thread(target=_watch, daemon=True)
        t.start()
        self._preview_monitor_thread = t

    def _cleanup(self):
        if self._capture_feeder:
            self._capture_feeder.stop()
            self._capture_feeder = None
        if self._screen_feeder:
            self._screen_feeder.stop()
            self._screen_feeder = None
        if self._hik_feeder:
            self._hik_feeder.stop()
            self._hik_feeder = None
        self._stop_preview()
        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.kill()
            except Exception:
                pass
            self._process = None

    def _start_startup_watchdog(self):
        timeout = self._startup_timeout_seconds
        proc = self._process
        if not proc or timeout <= 0:
            return

        def _watch():
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                current = self._process
                if (
                    self._stop_flag
                    or self._streaming_announced
                    or not current
                    or current.poll() is not None
                ):
                    return
                time.sleep(0.1)

            current = self._process
            if (
                self._stop_flag
                or self._streaming_announced
                or not current
                or current.poll() is not None
            ):
                return

            if self._source_type == "rtsp":
                msg = "等待 RTSP 源数据超时，请检查源地址、网络或设备状态。"
            else:
                msg = "启动超时，长时间未收到数据，请检查输入源状态。"
            logger.warning(
                "FFmpeg 启动超时 source_type={} timeout={}s",
                self._source_type,
                timeout,
            )
            try:
                self.error_occurred.emit(msg)
            except RuntimeError:
                # Worker QObject 可能已被销毁，避免后台守护线程把进程拖垮
                pass
            try:
                current.terminate()
            except Exception:
                pass

        thread = threading.Thread(target=_watch, daemon=True)
        thread.start()
        self._startup_watchdog_thread = thread

    def _mark_streaming(self):
        if self._streaming_announced:
            return
        self._streaming_announced = True
        self.status_changed.emit("推流中")

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
    def _is_ready_line(line: str) -> bool:
        line_lower = line.lower()
        return any(keyword in line_lower for keyword in READY_LINE_KEYWORDS)

    @staticmethod
    def _is_error(line: str) -> bool:
        error_keywords = [
            "connection refused", "no route to host",
            "connection timed out", "could not open",
            "invalid data found", "server returned",
            "i/o error", "error",  # [改进] 添加 i/o error 关键词检测
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
