"""stream_controller 模块单元测试

使用 mock 替代真实的 Qt 控件和 FFmpeg 进程。
"""

from unittest import mock
import pytest

from push_client.controllers.stream_controller import StreamController
from push_client.models.stream_model import StreamState
from push_client.models.config import StreamConfig


def _make_mock_card():
    """创建模拟的 StreamCardView"""
    card = mock.MagicMock()
    card.get_source_type.return_value = ""
    card.get_source_path.return_value = ""
    card.get_stream_name.return_value = ""
    # 信号 mock
    for sig in [
        "source_type_changed", "source_path_edited", "device_selected",
        "browse_clicked", "refresh_clicked", "start_clicked", "stop_clicked",
        "remove_clicked", "stream_name_edited", "codec_changed",
        "width_edited", "height_edited", "fps_edited", "bitrate_edited",
        "loop_toggled", "preview_toggled",
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
            "push_client.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "push_client.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "push_client.controllers.stream_controller.probe_video_info",
            return_value={},
        ):
            mock_build.return_value = ["ffmpeg", "-i", "test"]
            ctrl.start_stream()
            # 验证 build_ffmpeg_command 被调用时的 rtsp_url 参数
            if mock_build.called:
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
            "push_client.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "push_client.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "push_client.controllers.stream_controller.get_screen_refresh_rate",
            return_value=60,
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            if mock_build.called:
                _, kwargs = mock_build.call_args
                assert kwargs["video_codec"] == "libx264"
                assert kwargs["width"] == "1920"
                assert kwargs["height"] == "1080"
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
            "push_client.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "push_client.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "push_client.controllers.stream_controller.probe_video_info",
            return_value={"width": 1920, "height": 1080, "codec": "h264", "framerate": "30/1"},
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            if mock_build.called:
                _, kwargs = mock_build.call_args
                assert kwargs["video_codec"] == "copy"
                assert kwargs["width"] == "1920"
                assert kwargs["height"] == "1080"
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
            "push_client.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "push_client.controllers.stream_controller.FFmpegWorker"
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            if mock_build.called:
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
            "push_client.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "push_client.controllers.stream_controller.FFmpegWorker"
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            if mock_build.called:
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
            "push_client.controllers.stream_controller.build_ffmpeg_command"
        ) as mock_build, mock.patch(
            "push_client.controllers.stream_controller.FFmpegWorker"
        ), mock.patch(
            "push_client.controllers.stream_controller.get_screen_refresh_rate",
            return_value=60,
        ):
            mock_build.return_value = ["ffmpeg"]
            ctrl.start_stream()
            if mock_build.called:
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
        card.set_stream_name.assert_called_with("restored")
        card.set_source_type.assert_called_with("screen")


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

    def test_channel_index(self):
        card = _make_mock_card()
        ctrl = StreamController(
            card=card,
            channel_index=5,
            rtsp_server_getter=lambda: "",
            client_id_getter=lambda: "",
        )
        assert ctrl.channel_index == 5
