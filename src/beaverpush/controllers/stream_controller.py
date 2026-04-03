"""
单路推流通道控制器
==================

``StreamController`` 将一个 ``StreamCardView``（View）与
推流业务逻辑（FFmpegWorker / 配置数据）连接在一起。

职责：
    1. 监听卡片 UI 信号 → 更新内部数据 / 触发推流操作
    2. 监听 FFmpegWorker 信号 → 更新卡片 UI 状态
    3. 提供 ``to_config`` / ``from_config`` 进行配置序列化/反序列化
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ..models.stream_model import StreamState
from ..models.config import StreamConfig
from ..services.ffmpeg_service import (
    FFmpegWorker, build_ffmpeg_command, friendly_error, check_rtsp_server_reachable,
)
from ..services.device_service import probe_video_info, get_screen_refresh_rate, check_rtsp_reachable
from ..views.stream_card import StreamCardView
from ..services.log_service import logger

SERVER_ERROR_KEYWORDS = (
    "connection refused", "no route to host", "timed out", "timeout",
    "broken pipe", "could not write header", "error writing trailer",
    "av_interleaved_write_frame", "connection reset",
)
RTSP_SOURCE_ERROR_KEYWORDS = (
    "method describe failed", "404", "401", "could not find codec parameters",
    "invalid data", "could not open", "end of file",
)


class StreamController(QObject):
    """单路推流通道控制器。"""

    state_changed = Signal(object)  # StreamState

    def __init__(
        self,
        card: StreamCardView,
        channel_index: int,
        rtsp_server_getter: Callable[[], str],
        client_id_getter: Callable[[], str] | None = None,
        server_reconnect_interval_getter: Callable[[], int] | None = None,
        server_reconnect_max_attempts_getter: Callable[[], int] | None = None,
        status_reporter: Callable[[str], None] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._card = card
        self._channel_index = channel_index
        self._rtsp_server_getter = rtsp_server_getter
        self._client_id_getter = client_id_getter
        self._server_reconnect_interval_getter = server_reconnect_interval_getter or (lambda: 5)
        self._server_reconnect_max_attempts_getter = server_reconnect_max_attempts_getter or (lambda: 0)
        self._status_reporter = status_reporter
        self._worker: FFmpegWorker | None = None
        self._state = StreamState.IDLE

        self._source_type = card.get_source_type() or "video"
        self._source_path = ""
        self._stream_name = ""
        self._title = ""
        self._loop = False
        self._preview = False
        self._video_codec = ""
        self._width = ""
        self._height = ""
        self._framerate = ""
        self._bitrate = ""
        self._rtsp_url = ""
        self._source_reconnect_interval = 5
        self._source_reconnect_max_attempts = 0

        self._last_error = ""
        self._handled_worker_failure = False
        self._stop_requested = False
        self._reconnect_reason: str | None = None
        self._source_retry_count = 0
        self._server_retry_count = 0

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)

        self._connect_card_signals()

    def _connect_card_signals(self):
        c = self._card
        c.source_type_changed.connect(self._on_source_type)
        c.source_path_edited.connect(self._on_source_path)
        c.device_selected.connect(self._on_device_selected)
        c.browse_clicked.connect(self._on_browse)
        c.start_clicked.connect(self.start_stream)
        c.stop_clicked.connect(self.stop_stream)
        c.stream_name_edited.connect(self._on_stream_name)
        c.codec_changed.connect(self._on_codec)
        c.width_edited.connect(self._on_width)
        c.height_edited.connect(self._on_height)
        c.fps_edited.connect(self._on_fps)
        c.bitrate_edited.connect(self._on_bitrate)
        c.source_reconnect_interval_edited.connect(self._on_source_reconnect_interval)
        c.source_reconnect_max_attempts_edited.connect(self._on_source_reconnect_max_attempts)
        c.loop_toggled.connect(self._on_loop)
        c.preview_clicked.connect(self.toggle_preview)
        c.title_edited.connect(self._on_title)

    def _on_source_type(self, key: str):
        self._source_type = key
        self._source_path = ""

    def _on_source_path(self, path: str):
        self._source_path = path

    def _on_device_selected(self, value: str):
        self._source_path = value

    def _on_browse(self):
        path = self._card.browse_file()
        if path:
            self._source_path = path
            self._card.set_source_path(path)

    def _on_stream_name(self, name: str):
        self._stream_name = name

    def _on_codec(self, codec: str):
        self._video_codec = codec

    def _on_width(self, w: str):
        self._width = w

    def _on_height(self, h: str):
        self._height = h

    def _on_fps(self, fps: str):
        self._framerate = fps

    def _on_bitrate(self, br: str):
        self._bitrate = br

    def _on_source_reconnect_interval(self, value: str):
        self._source_reconnect_interval = self._parse_positive_int(value, 5)

    def _on_source_reconnect_max_attempts(self, value: str):
        self._source_reconnect_max_attempts = self._parse_non_negative_int(value, 0)

    def _on_loop(self, val: bool):
        self._loop = val

    def _on_title(self, title: str):
        self._title = title

    def start_stream(self):
        self._stop_requested = False
        self._cancel_reconnect(reset_state=False)
        self._source_retry_count = 0
        self._server_retry_count = 0
        self._start_stream_impl(preflight=True)

    def _start_stream_impl(self, preflight: bool):
        rtsp_server = self._rtsp_server_getter()
        if not rtsp_server:
            self._card.show_error("请先配置 RTSP 服务器地址")
            return
        if not self._source_path:
            self._card.show_error("请选择或输入视频源")
            return
        if not self._stream_name:
            self._card.show_error("请输入流名称")
            return

        client_id = self._client_id_getter() if self._client_id_getter else ""
        if not client_id:
            self._card.show_error("请先配置客户端 ID")
            return

        if self._source_type == "rtsp" and not self._source_path.startswith("rtsp://"):
            self._card.show_error("RTSP 地址格式不正确，应以 rtsp:// 开头")
            return
        if self._source_type == "video":
            import os
            if not os.path.isfile(self._source_path):
                self._card.show_error("视频文件不存在，请检查路径")
                return

        if preflight:
            if self._source_type == "rtsp":
                reachable, message = check_rtsp_reachable(self._source_path)
                if not reachable:
                    self._card.show_error(f"RTSP 源不可用：{message}")
                    return
            server_ok, server_message = check_rtsp_server_reachable(rtsp_server)
            if not server_ok:
                self._card.show_error(server_message)
                return

        rtsp_url = f"{rtsp_server.rstrip('/')}/{client_id}/{self._stream_name}"
        self._rtsp_url = rtsp_url
        codec = self._video_codec if self._video_codec != "自动" else ""

        width = self._width
        height = self._height
        framerate = self._framerate
        bitrate = self._bitrate

        if self._source_type == "screen":
            if not codec:
                codec = "libx264"
            if self._source_path.startswith("offset:"):
                parts = self._source_path.split(":", 1)[1].split(",")
                if len(parts) == 4 and not framerate:
                    framerate = str(get_screen_refresh_rate(int(parts[0]), int(parts[1])))
        elif self._source_type == "window":
            if not codec:
                codec = "libx264"
            if not framerate:
                framerate = "30"
        elif self._source_type == "camera":
            if not codec:
                codec = "libx264"
        elif self._source_type == "video":
            info = probe_video_info(self._source_path)
            if not codec and info.get("codec"):
                codec = "copy"
            if codec != "copy":
                if not width and info.get("width"):
                    width = str(info["width"])
                if not height and info.get("height"):
                    height = str(info["height"])
            if not framerate and info.get("framerate"):
                fr = str(info["framerate"])
                if "/" in fr:
                    num, den = fr.split("/")
                    framerate = str(round(int(num) / int(den)))
                else:
                    framerate = fr
        elif self._source_type == "rtsp" and not codec:
            codec = "copy"

        try:
            cmd = build_ffmpeg_command(
                source_type=self._source_type,
                source_path=self._source_path,
                rtsp_url=rtsp_url,
                loop=self._loop,
                video_codec=codec,
                width=width,
                height=height,
                framerate=framerate,
                bitrate=bitrate,
            )
        except ValueError as e:
            self._card.show_error(str(e))
            return

        self._handled_worker_failure = False
        self._worker = FFmpegWorker(self)
        self._worker.set_command(cmd)
        if self._source_type == "window" and self._source_path.startswith("hwnd:"):
            hwnd = int(self._source_path.split(":")[1])
            fps = int(framerate or "30")
            self._worker.set_window_capture(hwnd, fps)
        elif self._source_type == "screen" and self._source_path.startswith("offset:"):
            parts = self._source_path.split(":", 1)[1].split(",")
            if len(parts) == 4:
                ox, oy = int(parts[0]), int(parts[1])
                ow, oh = int(parts[2]), int(parts[3])
                fps = int(framerate or "30")
                self._worker.set_screen_capture(ox, oy, ow, oh, fps)

        self._worker.status_changed.connect(self._on_worker_status)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.progress_info.connect(self._on_worker_progress)
        self._worker.stopped.connect(self._on_worker_stopped)
        self._worker.preview_closed.connect(self._on_preview_closed)
        self._worker.start()

        logger.info(
            "推流启动: ch={} url={} source={}/{}",
            self._channel_index, rtsp_url, self._source_type, self._source_path
        )
        self._report_status(f"通道 {self._channel_index + 1} 开始推流")
        self._set_state(StreamState.STARTING)

    def toggle_preview(self):
        if not self.is_streaming or not self._worker:
            return
        if self._preview:
            self._worker.stop_preview_now()
            self._preview = False
            self._card.set_preview_active(False)
        else:
            self._worker.start_preview_now(self._rtsp_url)
            self._preview = True
            self._card.set_preview_active(True)

    def _on_preview_closed(self):
        self._preview = False
        self._card.set_preview_active(False)

    def stop_stream(self):
        self._stop_requested = True
        if self._reconnect_timer.isActive():
            self._cancel_reconnect()
            return
        if self._worker:
            logger.info("推流停止: ch={}", self._channel_index)
            self._report_status(f"通道 {self._channel_index + 1} 停止推流")
            self._set_state(StreamState.STOPPING)
            self._worker.stop()
        else:
            self._set_state(StreamState.IDLE)

    def force_stop(self):
        self._stop_requested = True
        self._cancel_reconnect(reset_state=False)
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        self._worker = None
        self._set_state(StreamState.IDLE)

    def _on_worker_status(self, status: str):
        self._card.set_status(status, self._state.value)
        if status == "推流中":
            self._source_retry_count = 0
            self._server_retry_count = 0
            self._reconnect_reason = None
            self._report_status(f"通道 {self._channel_index + 1} 正在推流")
            self._set_state(StreamState.STREAMING)

    def _on_worker_error(self, msg: str):
        if self._handled_worker_failure or self._stop_requested:
            return

        friendly = friendly_error(msg)
        self._last_error = friendly
        reason = self._classify_reconnect_reason(msg)
        if reason and self._schedule_reconnect(reason, friendly):
            self._handled_worker_failure = True
            return

        self._handled_worker_failure = True
        logger.error("推流错误 ch={}: {}", self._channel_index, friendly)
        self._report_status(f"通道 {self._channel_index + 1} 推流错误：{friendly.splitlines()[0]}")
        self._card.set_status("错误", "error")
        self._set_state(StreamState.ERROR)
        self._card.show_error(friendly)

    def _on_worker_progress(self, info: dict):
        pass

    def _on_worker_stopped(self):
        self._preview = False
        self._card.set_preview_active(False)
        self._worker = None
        if self._reconnect_timer.isActive():
            self._set_state(StreamState.RECONNECTING)
            return
        if self._stop_requested:
            self._set_state(StreamState.IDLE)
            return
        if not self._handled_worker_failure:
            reason = self._default_reconnect_reason_for_stop()
            if reason and self._schedule_reconnect(reason, "推流进程意外停止"):
                return
        if self._state != StreamState.ERROR:
            self._set_state(StreamState.IDLE)

    def _attempt_reconnect(self):
        if self._stop_requested:
            self._cancel_reconnect()
            return
        logger.warning("执行重连 ch={} reason={}", self._channel_index, self._reconnect_reason)
        self._report_status(f"通道 {self._channel_index + 1} 正在执行重连")
        self._start_stream_impl(preflight=False)

    def _schedule_reconnect(self, reason: str, friendly: str) -> bool:
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
        self._report_status(f"通道 {self._channel_index + 1} {status}")
        self._set_state(StreamState.RECONNECTING, status)
        self._reconnect_timer.start(interval * 1000)
        return True

    def _cancel_reconnect(self, reset_state: bool = True):
        self._reconnect_timer.stop()
        self._reconnect_reason = None
        if reset_state:
            self._set_state(StreamState.IDLE)

    def _classify_reconnect_reason(self, msg: str) -> str | None:
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

        if self._source_type in ("camera", "screen", "window"):
            if any(k in lower for k in SERVER_ERROR_KEYWORDS):
                return "server"
            return "source"

        return None

    def _default_reconnect_reason_for_stop(self) -> str | None:
        if self._source_type == "rtsp":
            return "source"
        if self._source_type in ("camera", "screen", "window"):
            return "source"
        return None

    def _set_state(self, state: StreamState, text_override: str | None = None):
        self._state = state
        active_states = (
            StreamState.STARTING, StreamState.STREAMING,
            StreamState.RECONNECTING, StreamState.STOPPING,
        )
        self._card.set_buttons_streaming(state in active_states)
        self._card.set_config_locked(state in active_states)

        state_text_map = {
            StreamState.IDLE: "就绪",
            StreamState.STARTING: "启动中...",
            StreamState.STREAMING: "推流中",
            StreamState.RECONNECTING: "重连中...",
            StreamState.STOPPING: "停止中...",
            StreamState.ERROR: "错误",
        }
        self._card.set_status(text_override or state_text_map.get(state, ""), state.value)
        self.state_changed.emit(state)

    @property
    def is_streaming(self) -> bool:
        return self._state in (
            StreamState.STARTING,
            StreamState.STREAMING,
            StreamState.RECONNECTING,
            StreamState.STOPPING,
        )

    @property
    def channel_index(self) -> int:
        return self._channel_index

    @property
    def card(self) -> StreamCardView:
        return self._card

    def to_config(self) -> StreamConfig:
        codec = self._video_codec if self._video_codec != "自动" else ""
        return StreamConfig(
            name=self._stream_name,
            title=self._title,
            source_type=self._source_type,
            source_path=self._source_path,
            loop=self._loop,
            preview=False,
            video_codec=codec,
            width=self._width,
            height=self._height,
            framerate=self._framerate,
            bitrate=self._bitrate,
            auto_start=self.is_streaming,
            source_reconnect_interval=self._source_reconnect_interval,
            source_reconnect_max_attempts=self._source_reconnect_max_attempts,
        )

    def from_config(self, cfg: StreamConfig):
        card = self._card
        if cfg.title:
            card.set_title(cfg.title)
        card.set_source_type(cfg.source_type)

        self._source_type = cfg.source_type
        self._source_path = cfg.source_path
        self._stream_name = cfg.name
        self._title = cfg.title
        self._loop = cfg.loop
        self._video_codec = cfg.video_codec if cfg.video_codec else "自动"
        self._width = cfg.width
        self._height = cfg.height
        self._framerate = cfg.framerate
        self._bitrate = cfg.bitrate
        self._source_reconnect_interval = cfg.source_reconnect_interval
        self._source_reconnect_max_attempts = cfg.source_reconnect_max_attempts

        card.set_source_path(cfg.source_path)
        card.set_stream_name(cfg.name)
        card.set_loop(cfg.loop)
        card.set_codec(self._video_codec)
        card.set_width(cfg.width)
        card.set_height(cfg.height)
        card.set_fps(cfg.framerate)
        card.set_bitrate(cfg.bitrate)
        card.set_source_reconnect_interval(cfg.source_reconnect_interval)
        card.set_source_reconnect_max_attempts(cfg.source_reconnect_max_attempts)

        has_advanced = any([
            cfg.video_codec, cfg.width, cfg.height,
            cfg.framerate, cfg.bitrate,
            cfg.source_reconnect_interval != 5,
            cfg.source_reconnect_max_attempts != 0,
        ])
        card.set_advanced_mode(has_advanced)

    @staticmethod
    def _parse_positive_int(value: str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _parse_non_negative_int(value: str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default

    @staticmethod
    def _format_retry_status(label: str, interval: int, attempt: int) -> str:
        return f"{label}，{interval} 秒后重连（第 {attempt} 次）"

    def _report_status(self, message: str):
        if self._status_reporter:
            self._status_reporter(message)

    @staticmethod
    def _should_stop_retrying(retry_count: int, max_attempts: int) -> bool:
        """是否达到重试上限；``max_attempts=0`` 表示无限重试。"""
        return max_attempts > 0 and retry_count >= max_attempts
