"""stream_controller 模块单元测试

使用 mock 替代真实的 Qt 控件和 FFmpeg 进程。
"""

from unittest import mock
import pytest

from beaverpush.controllers.stream_controller import StreamController
from beaverpush.models.stream_model import StreamState
from beaverpush.models.config import StreamConfig


@pytest.fixture(autouse=True)
def _mock_reachability():
    with mock.patch(
        "beaverpush.controllers.stream_controller.check_rtsp_server_reachable",
        return_value=(True, "ok"),
    ), mock.patch(
        "beaverpush.controllers.stream_controller.check_rtsp_reachable",
        return_value=(True, "ok"),
    ):
        yield


def _make_mock_card():
    """创建模拟的 StreamCardView"""
    card = mock.MagicMock()
    card.get_source_type.return_value = "video"
    card.get_source_path.return_value = ""
    card.get_stream_name.return_value = ""
    # 信号 mock
    for sig in [
        "source_type_changed", "source_path_edited", "device_selected",
        "browse_clicked", "refresh_clicked", "start_clicked", "stop_clicked",
        "remove_clicked", "stream_name_edited", "codec_changed",
        "width_edited", "height_edited", "fps_edited", "bitrate_edited",
        "source_reconnect_interval_edited", "source_reconnect_max_attempts_edited",
        "loop_toggled", "preview_clicked", "title_edited",
    ]:
        getattr(card, sig).connect = mock.MagicMock()
    return card


class TestStreamControllerUrlConstruction:
    """验证推流 URL 使用 client_id/stream_name 格式"""

    def test_url_format_with_client_id(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "client01",
        )
        # 设置内部状态
        ctrl._source_type = "video"
        ctrl._source_path = __file__  # 用一个存在的文件
        ctrl._stream_name = "stream1"
        ctrl._video_codec = "libx264"

        with mock.patch(
            "beaverpush.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "beaverpush.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "beaverpush.controllers.stream_controller.probe_video_info",
            return_value={},
        ):
            mock_build.return_value = ["ffmpeg", "-i", "test"]
            ctrl.start_stream()
            # 验证 build_ffmpeg_command 被调用时的 rtsp_url 参数
            mock_build.assert_called_once()
            _, kwargs = mock_build.call_args
            assert kwargs["rtsp_url"] == "rtsp://localhost:8554/client01/stream1"

    def test_missing_client_id_shows_error(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "",
        )
        ctrl._source_type = "video"
        ctrl._source_path = "/test.mp4"
        ctrl._stream_name = "stream1"

        ctrl.start_stream()
        card.show_error.assert_called_with("请先配置客户端 ID")

    def test_missing_server_shows_error(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "",
            client_id_getter=lambda: "client01",
        )
        ctrl._source_type = "video"
        ctrl._source_path = "/test.mp4"
        ctrl._stream_name = "stream1"

        ctrl.start_stream()
        card.show_error.assert_called_with("请先配置 RTSP 服务器地址")

    def test_missing_stream_name_shows_error(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "client01",
        )
        ctrl._source_type = "video"
        ctrl._source_path = "/test.mp4"
        ctrl._stream_name = ""

        ctrl.start_stream()
        card.show_error.assert_called_with("请输入流名称")


class TestStreamControllerSourceDefaults:
    """验证各源类型的参数默认值逻辑"""

    def test_screen_defaults_codec_to_libx264(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "screen"
        ctrl._source_path = "offset:0,0,1920,1080"
        ctrl._stream_name = "s1"
        ctrl._video_codec = "自动"  # 空/自动

        with mock.patch(
            "beaverpush.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "beaverpush.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "beaverpush.controllers.stream_controller.get_screen_refresh_rate",
            return_value=60,
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            mock_build.assert_called_once()
            _, kwargs = mock_build.call_args
            assert kwargs["video_codec"] == "libx264"
            # 管道模式不设置 width/height（尺寸在 rawvideo 参数中指定）
            assert kwargs["width"] == ""
            assert kwargs["height"] == ""
            assert kwargs["framerate"] == "60"

    def test_video_defaults_codec_to_copy(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "video"
        ctrl._source_path = __file__
        ctrl._stream_name = "s1"
        ctrl._video_codec = "自动"

        with mock.patch(
            "beaverpush.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "beaverpush.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "beaverpush.controllers.stream_controller.probe_video_info",
            return_value={"width": 1920, "height": 1080, "codec": "h264", "framerate": "30/1"},
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            mock_build.assert_called_once()
            _, kwargs = mock_build.call_args
            assert kwargs["video_codec"] == "copy"
            # copy 模式不设置 width/height（不能使用滤镜）
            assert kwargs["width"] == ""
            assert kwargs["height"] == ""
            assert kwargs["framerate"] == "30"

    def test_rtsp_defaults_codec_to_copy(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "rtsp"
        ctrl._source_path = "rtsp://source:554/live"
        ctrl._stream_name = "s1"
        ctrl._video_codec = "自动"

        with mock.patch(
            "beaverpush.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "beaverpush.controllers.stream_controller.FFmpegWorker"
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            mock_build.assert_called_once()
            _, kwargs = mock_build.call_args
            assert kwargs["video_codec"] == "copy"

    def test_camera_defaults_codec_to_libx264(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "camera"
        ctrl._source_path = "USB Camera"
        ctrl._stream_name = "s1"
        ctrl._video_codec = "自动"

        with mock.patch(
            "beaverpush.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "beaverpush.controllers.stream_controller.FFmpegWorker"
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            mock_build.assert_called_once()
            _, kwargs = mock_build.call_args
            assert kwargs["video_codec"] == "libx264"

    def test_user_override_takes_precedence(self):
        """用户显式设置的参数优先于源默认值"""
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "screen"
        ctrl._source_path = "offset:0,0,1920,1080"
        ctrl._stream_name = "s1"
        ctrl._video_codec = "h264_nvenc"  # 用户显式指定
        ctrl._width = "1280"
        ctrl._height = "720"
        ctrl._framerate = "24"

        with mock.patch(
            "beaverpush.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "beaverpush.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "beaverpush.controllers.stream_controller.get_screen_refresh_rate",
            return_value=60,
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            mock_build.assert_called_once()
            _, kwargs = mock_build.call_args
            assert kwargs["video_codec"] == "h264_nvenc"
            assert kwargs["width"] == "1280"
            assert kwargs["height"] == "720"
            assert kwargs["framerate"] == "24"


class TestStreamControllerConfig:
    """验证配置序列化/反序列化"""

    def test_to_config(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "video"
        ctrl._source_path = "/test.mp4"
        ctrl._stream_name = "s1"
        ctrl._video_codec = "libx264"
        ctrl._width = "1920"
        ctrl._height = "1080"

        cfg = ctrl.to_config()
        assert cfg.name == "s1"
        assert cfg.source_type == "video"
        assert cfg.video_codec == "libx264"
        assert cfg.width == "1920"

    def test_to_config_auto_codec_becomes_empty(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._video_codec = "自动"
        cfg = ctrl.to_config()
        assert cfg.video_codec == ""

    def test_to_config_includes_reconnect_settings(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_reconnect_interval = 9
        ctrl._source_reconnect_max_attempts = 0

        cfg = ctrl.to_config()
        assert cfg.source_reconnect_interval == 9
        assert cfg.source_reconnect_max_attempts == 0

    def test_from_config(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        cfg = StreamConfig(
            name="restored",
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            video_codec="libx265",
            width="1920",
            height="1080",
        )
        ctrl.from_config(cfg)
        assert ctrl._stream_name == "restored"
        assert ctrl._source_type == "screen"
        assert ctrl._video_codec == "libx265"
        assert ctrl._source_reconnect_interval == 5
        assert ctrl._source_reconnect_max_attempts == 3
        card.set_stream_name.assert_called_with("restored")
        card.set_source_type.assert_called_with("screen")

    def test_from_config_restores_reconnect_settings(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        cfg = StreamConfig(
            name="restored",
            source_type="rtsp",
            source_path="rtsp://source/live",
            source_reconnect_interval=11,
            source_reconnect_max_attempts=0,
        )

        ctrl.from_config(cfg)
        assert ctrl._source_reconnect_interval == 11
        assert ctrl._source_reconnect_max_attempts == 0
        card.set_source_reconnect_interval.assert_called_with(11)
        card.set_source_reconnect_max_attempts.assert_called_with(0)


class TestStreamControllerState:
    def test_initial_state_is_idle(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "",
            client_id_getter=lambda: "",
        )
        assert ctrl._state == StreamState.IDLE
        assert not ctrl.is_streaming

    def test_initial_source_type_from_card(self):
        """新建卡片时 source_type 应取自 card 默认值"""
        card = _make_mock_card()
        card.get_source_type.return_value = "video"
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "",
            client_id_getter=lambda: "",
        )
        assert ctrl._source_type == "video"

    def test_from_config_shows_advanced_when_has_params(self):
        """加载有高级参数的配置时自动展开高级面板"""
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "", client_id_getter=lambda: "c1",
        )
        cfg = StreamConfig(name="s1", video_codec="libx264", width="1920")
        ctrl.from_config(cfg)
        card.set_advanced_mode.assert_called_with(True)

    def test_from_config_hides_advanced_when_no_params(self):
        """加载无高级参数的配置时不展开高级面板"""
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "", client_id_getter=lambda: "c1",
        )
        cfg = StreamConfig(name="s1", source_type="video")
        ctrl.from_config(cfg)
        card.set_advanced_mode.assert_called_with(False)

    def test_channel_index(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=5,
            rtsp_server_getter=lambda: "",
            client_id_getter=lambda: "",
        )
        assert ctrl.channel_index == 5


class TestProgressSuppression:
    """所有模式都不显示进度信息"""

    def test_progress_not_forwarded_to_card(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "", client_id_getter=lambda: "",
        )
        ctrl._source_type = "video"
        ctrl._on_worker_progress({"time": "00:01:00", "fps": "30", "speed": "1x"})
        # set_progress should never be called (method removed from card)
        card.set_progress.assert_not_called()

    def test_screen_progress_also_suppressed(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "", client_id_getter=lambda: "",
        )
        ctrl._source_type = "screen"
        ctrl._on_worker_progress({"time": "00:01:00", "fps": "30"})
        # No exception should be raised


class TestReconnectBehavior:
    def test_initial_rtsp_source_failure_shows_error_without_reconnect(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "rtsp"
        ctrl._source_path = "rtsp://source/live"
        ctrl._stream_name = "s1"

        with mock.patch(
            "beaverpush.controllers.stream_controller.check_rtsp_reachable",
            return_value=(False, "连接超时"),
        ):
            ctrl.start_stream()

        card.show_error.assert_called_with("RTSP 源不可用：连接超时")
        assert not ctrl._reconnect_timer.isActive()

    def test_initial_server_failure_shows_error_without_reconnect(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "video"
        ctrl._source_path = __file__
        ctrl._stream_name = "s1"

        with mock.patch(
            "beaverpush.controllers.stream_controller.check_rtsp_server_reachable",
            return_value=(False, "连接被拒绝，请检查服务器是否启动。"),
        ):
            ctrl.start_stream()

        card.show_error.assert_called_with("连接被拒绝，请检查服务器是否启动。")
        assert not ctrl._reconnect_timer.isActive()

    def test_source_failure_schedules_reconnect(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "camera"
        ctrl._source_reconnect_interval = 7

        with mock.patch.object(ctrl._reconnect_timer, "start") as mock_start:
            ctrl._on_worker_error("Could not open camera device")

        mock_start.assert_called_once_with(7000)
        assert ctrl._state == StreamState.RECONNECTING
        card.show_error.assert_not_called()

    def test_source_failure_stops_after_max_attempts(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "camera"
        ctrl._source_reconnect_max_attempts = 1
        ctrl._source_retry_count = 1

        ctrl._on_worker_error("Could not open camera device")

        assert ctrl._state == StreamState.ERROR
        card.show_error.assert_called_once()

    def test_server_failure_schedules_reconnect_with_global_policy(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
            server_reconnect_interval_getter=lambda: 9,
            server_reconnect_duration_getter=lambda: 60,
        )
        ctrl._source_type = "video"

        with mock.patch.object(ctrl._reconnect_timer, "start") as mock_start:
            ctrl._on_worker_error("Connection refused")

        mock_start.assert_called_once_with(9000)
        assert ctrl._state == StreamState.RECONNECTING

    def test_stop_stream_cancels_pending_reconnect(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "camera"
        ctrl._source_reconnect_interval = 7

        ctrl._on_worker_error("Could not open camera device")
        assert ctrl._reconnect_timer.isActive()

        ctrl.stop_stream()

        assert not ctrl._reconnect_timer.isActive()
        assert ctrl._state == StreamState.IDLE


class TestPreviewToggle:
    """验证预览按钮切换逻辑"""

    def test_toggle_preview_ignored_when_not_streaming(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl.toggle_preview()
        # 不在推流中，不应有任何变化
        card.set_preview_active.assert_not_called()

    def test_toggle_preview_starts_preview(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        worker = mock.MagicMock()
        ctrl._worker = worker
        ctrl._state = StreamState.STREAMING
        ctrl._rtsp_url = "rtsp://localhost:8554/c1/s1"
        ctrl._preview = False

        ctrl.toggle_preview()
        worker.start_preview_now.assert_called_once_with("rtsp://localhost:8554/c1/s1")
        assert ctrl._preview is True
        card.set_preview_active.assert_called_with(True)

    def test_toggle_preview_stops_preview(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        worker = mock.MagicMock()
        ctrl._worker = worker
        ctrl._state = StreamState.STREAMING
        ctrl._preview = True

        ctrl.toggle_preview()
        worker.stop_preview_now.assert_called_once()
        assert ctrl._preview is False
        card.set_preview_active.assert_called_with(False)

    def test_preview_reset_on_worker_stopped(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._preview = True
        ctrl._on_worker_stopped()
        assert ctrl._preview is False
        card.set_preview_active.assert_called_with(False)

    def test_start_stream_stores_rtsp_url(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._source_type = "video"
        ctrl._source_path = __file__
        ctrl._stream_name = "s1"
        ctrl._video_codec = "libx264"

        with mock.patch(
            "beaverpush.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "beaverpush.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "beaverpush.controllers.stream_controller.probe_video_info",
            return_value={},
        ):
            mock_build.return_value = ["ffmpeg", "-i", "test"]
            ctrl.start_stream()
            assert ctrl._rtsp_url == "rtsp://localhost:8554/c1/s1"

    def test_to_config_preview_always_false(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "",
            client_id_getter=lambda: "",
        )
        ctrl._preview = True  # 运行时preview开启
        cfg = ctrl.to_config()
        assert cfg.preview is False

    def test_preview_closed_resets_button(self):
        """ffplay 预览窗口被用户关闭时，按钮应重置为预览状态"""
        card = _make_mock_card()
        ctrl = StreamController(
            card=card, channel_index=0,
            rtsp_server_getter=lambda: "rtsp://localhost:8554",
            client_id_getter=lambda: "c1",
        )
        ctrl._preview = True
        ctrl._on_preview_closed()
        assert ctrl._preview is False
        card.set_preview_active.assert_called_with(False)
