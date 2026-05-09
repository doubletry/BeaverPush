from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from .hikcamera_capture import (
    HikCameraProbeCancelled,
    probe_hikcamera_size_isolated,
)
from .log_service import logger


class HikCameraProbeWorker(QThread):
    """在后台线程中探测海康相机尺寸，并把阻塞 SDK 隔离到子进程。"""

    probe_succeeded = Signal(int, int)
    probe_failed = Signal(str)

    def __init__(
        self,
        serial_number: str,
        timeout_ms: int = 3000,
        *,
        process_timeout_seconds: float = 8.0,
        use_sdk_decode: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._serial_number = serial_number
        self._timeout_ms = timeout_ms
        self._process_timeout_seconds = process_timeout_seconds
        self._use_sdk_decode = bool(use_sdk_decode)
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        try:
            width, height = probe_hikcamera_size_isolated(
                self._serial_number,
                timeout_ms=self._timeout_ms,
                process_timeout_seconds=self._process_timeout_seconds,
                use_sdk_decode=self._use_sdk_decode,
                cancel_event=self._stop_requested,
            )
        except HikCameraProbeCancelled:
            return
        except TimeoutError:
            self.probe_failed.emit(
                "海康相机连接超时，探测进程未在限定时间内返回，请检查设备连接或重启相机 SDK。"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("海康相机后台探测失败 sn={} err={}", self._serial_number, exc)
            self.probe_failed.emit(str(exc))
        else:
            if not self._stop_requested.is_set():
                self.probe_succeeded.emit(width, height)
