"""encoder_probe 模块单元测试。

不依赖真实的 FFmpeg / 硬件，通过 mock ``subprocess.run`` 验证决策逻辑。
"""

from __future__ import annotations

from unittest import mock

from beaverpush.services import encoder_probe


def _fake_completed(returncode=0, stdout="", stderr=""):
    cp = mock.MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
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

    def test_qsv_probe_uses_init_hw_device(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            assert encoder_probe._probe_encoder("h264_qsv") is True
        assert "-init_hw_device" in captured["cmd"]
        idx = captured["cmd"].index("-init_hw_device")
        assert captured["cmd"][idx + 1].startswith("qsv=")

    def test_nvenc_probe_uses_init_hw_device(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            assert encoder_probe._probe_encoder("hevc_nvenc") is True
        assert "-init_hw_device" in captured["cmd"]
        idx = captured["cmd"].index("-init_hw_device")
        assert captured["cmd"][idx + 1].startswith("cuda")

    def test_software_probe_does_not_use_init_hw_device(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _fake_completed(returncode=0)

        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            side_effect=fake_run,
        ):
            assert encoder_probe._probe_encoder("libx264") is True
        assert "-init_hw_device" not in captured["cmd"]

    def test_returncode_zero_but_stderr_failure_marker_means_unavailable(self):
        """某些 QSV 实现即使 device 创建失败仍以 0 退出，需要扫 stderr。"""
        bad_stderr = "Device creation failed: -3.\nError initializing the MFX session\n"
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=0, stderr=bad_stderr),
        ):
            assert encoder_probe._probe_encoder("h264_qsv") is False

    def test_returncode_zero_but_mfx_session_create_error_means_unavailable(self):
        bad_stderr = "[hevc_qsv @ 0000029d540c0440] Error creating a MFX session: -9.\n"
        with mock.patch(
            "beaverpush.services.encoder_probe.subprocess.run",
            return_value=_fake_completed(returncode=0, stderr=bad_stderr),
        ):
            assert encoder_probe._probe_encoder("hevc_qsv") is False


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



    def test_only_software_when_no_hardware(self):
        # 软件 + 硬件编码器都在 listing 中；硬件实际探测全部失败
        all_listed = set(encoder_probe.SOFTWARE_ENCODERS) | set(encoder_probe.HARDWARE_ENCODERS)
        with mock.patch.object(
            encoder_probe, "_list_ffmpeg_encoders", return_value=all_listed,
        ), mock.patch.object(
            encoder_probe, "detect_gpu_vendors", return_value=None,
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
            # 让 vendors 检查不裁剪：返回 None 表示无法判定时回退到 probe 行为
            encoder_probe, "detect_gpu_vendors", return_value=None,
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
            encoder_probe, "detect_gpu_vendors", return_value=None,
        ), mock.patch.object(
            encoder_probe, "_probe_encoder", return_value=True,
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

        def fake_probe(name: str) -> bool:
            probe_calls.append(name)
            return True  # 模拟 libmfx 软件回退导致的“假成功”

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
            encoder_probe, "_probe_encoder", return_value=True,
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
            side_effect=lambda name: name == "h264_nvenc",
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
            encoder_probe, "_probe_encoder", return_value=False,
        ):
            encoder_probe.detect_available_encoders()
        assert list_mock.call_count == 1
