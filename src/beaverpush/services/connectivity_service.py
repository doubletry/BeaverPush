"""
异步连通性检测服务
==================

将可能阻塞的 RTSP 源 / 服务器检测放到后台线程中执行，避免 UI 卡死。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from .log_service import logger

# ``(ok, message)`` 格式的后台检测函数。
CheckCallable = Callable[[], tuple[bool, str]]
# ``(阶段提示, 检测函数, 失败消息前缀)``。
CheckTask = tuple[str, CheckCallable, str]


class ConnectivityCheckWorker(QThread):
    """按顺序执行一组连通性检测任务。"""

    stage_changed = Signal(str)
    check_completed = Signal(bool, str)

    def __init__(self, tasks: list[CheckTask], parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            message = ""
            for stage, checker, failure_prefix in self._tasks:
                if self._stop_requested:
                    return
                self.stage_changed.emit(stage)
                ok, message = checker()
                if self._stop_requested:
                    return
                if not ok:
                    self.check_completed.emit(False, f"{failure_prefix}{message}")
                    return
            self.check_completed.emit(True, message)
        except Exception as exc:
            logger.exception("后台连通性检测异常")
            self.check_completed.emit(False, f"检测失败: {exc}")
