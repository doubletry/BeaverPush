"""
controllers/reconnect_policy.py -- 重连策略
============================================

从 StreamController 提取的重连状态机。
封装重连原因分类、调度、取消、计数逻辑。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QTimer

from ..models.stream_model import StreamState
from ..services.log_service import logger


class ReconnectCallback(Protocol):
    """重连策略需要的回调接口。"""

    def _start_stream_impl(self, preflight: bool) -> None:
        """启动推流实现。"""
        ...

    def _set_state(self, state: StreamState, text_override: str | None = None) -> None:
        """设置状态。"""
        ...

    def _report_status(self, message: str) -> None:
        """报告状态。"""
        ...


# 用于识别"推流目标服务器异常"的 FFmpeg 错误关键词。
SERVER_ERROR_KEYWORDS = (
    "connection refused", "no route to host", "timed out", "timeout",
    "broken pipe", "could not write header", "error writing trailer",
    "av_interleaved_write_frame", "connection reset",
)
# 用于识别"RTSP 输入源异常"的 FFmpeg 错误关键词。
RTSP_SOURCE_ERROR_KEYWORDS = (
    "method describe failed", "404", "401", "could not find codec parameters",
    "invalid data", "could not open", "end of file",
)


class ReconnectPolicy:
    """重连策略状态机。

    管理推流失败后的重连逻辑，包括：
    - 重连原因分类（source/server）
    - 重连调度（间隔、最大尝试次数）
    - 重连计数器
    - 重连计时器

    使用方式::

        policy = ReconnectPolicy(
            channel_index=0,
            source_type="camera",
            source_reconnect_interval=5,
            source_reconnect_max_attempts=0,
            server_reconnect_interval_getter=lambda: 5,
            server_reconnect_max_attempts_getter=lambda: 0,
            callback=self,
        )
        # 当推流失败时
        reason = policy.classify_reason(error_msg)
        if reason:
            policy.schedule(reason, friendly_msg)
    """

    def __init__(
        self,
        channel_index: int,
        source_type: str,
        source_reconnect_interval: int = 5,
        source_reconnect_max_attempts: int = 0,
        server_reconnect_interval_getter: Callable[[], int] | None = None,
        server_reconnect_max_attempts_getter: Callable[[], int] | None = None,
        callback: ReconnectCallback | None = None,
        parent: QObject | None = None,
    ):
        self._channel_index = channel_index
        self._source_type = source_type
        self._source_reconnect_interval = source_reconnect_interval
        self._source_reconnect_max_attempts = source_reconnect_max_attempts
        self._server_reconnect_interval_getter = server_reconnect_interval_getter or (lambda: 5)
        self._server_reconnect_max_attempts_getter = server_reconnect_max_attempts_getter or (lambda: 0)
        self._callback = callback

        self._reconnect_reason: str | None = None
        self._source_retry_count = 0
        self._server_retry_count = 0

        self._reconnect_timer = QTimer(parent)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)

    @property
    def reason(self) -> str | None:
        """当前重连原因。"""
        return self._reconnect_reason

    @property
    def is_active(self) -> bool:
        """是否有重连计时器在运行。"""
        return self._reconnect_timer.isActive()

    def update_source_type(self, source_type: str) -> None:
        """更新源类型（用于源类型切换时）。"""
        self._source_type = source_type

    def update_source_reconnect_interval(self, interval: int) -> None:
        """更新源重连间隔。"""
        self._source_reconnect_interval = interval

    def update_source_reconnect_max_attempts(self, max_attempts: int) -> None:
        """更新源重连最大尝试次数。"""
        self._source_reconnect_max_attempts = max_attempts

    def reset_counts(self) -> None:
        """重置重连计数器。"""
        self._source_retry_count = 0
        self._server_retry_count = 0

    def classify_reason(self, msg: str) -> str | None:
        """根据错误消息分类重连原因。

        Args:
            msg: FFmpeg 错误消息。

        Returns:
            "source" 或 "server" 或 None（不需要重连）。
        """
        lower = msg.lower()

        if self._source_type == "video":
            return "server" if any(k in lower for k in SERVER_ERROR_KEYWORDS) else None

        if self._source_type == "rtsp":
            if any(k in lower for k in RTSP_SOURCE_ERROR_KEYWORDS):
                return "source"
            if any(k in lower for k in SERVER_ERROR_KEYWORDS):
                return "server"
            # RTSP 输入断流时 FFmpeg 的报错文本分散，未知错误默认按源异常处理。
            return "source"

        if self._source_type in ("camera", "screen", "window", "hikcamera"):
            if any(k in lower for k in SERVER_ERROR_KEYWORDS):
                return "server"
            return "source"

        return None

    def default_reason_for_stop(self) -> str | None:
        """推流进程意外停止时的默认重连原因。

        Returns:
            "source" 或 None。
        """
        if self._source_type == "rtsp":
            return "source"
        if self._source_type in ("camera", "screen", "window", "hikcamera"):
            return "source"
        return None

    def schedule(self, reason: str, friendly: str) -> bool:
        """调度一次重连。

        Args:
            reason: 重连原因（"source" 或 "server"）。
            friendly: 友好的错误描述。

        Returns:
            True 表示已调度，False 表示达到重试上限。
        """
        interval = 0
        if reason == "server":
            interval = max(1, self._server_reconnect_interval_getter())
            max_attempts = max(0, self._server_reconnect_max_attempts_getter())
            if self._should_stop_retrying(self._server_retry_count, max_attempts):
                return False
            self._server_retry_count += 1
            status = self._format_retry_status("服务器失联", interval, self._server_retry_count)
        elif reason == "source":
            interval = max(1, self._source_reconnect_interval)
            if self._should_stop_retrying(
                self._source_retry_count,
                self._source_reconnect_max_attempts,
            ):
                return False
            self._source_retry_count += 1
            status = self._format_retry_status("源失联", interval, self._source_retry_count)
        else:
            return False

        self._reconnect_reason = reason
        logger.warning("推流异常，准备重连 ch={} reason={} msg={}", self._channel_index, reason, friendly)
        if self._callback:
            self._callback._report_status(f"通道 {self._channel_index + 1} {status}")
            self._callback._set_state(StreamState.RECONNECTING, status)
        self._reconnect_timer.start(interval * 1000)
        return True

    def cancel(self, reset_state: bool = True) -> None:
        """取消重连。

        Args:
            reset_state: 是否重置状态为 IDLE。
        """
        self._reconnect_timer.stop()
        self._reconnect_reason = None
        if reset_state and self._callback:
            self._callback._set_state(StreamState.IDLE)

    def _attempt_reconnect(self) -> None:
        """尝试重连。"""
        if self._callback:
            logger.warning("执行重连 ch={} reason={}", self._channel_index, self._reconnect_reason)
            self._callback._report_status(f"通道 {self._channel_index + 1} 正在执行重连")
            self._callback._start_stream_impl(preflight=False)

    @staticmethod
    def _format_retry_status(label: str, interval: int, attempt: int) -> str:
        """格式化重试状态文本。"""
        return f"{label}，{interval} 秒后重连（第 {attempt} 次）"

    @staticmethod
    def _should_stop_retrying(retry_count: int, max_attempts: int) -> bool:
        """是否达到重试上限；``max_attempts=0`` 表示无限重试。"""
        return max_attempts > 0 and retry_count >= max_attempts
