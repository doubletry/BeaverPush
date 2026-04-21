"""encoder_probe 模块单元测试。

不依赖真实的 FFmpeg / 硬件，通过 mock ``subprocess.run`` 验证决策逻辑。
"""

from __future__ import annotations

import os
import tempfile
from unittest import mock

from beaverpush.services import encoder_probe


def _fake_completed(returncode=0, stdout="", stderr=""):
    cp = mock.MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _probe_result(ok: bool, rc: int | None = None, stderr: str = ""):
    """构造 _probe_encoder 的返回三元组，方便 mock 使用。"""
    return (ok, rc if rc is not None else (0 if ok else 1), stderr)


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
    def test_create_qsv_probe_video_writes_y4m_file(self):
        path = encoder_probe._create_qsv_probe_video()
        try:
            with open(path, "rb") as f:
                header = f.read(64)
            assert header.startswith(b"YUV4MPEG2 W1280 H720 F30:1 ")
        finally:
            os.unlink(path)

    def test_success(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=0),
        ):
            ok, _, _ = encoder_probe._probe_encoder("h264_qsv")
            assert ok is True

    def test_failure_returncode(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=1, stderr="boom"),
        ):
            ok, rc, stderr = encoder_probe._probe_encoder("h264_nvenc")
            assert ok is False
            assert rc == 1
            assert "boom" in stderr

    def test_timeout_treated_as_unavailable(self):
        import subprocess as sp
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="ffmpeg", timeout=5),
        ):
            ok, _, _ = encoder_probe._probe_encoder("h264_nvenc")
            assert ok is False

    def test_qsv_probe_uses_init_hw_device(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            ok, _, _ = encoder_probe._probe_encoder("h264_qsv")
            assert ok is True
        assert "-init_hw_device" in captured["cmd"]
        idx = captured["cmd"].index("-init_hw_device")
        spec = captured["cmd"][idx + 1]
        assert spec.startswith("qsv=")
        # 关键回归：再也不能出现历史上那个非法的 ``hw_any`` 子设备名
        assert "hw_any" not in spec

    def test_qsv_probe_uses_temp_video_file_source(self):
        """QSV probe 改为走临时视频文件输入，而不是 lavfi 合成源。

        回归背景：真实用户在 UHD 770 上用命令行直接 ``-c:v hevc_qsv`` 转码正常，
        但 lavfi ``wrapped_avframe`` probe 会以 ``Error creating a MFX session:
        -9.`` 失败。这里要求 probe 改成更贴近 ``ffmpeg -i input.mp4`` 的
        文件输入路径。
        """
        captured = {}
        probe_path = os.path.join(tempfile.gettempdir(), "fake-qsv-probe.y4m")

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch.object(
            encoder_probe, "_create_qsv_probe_video", return_value=probe_path,
        ), mock.patch(
            "beaverpush.services.encoder_probe.os.unlink",
        ) as unlink, mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            ok, _, _ = encoder_probe._probe_encoder("hevc_qsv")
            assert ok is True
        cmd = captured["cmd"]
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == probe_path
        assert "lavfi" not in cmd
        assert "-pix_fmt" not in cmd
        unlink.assert_called_once_with(probe_path)

    def test_qsv_probe_tries_no_longer_uses_hw_any(self):
        """所有平台、所有候选 spec 中都不能再包含 ``hw_any``。"""
        for spec in encoder_probe._qsv_device_specs():
            assert "hw_any" not in spec, spec

    def test_qsv_probe_tries_multiple_device_specs(self):
        """第一条 spec 失败 (rc=1, ``device creation failed``)，
        第二条成功——_probe_encoder 应整体判定为可用。
        强制走 Windows 候选列表，保证 fallback 逻辑被覆盖。
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                return _fake_completed(
                    returncode=1,
                    stderr="Device creation failed: -3.\n",
                )
            return _fake_completed(returncode=0)

        # 直接 mock 候选列表，避免依赖宿主平台
        win_specs = ("qsv=hw", "qsv=hw,child_device_type=d3d11va")
        with mock.patch.object(
            encoder_probe, "_qsv_device_specs", return_value=win_specs,
        ), mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            ok, _, _ = encoder_probe._probe_encoder("h264_qsv")
        assert ok is True
        assert len(calls) == 2
        # 第二次调用必须命中第二条 spec，证明确实换了规范而不是简单重试
        assert "-init_hw_device" in calls[1]
        idx0 = calls[0].index("-init_hw_device")
        idx1 = calls[1].index("-init_hw_device")
        assert calls[0][idx0 + 1] == "qsv=hw"
        assert calls[1][idx1 + 1] == "qsv=hw,child_device_type=d3d11va"

    def test_nvenc_probe_uses_init_hw_device(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            ok, _, _ = encoder_probe._probe_encoder("hevc_nvenc")
            assert ok is True
        assert "-init_hw_device" in captured["cmd"]
        idx = captured["cmd"].index("-init_hw_device")
        assert captured["cmd"][idx + 1].startswith("cuda")

    def test_nvenc_probe_keeps_yuv420p_path(self):
        """QSV 的 probe 输入修正不能影响 NVENC 的既有防假阴性路径。"""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            ok, _, _ = encoder_probe._probe_encoder("h264_nvenc")
            assert ok is True
        cmd = captured["cmd"]
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == "testsrc=duration=1:size=320x240:rate=1"
        pix_idx = cmd.index("-pix_fmt")
        assert cmd[pix_idx + 1] == "yuv420p"

    def test_software_probe_does_not_use_init_hw_device(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            ok, _, _ = encoder_probe._probe_encoder("libx264")
            assert ok is True
        assert "-init_hw_device" not in captured["cmd"]

    def test_returncode_zero_but_stderr_failure_marker_means_unavailable(self):
        """某些 QSV 实现即使 device 创建失败仍以 0 退出，需要扫 stderr。
        使用我们保留的精确标记 ``Device creation failed``。
        """
        bad_stderr = "Device creation failed: -3.\n"
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=0, stderr=bad_stderr),
        ):
            ok, _, _ = encoder_probe._probe_encoder("h264_qsv")
            assert ok is False

    def test_returncode_zero_but_mfx_session_create_error_means_unavailable(self):
        bad_stderr = "[hevc_qsv @ 0000029d540c0440] Error creating a MFX session: -9.\n"
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=0, stderr=bad_stderr),
        ):
            ok, _, _ = encoder_probe._probe_encoder("hevc_qsv")
            assert ok is False

    def test_benign_d3d11_fallback_message_does_not_false_negative(self):
        """关键回归：FFmpeg 在 QSV 路径上常打印
        ``Failed to create a D3D11 device, trying D3D9.`` 之类的回退提示，
        然后正常继续编码。旧的过宽 ``failed to create`` 标记会把它误判为失败。
        """
        benign_stderr = (
            "[AVHWDeviceContext @ 0xabc] Failed to create a D3D11 device, "
            "trying D3D9.\n"
            "frame=    1 fps=0.0 q=-0.0 size=N/A time=00:00:01.00 bitrate=N/A\n"
        )
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=0, stderr=benign_stderr),
        ):
            ok, _, _ = encoder_probe._probe_encoder("h264_qsv")
            assert ok is True


class TestClassifyGpuVendor:
    def test_intel(self):
        assert encoder_probe._classify_gpu_vendor("Intel(R) UHD Graphics 770") == "intel"

    def test_nvidia(self):
        assert encoder_probe._classify_gpu_vendor("NVIDIA GeForce RTX 4070") == "nvidia"
        assert encoder_probe._classify_gpu_vendor("Quadro P2000") == "nvidia"

    def test_amd(self):
        assert encoder_probe._classify_gpu_vendor("AMD Radeon RX 6800") == "amd"

    def test_unknown(self):
        assert encoder_probe._classify_gpu_vendor("Microsoft Basic Display Adapter") is None


class TestDetectGpuVendorsLinux:
    def test_xeon_w5_no_gpu_returns_empty_set(self):
        """Xeon W5-3545 + 无独显场景：lspci 输出里没有任何显示控制器行。"""
        lspci_stdout = (
            "00:00.0 Host bridge: Intel Corporation Device 1234\n"
            "00:1f.0 ISA bridge: Intel Corporation Device 5678\n"
        )
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=lspci_stdout),
        ):
            assert encoder_probe._detect_gpu_vendors_linux(timeout=5.0) == set()

    def test_nvidia_only(self):
        lspci_stdout = (
            "01:00.0 VGA compatible controller: NVIDIA Corporation GA104 [GeForce RTX 3070]\n"
        )
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=lspci_stdout),
        ):
            assert encoder_probe._detect_gpu_vendors_linux(timeout=5.0) == {"nvidia"}

    def test_intel_plus_nvidia(self):
        lspci_stdout = (
            "00:02.0 VGA compatible controller: Intel Corporation UHD Graphics 770\n"
            "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050 Mobile]\n"
        )
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=lspci_stdout),
        ):
            assert encoder_probe._detect_gpu_vendors_linux(timeout=5.0) == {"intel", "nvidia"}

    def test_lspci_missing_returns_none(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            assert encoder_probe._detect_gpu_vendors_linux(timeout=5.0) is None


class TestDetectGpuVendorsWindows:
    def test_powershell_name_list(self):
        stdout = (
            "NVIDIA GeForce RTX 4070\n"
            "Microsoft Basic Render Driver\n"
        )
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=stdout),
        ):
            assert encoder_probe._detect_gpu_vendors_windows(timeout=5.0) == {"nvidia"}

    def test_wmic_value_output(self):
        stdout = (
            "Name=NVIDIA GeForce RTX 4070\n"
            "\n"
            "Name=Intel(R) UHD Graphics 770\n"
        )
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(stdout=stdout),
        ):
            assert encoder_probe._detect_gpu_vendors_windows(timeout=5.0) == {
                "nvidia", "intel",
            }

    def test_commands_missing_returns_none(self):
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            assert encoder_probe._detect_gpu_vendors_windows(timeout=5.0) is None


class TestDetectAvailableEncoders:
    def test_only_software_when_no_hardware(self):
        # 软件 + 硬件编码器都在 listing 中；硬件实际探测全部失败
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
        ), mock.patch.object(
            encoder_probe, "detect_gpu_vendors", return_value=None,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=_probe_result(False),
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
            # 让 vendors 检查不裁剪：返回 None 表示无法判定时回退到 probe 行为
            encoder_probe, "detect_gpu_vendors", return_value=None,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder",
            side_effect=lambda name: _probe_result(name in ("h264_qsv", "hevc_qsv")),
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
            encoder_probe, "detect_gpu_vendors", return_value=None,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=_probe_result(True),
        ):
            result = encoder_probe.detect_available_encoders()
        assert result == ["libx264"]

    def test_no_intel_gpu_skips_qsv_even_if_probe_would_pass(self):
        """关键回归：无 Intel iGPU 的机器（例如 Xeon W5-3545）即使 ffmpeg 内置
        了 QSV 编码器、即使 1 帧 testsrc probe 通过 libmfx 软件回退能成功，
        也不应该把 QSV 暴露到 UI；同时 NVIDIA-only 时仍能正常列出 nvenc。
        """
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        probe_calls: list[str] = []

        def fake_probe(name: str):
            probe_calls.append(name)
            return _probe_result(True)  # 模拟 libmfx 软件回退导致的“假成功”

        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
        ), mock.patch.object(
            encoder_probe, "detect_gpu_vendors", return_value={"nvidia"},
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", side_effect=fake_probe,
        ):
            result = encoder_probe.detect_available_encoders()
        assert "h264_qsv" not in result
        assert "hevc_qsv" not in result
        assert "h264_nvenc" in result
        assert "hevc_nvenc" in result
        # 既然 vendor 已经判明无 Intel，就不应该再去为 QSV 启动 ffmpeg 子进程
        assert "h264_qsv" not in probe_calls
        assert "hevc_qsv" not in probe_calls

    def test_no_gpu_at_all_strips_all_hardware_encoders(self):
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
        ), mock.patch.object(
            encoder_probe, "detect_gpu_vendors", return_value=set(),
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=_probe_result(True),
        ):
            result = encoder_probe.detect_available_encoders()
        for hw in encoder_probe.HARDWARE_ENCODERS:
            assert hw not in result

    def test_vendor_detection_unknown_falls_back_to_probe(self):
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
        ), mock.patch.object(
            encoder_probe, "detect_gpu_vendors", return_value=None,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder",
            side_effect=lambda name: _probe_result(name == "h264_nvenc"),
        ):
            result = encoder_probe.detect_available_encoders()
        assert "h264_nvenc" in result
        assert "hevc_nvenc" not in result
        assert "h264_qsv" not in result

    def test_listing_subprocess_called_only_once(self):
        """关键性能保证：哪怕有 6 个候选编码器，也只能调用一次 ffmpeg -encoders。"""
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        list_mock = mock.MagicMock(return_value=all_listed)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", list_mock,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=_probe_result(False),
        ):
            encoder_probe.detect_available_encoders()
        assert list_mock.call_count == 1

    def test_probe_failure_logs_stderr_when_vendor_detected(self):
        """vendor 已确认在场（这里是 intel）但 QSV probe 仍失败时，
        必须把 ffmpeg stderr 抬到 WARNING 日志，否则用户根本无法定位
        驱动 / oneVPL / runtime 哪一层挂了。
        """
        from loguru import logger as _logger

        marker_stderr = (
            "[h264_qsv @ 0x1] Error creating a MFX session: -9.\n"
            "Conversion failed!\n"
        )
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)

        captured: list[str] = []
        sink_id = _logger.add(
            lambda msg: captured.append(str(msg)),
            level="WARNING",
            format="{level}|{message}",
        )
        try:
            with mock.patch.object(
                encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
            ), mock.patch.object(
                encoder_probe, "detect_gpu_vendors", return_value={"intel"},
            ), mock.patch.object(
                encoder_probe, "_probe_encoder",
                return_value=_probe_result(False, rc=1, stderr=marker_stderr),
            ):
                result = encoder_probe.detect_available_encoders()
        finally:
            _logger.remove(sink_id)

        assert "h264_qsv" not in result
        warning_blob = "\n".join(captured)
        # 警告里必须出现：编码器名 + 厂商 + ffmpeg 实际报错关键字
        assert "WARNING" in warning_blob
        assert "h264_qsv" in warning_blob
        assert "intel" in warning_blob
        assert "Error creating a MFX session" in warning_blob
