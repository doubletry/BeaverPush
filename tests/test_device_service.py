"""device_service 模块单元测试"""

import subprocess
from unittest import mock

import pytest

from beaverpush.services.device_service import get_camera_best_resolution


class TestGetCameraBestResolution:
    """[改进] 测试自动获取摄像头最高分辨率功能"""

    def test_returns_mjpeg_resolution_when_available(self):
        """优先返回 mjpeg 编码的分辨率（高分辨率下帧率更高）"""
        stderr_output = """
[dshow @ 000001d3661145c0] "1080P USB Camera" (video)
[dshow @ 000001d3661145c0]   Alternative name "@device_pnp_\\..."
[dshow @ 000001d3661145c0]   pixel_format=yuyv422  min s=640x480 fps=30 max s=640x480 fps=30
[dshow @ 000001d3661145c0]   pixel_format=yuyv422  min s=1920x1080 fps=5 max s=1920x1080 fps=5
[dshow @ 000001d3661145c0]   vcodec=mjpeg  min s=640x480 fps=25 max s=640x480 fps=30
[dshow @ 000001d3661145c0]   vcodec=mjpeg  min s=1920x1080 fps=25 max s=1920x1080 fps=30
"""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=stderr_output
        )
        with mock.patch(
            "beaverpush.services.device_service.subprocess.run",
            return_value=completed,
        ):
            result = get_camera_best_resolution("1080P USB Camera")

        assert result == (1920, 1080, "mjpeg")

    def test_returns_raw_when_no_mjpeg(self):
        """没有 mjpeg 时返回 raw 编码的分辨率"""
        stderr_output = """
[dshow @ 000001d3661145c0] "USB Camera" (video)
[dshow @ 000001d3661145c0]   pixel_format=yuyv422  min s=640x480 fps=30 max s=640x480 fps=30
[dshow @ 000001d3661145c0]   pixel_format=yuyv422  min s=1280x720 fps=10 max s=1280x720 fps=10
"""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=stderr_output
        )
        with mock.patch(
            "beaverpush.services.device_service.subprocess.run",
            return_value=completed,
        ):
            result = get_camera_best_resolution("USB Camera")

        assert result == (1280, 720, "raw")

    def test_returns_none_on_empty_output(self):
        """FFmpeg 输出为空时返回 None"""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with mock.patch(
            "beaverpush.services.device_service.subprocess.run",
            return_value=completed,
        ):
            result = get_camera_best_resolution("USB Camera")

        assert result is None

    def test_returns_none_on_exception(self):
        """发生异常时返回 None"""
        with mock.patch(
            "beaverpush.services.device_service.subprocess.run",
            side_effect=FileNotFoundError("no ffmpeg"),
        ):
            result = get_camera_best_resolution("USB Camera")

        assert result is None

    def test_returns_none_on_timeout(self):
        """超时时返回 None"""
        with mock.patch(
            "beaverpush.services.device_service.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5),
        ):
            result = get_camera_best_resolution("USB Camera")

        assert result is None

    def test_selects_highest_mjpeg_resolution(self):
        """多个 mjpeg 分辨率时选择最高的"""
        stderr_output = """
[dshow @ 000001d3661145c0] "Camera" (video)
[dshow @ 000001d3661145c0]   vcodec=mjpeg  min s=640x480 fps=30 max s=640x480 fps=30
[dshow @ 000001d3661145c0]   vcodec=mjpeg  min s=1280x720 fps=30 max s=1280x720 fps=30
[dshow @ 000001d3661145c0]   vcodec=mjpeg  min s=1920x1080 fps=30 max s=1920x1080 fps=30
"""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=stderr_output
        )
        with mock.patch(
            "beaverpush.services.device_service.subprocess.run",
            return_value=completed,
        ):
            result = get_camera_best_resolution("Camera")

        assert result == (1920, 1080, "mjpeg")

    def test_mjpeg_higher_priority_than_raw_same_resolution(self):
        """相同分辨率时 mjpeg 优先于 raw"""
        stderr_output = """
[dshow @ 000001d3661145c0] "Camera" (video)
[dshow @ 000001d3661145c0]   pixel_format=yuyv422  min s=1920x1080 fps=5 max s=1920x1080 fps=5
[dshow @ 000001d3661145c0]   vcodec=mjpeg  min s=1920x1080 fps=30 max s=1920x1080 fps=30
"""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=stderr_output
        )
        with mock.patch(
            "beaverpush.services.device_service.subprocess.run",
            return_value=completed,
        ):
            result = get_camera_best_resolution("Camera")

        assert result == (1920, 1080, "mjpeg")
