"""ffmpeg_service 模块单元测试"""

import subprocess
import time
from unittest import mock

import pytest

from beaverpush.services.ffmpeg_service import (
    build_ffmpeg_command, friendly_error, _make_even, FFmpegWorker,
    check_rtsp_server_reachable, RTSP_TIMEOUT_US,
    _mask_sensitive_cmd,
)
from beaverpush.services import ffmpeg_service as _ffmpeg_service_mod


@pytest.fixture(autouse=True)
def _force_nvenc_new_presets():
    """让 ``_low_latency_encode_args`` 在测试中固定走"新版 ffmpeg"分支
    （``-preset p1 -tune ll``），避免本机 PATH 上恰好是旧版 ffmpeg
    时让针对 nvenc 的断言飘忽不定。需要测旧版 fallback 的用例自行覆写。"""
    original = _ffmpeg_service_mod._NVENC_NEW_PRESETS_CACHE
    _ffmpeg_service_mod._NVENC_NEW_PRESETS_CACHE = True
    try:
        yield
    finally:
        _ffmpeg_service_mod._NVENC_NEW_PRESETS_CACHE = original


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

    def test_screen_uses_wallclock_timestamps(self):
        cmd = build_ffmpeg_command(
            source_type="screen",
            source_path="offset:0,0,1920,1080",
            rtsp_url="rtsp://localhost:8554/c/s",
        )
        assert "-use_wallclock_as_timestamps" in cmd
        idx = cmd.index("-use_wallclock_as_timestamps")
        assert cmd[idx + 1] == "1"
        # wallclock flag 应出现在 -i pipe:0 之前
        pipe_idx = cmd.index("pipe:0")
        assert idx < pipe_idx


class TestBuildFfmpegCommandWindow:
    def test_window_uses_rawvideo_pipe(self):
        with mock.patch(
            "beaverpush.services.ffmpeg_service.get_window_rect",
            return_value=(0, 0, 800, 600),
        ):
            cmd = build_ffmpeg_command(
                source_type="window",
                source_path="hwnd:12345",
                rtsp_url="rtsp://localhost:8554/c/s",
            )
        assert "rawvideo" in cmd
        assert "pipe:0" in cmd

    def test_window_uses_wallclock_timestamps(self):
        with mock.patch(
            "beaverpush.services.ffmpeg_service.get_window_rect",
            return_value=(0, 0, 800, 600),
        ):
            cmd = build_ffmpeg_command(
                source_type="window",
                source_path="hwnd:12345",
                rtsp_url="rtsp://localhost:8554/c/s",
            )
        assert "-use_wallclock_as_timestamps" in cmd
        idx = cmd.index("-use_wallclock_as_timestamps")
        assert cmd[idx + 1] == "1"
        pipe_idx = cmd.index("pipe:0")
        assert idx < pipe_idx


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
        assert "-timeout" in cmd
        timeout_idx = cmd.index("-timeout")
        assert cmd[timeout_idx + 1] == RTSP_TIMEOUT_US


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
        with pytest.raises(ValueError):
            build_ffmpeg_command(
                source_type="unknown",
                source_path="",
                rtsp_url="rtsp://localhost:8554/c/s",
            )


class TestBuildFfmpegCommandHikCamera:
    """海康工业相机使用 rawvideo 管道 + bgr24"""

    def test_hikcamera_uses_rawvideo_bgr24_pipe(self):
        cmd = build_ffmpeg_command(
            source_type="hikcamera",
            source_path="00DA1234567",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="1920",
            height="1080",
            framerate="25",
        )
        # 输入端
        f_idx = cmd.index("-f")
        assert cmd[f_idx + 1] == "rawvideo"
        pf_idx = cmd.index("-pixel_format")
        assert cmd[pf_idx + 1] == "bgr24"
        vs_idx = cmd.index("-video_size")
        assert cmd[vs_idx + 1] == "1920x1080"
        assert "pipe:0" in cmd
        # 输入帧率应来自参数；不应同时再追加 -r 出现两次帧率
        fr_count = cmd.count("-framerate")
        assert fr_count == 1
        assert cmd[cmd.index("-framerate") + 1] == "25"
        assert "-r" not in cmd

    def test_hikcamera_dimensions_are_even(self):
        cmd = build_ffmpeg_command(
            source_type="hikcamera",
            source_path="SN001",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="1281",
            height="721",
        )
        vs_idx = cmd.index("-video_size")
        assert cmd[vs_idx + 1] == "1282x722"

    def test_hikcamera_default_framerate(self):
        cmd = build_ffmpeg_command(
            source_type="hikcamera",
            source_path="SN001",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="1920",
            height="1080",
        )
        assert cmd[cmd.index("-framerate") + 1] == "30"

    def test_hikcamera_requires_dimensions(self):
        with pytest.raises(ValueError):
            build_ffmpeg_command(
                source_type="hikcamera",
                source_path="SN001",
                rtsp_url="rtsp://localhost:8554/c/s",
            )

    def test_hikcamera_default_codec_libx264_with_low_latency(self):
        cmd = build_ffmpeg_command(
            source_type="hikcamera",
            source_path="SN001",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="1920",
            height="1080",
        )
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "libx264"
        assert "ultrafast" in cmd
        assert "zerolatency" in cmd

    def test_hikcamera_custom_hardware_codec(self):
        """硬件加速编码器应被原样透传，并使用各自合法的 -preset。"""
        # 不同编码器对 -preset 的合法取值不同：
        #   libx264/libx265 → ultrafast, zerolatency
        #   h264_nvenc/hevc_nvenc → p1, ll  (NVENC 不接受 ultrafast)
        #   h264_qsv/hevc_qsv → veryfast    (QSV 没有 zerolatency)
        cases = {
            "libx265": ("ultrafast", "zerolatency"),
            "h264_nvenc": ("p1", "ll"),
            "hevc_nvenc": ("p1", "ll"),
            "h264_qsv": ("veryfast", None),
            "hevc_qsv": ("veryfast", None),
        }
        for codec, (preset, tune) in cases.items():
            cmd = build_ffmpeg_command(
                source_type="hikcamera",
                source_path="SN001",
                rtsp_url="rtsp://localhost:8554/c/s",
                video_codec=codec,
                width="1920",
                height="1080",
            )
            cv_idx = cmd.index("-c:v")
            assert cmd[cv_idx + 1] == codec, codec
            assert preset in cmd, f"{codec}: 期望 preset {preset}"
            if tune is None:
                assert "-tune" not in cmd, f"{codec}: 不应包含 -tune"
            else:
                assert tune in cmd, f"{codec}: 期望 tune {tune}"
            # 关键回归：nvenc/qsv 命令不能再误用 libx264 的 ultrafast/zerolatency
            if codec.endswith("_nvenc") or codec.endswith("_qsv"):
                assert "ultrafast" not in cmd, codec
                assert "zerolatency" not in cmd, codec

    def test_hikcamera_no_scale_filter_when_size_set(self):
        """hikcamera 输入尺寸已固定，不应额外插入 scale 滤镜。"""
        cmd = build_ffmpeg_command(
            source_type="hikcamera",
            source_path="SN001",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="1920",
            height="1080",
        )
        assert "-vf" not in cmd

    def test_hikcamera_uses_wallclock_timestamps(self):
        cmd = build_ffmpeg_command(
            source_type="hikcamera",
            source_path="SN001",
            rtsp_url="rtsp://localhost:8554/c/s",
            width="640",
            height="480",
        )
        idx = cmd.index("-use_wallclock_as_timestamps")
        assert cmd[idx + 1] == "1"


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
            "beaverpush.services.ffmpeg_service.get_window_rect",
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
        # NVENC 不接受 ultrafast/zerolatency；应使用 NVENC 自己的低延迟预设
        assert "ultrafast" not in cmd
        assert "zerolatency" not in cmd
        assert "p1" in cmd
        assert "ll" in cmd

    def test_nvenc_qsv_webrtc_compat_args(self):
        """NVENC/QSV 必须输出 -bf 0 与 -g <gop> 以便 mediamtx 转 WebRTC 后能被浏览器解码。

        h264_nvenc 还需 ``-profile:v main`` 与主流浏览器实现对齐。
        """
        for codec in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv"):
            cmd = build_ffmpeg_command(
                source_type="screen",
                source_path="offset:0,0,1920,1080",
                rtsp_url="rtsp://localhost:8554/c/s",
                video_codec=codec,
                framerate="30",
            )
            assert "-bf" in cmd, codec
            assert cmd[cmd.index("-bf") + 1] == "0", codec
            assert "-g" in cmd, codec
            # framerate=30 → gop=60
            assert cmd[cmd.index("-g") + 1] == "60", codec

        h264_cmd = build_ffmpeg_command(
            source_type="hikcamera", source_path="SN001",
            rtsp_url="rtsp://localhost:8554/c/s",
            video_codec="h264_nvenc",
            width="1920", height="1080", framerate="30",
        )
        assert "-profile:v" in h264_cmd
        assert h264_cmd[h264_cmd.index("-profile:v") + 1] == "main"

    def test_software_codecs_no_extra_webrtc_args(self):
        """libx264/libx265 不应被注入 ``-bf 0`` / ``-g``。

        说明：libx264 默认 ``-bf 3``，但我们对软件编码器一律加上
        ``-tune zerolatency``（见 ``_low_latency_encode_args``），该 tune
        会把 B 帧禁用、把 GOP 设到合理范围，已经满足 WebRTC 兼容，
        无需在命令行再叠加 ``-bf``/``-g``。
        """
        for codec in ("libx264", "libx265"):
            cmd = build_ffmpeg_command(
                source_type="screen",
                source_path="offset:0,0,1920,1080",
                rtsp_url="rtsp://localhost:8554/c/s",
                video_codec=codec,
                framerate="30",
            )
            assert "-bf" not in cmd, codec
            assert "-g" not in cmd, codec

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

    def test_has_hik_capture_method(self):
        worker = FFmpegWorker()
        assert hasattr(worker, "set_hik_capture")
        worker.set_hik_capture("00DA1234567", 1920, 1080, 25)
        assert worker._hik_sn == "00DA1234567"
        assert worker._hik_w == 1920
        assert worker._hik_h == 1080
        assert worker._hik_fps == 25

    def test_hik_capture_strips_sn_and_defaults_fps(self):
        worker = FFmpegWorker()
        worker.set_hik_capture("  SN001  ", 640, 480, 0)
        assert worker._hik_sn == "SN001"
        assert worker._hik_fps == 30

    def test_hik_capture_use_sdk_decode_default_true(self):
        worker = FFmpegWorker()
        worker.set_hik_capture("SN001", 640, 480, 30)
        assert worker._hik_use_sdk_decode is True

    def test_hik_capture_use_sdk_decode_can_be_disabled(self):
        worker = FFmpegWorker()
        worker.set_hik_capture("SN001", 640, 480, 30, use_sdk_decode=False)
        assert worker._hik_use_sdk_decode is False

    def test_start_preview_now_sets_state(self):
        worker = FFmpegWorker()
        with mock.patch.object(worker, "_start_preview"), \
             mock.patch.object(worker, "_start_preview_monitor"):
            worker.start_preview_now("rtsp://localhost:8554/c/s")
            assert worker._preview_enabled is True
            assert worker._preview_url == "rtsp://localhost:8554/c/s"
            worker._start_preview.assert_called_once()
            worker._start_preview_monitor.assert_called_once()

    def test_stop_preview_now_sets_state(self):
        worker = FFmpegWorker()
        with mock.patch.object(worker, "_stop_preview"):
            worker.stop_preview_now()
            assert worker._preview_enabled is False
            worker._stop_preview.assert_called_once()

    def test_preview_closed_emitted_when_ffplay_exits(self):
        """ffplay 进程退出时应发出 preview_closed 信号"""
        worker = FFmpegWorker()
        worker._preview_enabled = True
        mock_proc = mock.MagicMock()
        mock_proc.wait.return_value = 0
        worker._preview_process = mock_proc

        with mock.patch.object(worker, "preview_closed") as mock_signal:
            worker._start_preview_monitor()
            worker._preview_monitor_thread.join(timeout=2)
            mock_signal.emit.assert_called_once()
            assert worker._preview_enabled is False

    def test_preview_closed_not_emitted_on_manual_stop(self):
        """用户主动停止预览时不应发出 preview_closed 信号"""
        worker = FFmpegWorker()
        worker._preview_enabled = False  # 已被 stop_preview_now 置为 False
        mock_proc = mock.MagicMock()
        mock_proc.wait.return_value = 0
        worker._preview_process = mock_proc

        with mock.patch.object(worker, "preview_closed") as mock_signal:
            worker._start_preview_monitor()
            worker._preview_monitor_thread.join(timeout=2)
            mock_signal.emit.assert_not_called()

    def test_status_turns_streaming_after_progress(self):
        worker = FFmpegWorker()
        worker.set_command(["ffmpeg", "-i", "test"])
        statuses = []
        progress = []
        worker.status_changed.connect(statuses.append)
        worker.progress_info.connect(progress.append)

        mock_proc = mock.MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr.readline.side_effect = [
            b"frame=1 fps=25.0 size=1kB time=00:00:01 speed=1x\n",
            b"",
        ]
        mock_proc.stderr.read.return_value = b""

        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.Popen",
            return_value=mock_proc,
        ):
            worker.run()

        assert statuses[:2] == ["正在启动推流...", "等待数据..."]
        assert statuses[2] == "推流中"
        assert progress

    def test_status_turns_streaming_after_ready_line(self):
        worker = FFmpegWorker()
        worker.set_command(["ffmpeg", "-i", "test"])
        statuses = []
        worker.status_changed.connect(statuses.append)

        mock_proc = mock.MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr.readline.side_effect = [
            b"Press [q] to stop, [?] for help\n",
            b"",
        ]
        mock_proc.stderr.read.return_value = b""

        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.Popen",
            return_value=mock_proc,
        ):
            worker.run()

        assert statuses[:3] == ["正在启动推流...", "等待数据...", "推流中"]

    def test_rtsp_startup_timeout_terminates_process(self):
        worker = FFmpegWorker()
        worker.set_source_type("rtsp")
        worker.set_command(["ffmpeg", "-i", "test"])
        worker._startup_timeout_seconds = 0.01
        statuses = []
        worker.status_changed.connect(statuses.append)

        mock_proc = mock.MagicMock()
        mock_proc.returncode = 1
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 1

        def delayed_readline():
            time.sleep(0.2)
            return b""

        mock_proc.stderr.readline.side_effect = delayed_readline
        mock_proc.stderr.read.return_value = b""

        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.Popen",
            return_value=mock_proc,
        ):
            worker.run()

        mock_proc.terminate.assert_called()
        assert "推流中" not in statuses

    def test_stop_does_not_block_waiting_for_process_exit(self):
        worker = FFmpegWorker()
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = None
        worker._process = mock_proc

        worker.stop()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_not_called()


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


class TestCheckRtspServerReachable:
    def test_success(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.run",
            return_value=completed,
        ):
            ok, message = check_rtsp_server_reachable("rtsp://localhost:8554")
        assert ok is True
        assert "连接成功" in message

    def test_success_with_auth(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.run",
            return_value=completed,
        ) as mock_run:
            ok, message = check_rtsp_server_reachable(
                "rtsp://localhost:8554",
                username="alice",
                auth_secret="AKsecret",
                machine_name="pc1",
            )
        assert ok is True
        # 验证测试 URL 包含认证信息和三级路径
        call_args = mock_run.call_args[0][0]
        test_url = call_args[-1]
        assert "alice:AKsecret@" in test_url
        assert "/alice/pc1/__connection_test__" in test_url

    def test_success_with_auth_normalizes_server_and_encodes_secret(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.run",
            return_value=completed,
        ) as mock_run:
            ok, _ = check_rtsp_server_reachable(
                "localhost:8554",
                username="alice",
                auth_secret="A@B:C/%",
                machine_name="pc1",
            )
        assert ok is True
        test_url = mock_run.call_args[0][0][-1]
        assert test_url == "rtsp://alice:A%40B%3AC%2F%25@localhost:8554/alice/pc1/__connection_test__"

    def test_invalid_server_returns_readable_error(self):
        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.run",
        ) as mock_run:
            ok, message = check_rtsp_server_reachable("http://localhost:8554")
        assert ok is False
        assert message == "RTSP 服务器地址格式不正确，应为 rtsp://host[:port]"
        mock_run.assert_not_called()

    def test_refused(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Connection refused",
        )
        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.run",
            return_value=completed,
        ):
            ok, message = check_rtsp_server_reachable("rtsp://localhost:8554")
        assert ok is False
        assert "连接被拒绝" in message

    def test_unauthorized(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="401 Unauthorized",
        )
        with mock.patch(
            "beaverpush.services.ffmpeg_service.subprocess.run",
            return_value=completed,
        ):
            ok, message = check_rtsp_server_reachable(
                "rtsp://localhost:8554",
                username="alice",
                auth_secret="wrong",
            )
        assert ok is False
        assert "认证失败" in message


class TestMaskSensitiveCmd:
    """``_mask_sensitive_cmd`` 把 RTSP URL 里的密码替换为 ``***`` 后再写日志，
    避免 ``%APPDATA%/BeaverPush/logs/*.log`` 留下明文凭据。"""

    def test_masks_password_in_rtsp_url(self):
        cmd = ["ffmpeg", "-i", "input.mp4",
               "-f", "rtsp", "rtsp://alice:s3cret@host:554/stream"]
        masked = _mask_sensitive_cmd(cmd)
        assert "s3cret" not in masked
        assert "rtsp://alice:***@host:554/stream" in masked

    def test_passthrough_when_no_credentials(self):
        cmd = ["ffmpeg", "-i", "in.mp4", "-f", "rtsp", "rtsp://host:554/s"]
        assert _mask_sensitive_cmd(cmd) == " ".join(cmd)

    def test_does_not_modify_original_list(self):
        secret_url = "rtsp://u:p@h:554/x"
        cmd = ["ffmpeg", secret_url]
        _mask_sensitive_cmd(cmd)
        assert cmd[1] == secret_url


class TestNvencLegacyPresetFallback:
    """老版 ffmpeg (n4.x 等) 的 nvenc 不支持 ``p1..p7`` 预设也没有 ``-tune``，
    必须回退到 ``-preset llhp`` 不带 tune；否则 ffmpeg 会以
    ``Error setting option preset to value p1`` 直接退出。"""

    def test_uses_llhp_when_new_presets_unsupported(self, monkeypatch):
        monkeypatch.setattr(
            _ffmpeg_service_mod, "_NVENC_NEW_PRESETS_CACHE", False,
        )
        for codec in ("h264_nvenc", "hevc_nvenc"):
            cmd = build_ffmpeg_command(
                source_type="screen",
                source_path="offset:0,0,1920,1080",
                rtsp_url="rtsp://localhost:8554/c/s",
                video_codec=codec,
                framerate="30",
            )
            assert "p1" not in cmd, f"{codec}: 不应再使用新预设 p1"
            assert "-tune" not in cmd, f"{codec}: 旧版 nvenc 没有 -tune 选项"
            preset_idx = cmd.index("-preset")
            assert cmd[preset_idx + 1] == "llhp", codec

    def test_probe_caches_result(self, monkeypatch):
        """``_nvenc_supports_new_presets`` 必须只跑一次 ffmpeg 进程，
        以免每次构建命令都付出 100~300ms 的拉起开销。"""
        monkeypatch.setattr(
            _ffmpeg_service_mod, "_NVENC_NEW_PRESETS_CACHE", None,
        )
        call_count = {"n": 0}

        def fake_run(*args, **kwargs):
            call_count["n"] += 1
            return subprocess.CompletedProcess(
                args=args, returncode=0,
                stdout="       p1              12   E..V....... fastest\n",
                stderr="",
            )

        monkeypatch.setattr(
            _ffmpeg_service_mod.subprocess, "run", fake_run,
        )
        assert _ffmpeg_service_mod._nvenc_supports_new_presets() is True
        assert _ffmpeg_service_mod._nvenc_supports_new_presets() is True
        assert call_count["n"] == 1

    def test_probe_falls_back_to_old_when_ffmpeg_missing(self, monkeypatch):
        monkeypatch.setattr(
            _ffmpeg_service_mod, "_NVENC_NEW_PRESETS_CACHE", None,
        )

        def boom(*args, **kwargs):
            raise FileNotFoundError("no ffmpeg")

        monkeypatch.setattr(
            _ffmpeg_service_mod.subprocess, "run", boom,
        )
        assert _ffmpeg_service_mod._nvenc_supports_new_presets() is False
