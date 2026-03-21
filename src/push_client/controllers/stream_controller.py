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

from PySide6.QtCore import QObject, Signal

from ..models.stream_model import StreamState
from ..models.config import StreamConfig
from ..services.ffmpeg_service import (
    FFmpegWorker, build_ffmpeg_command, friendly_error,
)
from ..services.device_service import probe_video_info, get_screen_refresh_rate
from ..views.stream_card import StreamCardView


class StreamController(QObject):
    """单路推流通道控制器。

    在 MVC 架构中，StreamController 是连接 StreamCardView（View）
    和推流业务逻辑的桥梁。

    Signals:
        state_changed(StreamState): 推流状态变化时发出，供上层监听。

    Args:
        card: 关联的卡片 UI 控件。
        channel_index: 通道索引。
        rtsp_server_getter: 返回当前 RTSP 服务器地址的回调。
        client_id_getter: 返回当前客户端 ID 的回调。
        parent: 父 QObject。
    """

    state_changed = Signal(object)  # StreamState

    def __init__(
        self,
        card: StreamCardView,
        channel_index: int,
        rtsp_server_getter: callable,
        client_id_getter: callable = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._card = card
        self._channel_index = channel_index
        self._rtsp_server_getter = rtsp_server_getter
        self._client_id_getter = client_id_getter
        self._worker: FFmpegWorker | None = None
        self._state = StreamState.IDLE

        # ── 内部数据（与 UI 同步）──
        self._source_type = ""
        self._source_path = ""
        self._stream_name = ""
        self._loop = False
        self._preview = False
        self._video_codec = ""
        self._width = ""
        self._height = ""
        self._framerate = ""
        self._bitrate = ""

        self._connect_card_signals()

    # ==================================================================
    #  信号连接：View → Controller
    # ==================================================================

    def _connect_card_signals(self):
        """监听卡片 UI 的所有交互信号。"""
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
        c.loop_toggled.connect(self._on_loop)
        c.preview_toggled.connect(self._on_preview)

    # ── 数据同步回调 ──

    def _on_source_type(self, key: str):
        self._source_type = key
        # 切换源类型时清空之前选择的路径，防止残留
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

    def _on_loop(self, val: bool):
        self._loop = val

    def _on_preview(self, val: bool):
        self._preview = val

    # ==================================================================
    #  推流控制
    # ==================================================================

    def start_stream(self):
        """校验参数并启动推流。

        校验失败时通过卡片弹窗提示用户。
        包含对不同源类型的格式校验。
        """
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

        # 获取客户端 ID
        client_id = self._client_id_getter() if self._client_id_getter else ""
        if not client_id:
            self._card.show_error("请先配置客户端 ID")
            return

        # ── 源类型格式校验 ──
        if self._source_type == "rtsp" and not self._source_path.startswith("rtsp://"):
            self._card.show_error("RTSP 地址格式不正确，应以 rtsp:// 开头")
            return
        if self._source_type == "video":
            import os
            if not os.path.isfile(self._source_path):
                self._card.show_error("视频文件不存在，请检查路径")
                return

        rtsp_url = f"{rtsp_server.rstrip('/')}/{client_id}/{self._stream_name}"
        codec = self._video_codec if self._video_codec != "自动" else ""

        # ── 根据视频源类型解析默认参数 ──
        width = self._width
        height = self._height
        framerate = self._framerate
        bitrate = self._bitrate

        if self._source_type == "screen":
            if not codec:
                codec = "libx264"
            if self._source_path.startswith("offset:"):
                parts = self._source_path.split(":", 1)[1].split(",")
                if len(parts) == 4:
                    # 管道模式: 尺寸在 rawvideo 输入参数中指定，不设 width/height
                    if not framerate:
                        framerate = str(get_screen_refresh_rate(
                            int(parts[0]), int(parts[1])
                        ))
        elif self._source_type == "window":
            if not codec:
                codec = "libx264"
            # 管道模式: 尺寸在 rawvideo 输入参数中指定，不设 width/height
            if not framerate:
                framerate = "30"
        elif self._source_type == "camera":
            if not codec:
                codec = "libx264"
        elif self._source_type == "video":
            info = probe_video_info(self._source_path)
            if not codec and info.get("codec"):
                codec = "copy"
            # copy 模式不需要 width/height（不能使用滤镜）
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
        elif self._source_type == "rtsp":
            if not codec:
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

        # 构建 Worker 并启动
        self._worker = FFmpegWorker(self)
        self._worker.set_command(cmd)
        if self._preview:
            self._worker.set_preview(True, rtsp_url)
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

        # Worker 信号 → Controller → View
        self._worker.status_changed.connect(self._on_worker_status)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.progress_info.connect(self._on_worker_progress)
        self._worker.stopped.connect(self._on_worker_stopped)
        self._worker.start()

        self._set_state(StreamState.STARTING)

    def stop_stream(self):
        """请求停止推流。"""
        if self._worker:
            self._set_state(StreamState.STOPPING)
            self._worker.stop()

    def force_stop(self):
        """强制停止推流（应用退出时调用，阻塞等待线程结束）。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)

    # ==================================================================
    #  Worker 信号处理
    # ==================================================================

    def _on_worker_status(self, status: str):
        """FFmpeg 状态变化。"""
        self._card.set_status(status, self._state.value)
        if status == "推流中":
            self._set_state(StreamState.STREAMING)
        elif status == "已停止":
            self._set_state(StreamState.IDLE)

    def _on_worker_error(self, msg: str):
        """FFmpeg 报错。"""
        friendly = friendly_error(msg)
        self._card.set_status("错误", "error")
        self._set_state(StreamState.ERROR)
        self._card.show_error(friendly)

    def _on_worker_progress(self, info: dict):
        """FFmpeg 推流进度更新。"""
        parts = []
        if "time" in info:
            parts.append(f"时间:{info['time']}")
        if "fps" in info:
            parts.append(f"帧率:{info['fps']}")
        if "bitrate" in info:
            parts.append(f"码率:{info['bitrate']}")
        if "speed" in info:
            parts.append(f"速度:{info['speed']}")
        if "frame" in info:
            parts.append(f"帧:{info['frame']}")
        self._card.set_progress("  ".join(parts))

    def _on_worker_stopped(self):
        """FFmpeg 进程已结束。"""
        self._card.set_progress("")
        self._set_state(StreamState.IDLE)

    # ==================================================================
    #  状态管理
    # ==================================================================

    def _set_state(self, state: StreamState):
        """更新推流状态并同步到卡片 UI。"""
        self._state = state
        is_streaming = state in (StreamState.STARTING, StreamState.STREAMING)
        self._card.set_buttons_streaming(is_streaming)
        # 推流中锁定配置项，停止后解锁
        self._card.set_config_locked(is_streaming)

        state_text_map = {
            StreamState.IDLE: "就绪",
            StreamState.STARTING: "启动中...",
            StreamState.STREAMING: "推流中",
            StreamState.STOPPING: "停止中...",
            StreamState.ERROR: "错误",
        }
        self._card.set_status(state_text_map.get(state, ""), state.value)
        self.state_changed.emit(state)

    @property
    def is_streaming(self) -> bool:
        """当前是否正在推流。"""
        return self._state in (StreamState.STARTING, StreamState.STREAMING)

    @property
    def channel_index(self) -> int:
        """通道编号。"""
        return self._channel_index

    @property
    def card(self) -> StreamCardView:
        """关联的卡片视图。"""
        return self._card

    # ==================================================================
    #  配置序列化
    # ==================================================================

    def to_config(self) -> StreamConfig:
        """将当前通道参数导出为配置对象。"""
        codec = self._video_codec if self._video_codec != "自动" else ""
        return StreamConfig(
            name=self._stream_name,
            source_type=self._source_type,
            source_path=self._source_path,
            loop=self._loop,
            preview=self._preview,
            video_codec=codec,
            width=self._width,
            height=self._height,
            framerate=self._framerate,
            bitrate=self._bitrate,
            auto_start=self.is_streaming,
        )

    def from_config(self, cfg: StreamConfig):
        """从配置对象恢复通道参数（同步到 UI）。"""
        self._source_type = cfg.source_type
        self._source_path = cfg.source_path
        self._stream_name = cfg.name
        self._loop = cfg.loop
        self._preview = cfg.preview
        self._video_codec = cfg.video_codec if cfg.video_codec else "自动"
        self._width = cfg.width
        self._height = cfg.height
        self._framerate = cfg.framerate
        self._bitrate = cfg.bitrate

        # 同步到 View
        card = self._card
        card.set_source_type(cfg.source_type)
        card.set_source_path(cfg.source_path)
        card.set_stream_name(cfg.name)
        card.set_loop(cfg.loop)
        card.set_preview(cfg.preview)
        card.set_codec(self._video_codec)
        card.set_width(cfg.width)
        card.set_height(cfg.height)
        card.set_fps(cfg.framerate)
        card.set_bitrate(cfg.bitrate)
