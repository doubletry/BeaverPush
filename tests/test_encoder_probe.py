"""encoder_probe 模块单元测试。

不依赖真实的 FFmpeg / 硬件，通过 mock ``subprocess.run`` 验证决策逻辑。
"""

from __future__ import annotations

from unittest import mock

from beaverpush.services import encoder_probe


def _fake_completed(returncode=0, stdout=""):
    cp = mock.MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    return cp


class TestFFmpegListsEncoder:
    def test_present_in_listing(self):
        listing = " V..... libx264              H.264\n V..... h264_nvenc           NVIDIA\n"
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=listing),
        ):
            assert encoder_probe._ffmpeg_lists_encoder("libx264") is True
            assert encoder_probe._ffmpeg_lists_encoder("h264_nvenc") is True

    def test_missing_from_listing(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=" V..... libx264 H.264\n"),
        ):
            assert encoder_probe._ffmpeg_lists_encoder("h264_qsv") is False

    def test_substring_does_not_false_match(self):
        # "libx264rgb" 不应让 "libx264" 误判为不存在 / 让 "libx264" 之外的
        # 名字命中。这里只列出 libx264rgb，查询 libx264 应返回 False。
        listing = " V..... libx264rgb           Libx264 RGB encoder\n"
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=listing),
        ):
            assert encoder_probe._ffmpeg_lists_encoder("libx264") is False
            assert encoder_probe._ffmpeg_lists_encoder("libx264rgb") is True

    def test_ffmpeg_missing_returns_false(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            assert encoder_probe._ffmpeg_lists_encoder("libx264") is False


class TestProbeEncoder:
    def test_success(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=0),
        ):
            assert encoder_probe._probe_encoder("h264_qsv") is True

    def test_failure_returncode(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=1),
        ):
            assert encoder_probe._probe_encoder("h264_nvenc") is False

    def test_timeout_treated_as_unavailable(self):
        import subprocess as sp
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="ffmpeg", timeout=5),
        ):
            assert encoder_probe._probe_encoder("h264_nvenc") is False


class TestDetectAvailableEncoders:
    def test_only_software_when_no_hardware(self):
        # 软件 + 硬件编码器都在 listing 中；硬件实际探测全部失败
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=False,
        ):
            result = encoder_probe.detect_available_encoders()
        assert "libx264" in result
        assert "libx265" in result
        assert "h264_nvenc" not in result
        assert "h264_qsv" not in result

    def test_includes_hardware_when_probe_succeeds(self):
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder",
            side_effect=lambda name: name in ("h264_qsv", "hevc_qsv"),
        ):
            result = encoder_probe.detect_available_encoders()
        assert "libx264" in result
        assert "h264_qsv" in result
        assert "hevc_qsv" in result
        assert "h264_nvenc" not in result

    def test_skips_codec_not_in_ffmpeg_listing(self):
        listed = {"libx264"}  # 只有 libx264 在 -encoders 输出里
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=listed,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=True,
        ):
            result = encoder_probe.detect_available_encoders()
        assert result == ["libx264"]

    def test_listing_subprocess_called_only_once(self):
        """关键性能保证：哪怕有 6 个候选编码器，也只能调用一次 ffmpeg -encoders。"""
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        list_mock = mock.MagicMock(return_value=all_listed)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", list_mock,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=False,
        ):
            encoder_probe.detect_available_encoders()
        assert list_mock.call_count == 1

