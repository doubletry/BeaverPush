"""ffmpeg_service 模块单元测试"""

import subprocess
from unittest import mock

from push_client.services.ffmpeg_service import (
    build_ffmpeg_command, friendly_error, _make_even, FFmpegWorker,
)


class TestMakeEven:
    def test_even_unchanged(self):
        assert _make_even(1920) == 1920

    def test_odd_incremented(self):
        assert _make_even(1921) == 1922

    def test_zero(self):
        assert _make_even(0) == 0


class TestBuildFfmpegCommandScreen:
    """屏幕捕获使用 rawvideo 管道模式（替代 gdigrab）"""

    def test_screen_uses_rawvideo_pipe(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/client1/stream1",
            framerate="30",
        )
        assert "-f" in cmd
        idx = cmd.index("-f")
        # 第一个 -f 应该是 rawvideo（不是 gdigrab）
        assert cmd[idx + 1] == "rawvideo"
        assert "-pixel_format" in cmd
        assert "bgra" in cmd
        assert "pipe:0" in cmd
        # 不应包含 gdigrab
        assert "gdigrab" not in cmd
        assert "desktop" not in cmd

    def test_screen_dimensions_are_even(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:100,200,1921,1081",
            rtsp_url="rtsp://localhost:8554/c/s",
        )
        video_size_idx = cmd.index("-video_size")
        size = cmd[video_size_idx + 1]
        w, h = size.split("x")
        assert int(w) % 2 == 0
        assert int(h) % 2 == 0

    def test_screen_default_framerate(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
            framerate="",  # 空 → 默认 30
        )
        fr_idx = cmd.index("-framerate")
        assert cmd[fr_idx + 1] == "30"

    def test_screen_custom_framerate(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
            framerate="60",
        )
        fr_idx = cmd.index("-framerate")
        assert cmd[fr_idx + 1] == "60"

    def test_screen_invalid_source_path_raises(self):
        import pytest
        with pytest.raises(ValueError, match="屏幕捕获源路径格式错误"):
            build_ffmpeg_command(
                source_type="screen",
                source_path="something_wrong",
                rtsp_url="rtsp://localhost:8554/c/s",
            )


class TestBuildFfmpegCommandWindow:
    def test_window_uses_rawvideo_pipe(self):
        with mock.patch(
            "push_client.services.ffmpeg_service.get_window_rect",
            return_value=(0, 0, 800, 600),
        ):
            cmd = build_ffmpeg_command(
                source_type="window",
                source_path="hwnd:12345",
                rtsp_url="rtsp://localhost:8554/c/s",
            )
        assert "rawvideo" in cmd
        assert "pipe:0" in cmd


class TestBuildFfmpegCommandVideo:
    def test_video_with_loop(self):
        cmd = build_ffmpeg_command(
            source_type="video",
            source_path="/test.mp4",
            rtsp_url="rtsp://localhost:8554/c/s",
            loop=True,
        )
        assert "-stream_loop" in cmd
        assert "-1" in cmd

    def test_video_with_codec_copy(self):
        cmd = build_ffmpeg_command(
            source_type="video",
            source_path="/test.mp4",
            rtsp_url="rtsp://localhost:8554/c/s",
            video_codec="copy",
        )
        assert "-c:v" in cmd
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "copy"

    def test_video_copy_no_scale_filter(self):
        """copy 模式不应产生 -vf scale 滤镜"""
        cmd = build_ffmpeg_command(
            source_type="video",
            source_path="/test.mp4",
            rtsp_url="rtsp://localhost:8554/c/s",
            video_codec="copy",
            width="1920",
            height="1080",
        )
        assert "-vf" not in cmd

    def test_video_copy_no_pix_fmt(self):
        """copy 模式不应产生 -pix_fmt"""
        cmd = build_ffmpeg_command(
            source_type="video",
            source_path="/test.mp4",
            rtsp_url="rtsp://localhost:8554/c/s",
            video_codec="copy",
        )
        assert "-pix_fmt" not in cmd

    def test_video_with_scale(self):
        cmd = build_ffmpeg_command(
            source_type="video",
            source_path="/test.mp4",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="1280",
            height="720",
        )
        assert "-vf" in cmd
        vf_idx = cmd.index("-vf")
        assert "scale=" in cmd[vf_idx + 1]


class TestBuildFfmpegCommandRtsp:
    def test_rtsp_source(self):
        cmd = build_ffmpeg_command(
            source_type="rtsp",
            source_path="rtsp://source:554/live",
            rtsp_url="rtsp://localhost:8554/c/s",
            video_codec="copy",
        )
        assert "-rtsp_transport" in cmd
        assert "tcp" in cmd


class TestBuildFfmpegCommandCamera:
    def test_camera_source(self):
        cmd = build_ffmpeg_command(
            source_type="camera",
            source_path="USB Camera",
            rtsp_url="rtsp://localhost:8554/c/s",
            framerate="30",
        )
        assert "dshow" in cmd
        assert "video=USB Camera" in cmd


class TestBuildFfmpegCommandUnsupported:
    def test_unsupported_type_raises(self):
        import pytest
        with pytest.raises(ValueError):
            build_ffmpeg_command(
                source_type="unknown",
                source_path="",
                rtsp_url="rtsp://localhost:8554/c/s",
            )


class TestBuildFfmpegCommandScreenNoFilter:
    """screen/window 管道源不应产生 scale 滤镜"""

    def test_screen_no_scale_filter(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="1920",
            height="1080",
        )
        assert "-vf" not in cmd

    def test_window_no_scale_filter(self):
        with mock.patch(
            "push_client.services.ffmpeg_service.get_window_rect",
            return_value=(0, 0, 800, 600),
        ):
            cmd = build_ffmpeg_command(
                source_type="window",
                source_path="hwnd:12345",
                rtsp_url="rtsp://localhost:8554/c/s",
                width="800",
                height="600",
            )
        assert "-vf" not in cmd


class TestBuildFfmpegCommandEncoding:
    def test_screen_defaults_to_libx264(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
        )
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "libx264"
        assert "ultrafast" in cmd
        assert "zerolatency" in cmd

    def test_custom_codec(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
            video_codec="h264_nvenc",
        )
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "h264_nvenc"

    def test_bitrate_applied(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
            bitrate="4M",
        )
        bv_idx = cmd.index("-b:v")
        assert cmd[bv_idx + 1] == "4M"

    def test_output_format_is_rtsp(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
        )
        # URL 是最后一个参数
        assert cmd[-1] == "rtsp://localhost:8554/c/s"
        # 命令中应包含 -f rtsp 和 -rtsp_transport tcp
        assert "-f" in cmd
        f_idx = cmd.index("-f", len(cmd) - 6)  # 最后几个参数中找
        assert cmd[f_idx + 1] == "rtsp"


class TestFFmpegWorkerInit:
    def test_has_screen_capture_method(self):
        worker = FFmpegWorker()
        assert hasattr(worker, "set_screen_capture")
        worker.set_screen_capture(0, 0, 1920, 1080, 30)
        assert worker._screen_w == 1920
        assert worker._screen_h == 1080

    def test_has_window_capture_method(self):
        worker = FFmpegWorker()
        worker.set_window_capture(12345, 30)
        assert worker._window_hwnd == 12345


class TestFriendlyError:
    def test_connection_refused(self):
        result = friendly_error("Connection refused")
        assert "连接被拒绝" in result

    def test_no_such_file(self):
        result = friendly_error("No such file or directory")
        assert "文件不存在" in result

    def test_unknown_error_passthrough(self):
        result = friendly_error("some random message")
        assert result == "some random message"
