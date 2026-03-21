"""window_capture 模块单元测试

测试屏幕捕获相关函数（使用 mock 替代 Win32 API）。
"""

from unittest import mock
import ctypes

from push_client.services.window_capture import (
    _make_even,
    ScreenCaptureFeeder,
)


class TestMakeEven:
    def test_even_unchanged(self):
        assert _make_even(1920) == 1920

    def test_odd_incremented(self):
        assert _make_even(1921) == 1922

    def test_one(self):
        assert _make_even(1) == 2

    def test_zero(self):
        assert _make_even(0) == 0


class TestScreenCaptureFeeder:
    def test_init_makes_even(self):
        feeder = ScreenCaptureFeeder(0, 0, 1921, 1081, 30)
        assert feeder.w == 1922
        assert feeder.h == 1082
        assert feeder.fps == 30
        assert feeder.x == 0
        assert feeder.y == 0

    def test_stop_without_start(self):
        feeder = ScreenCaptureFeeder(0, 0, 1920, 1080, 30)
        feeder.stop()  # 不应抛出异常

    def test_start_creates_thread(self):
        feeder = ScreenCaptureFeeder(0, 0, 1920, 1080, 30)
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = 0  # 已退出
        with mock.patch(
            "push_client.services.window_capture.capture_screen_frame",
            return_value=None,
        ):
            feeder.start(mock_process)
            assert feeder._running is True
            assert feeder._thread is not None
            feeder.stop()


class TestScreenCaptureStructures:
    """验证屏幕捕获结构体和常量已正确定义"""

    def test_cursorinfo_struct_exists(self):
        from push_client.services.window_capture import CURSORINFO
        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(CURSORINFO)
        assert ci.cbSize > 0

    def test_iconinfo_struct_exists(self):
        from push_client.services.window_capture import ICONINFO
        ii = ICONINFO()
        assert hasattr(ii, "xHotspot")
        assert hasattr(ii, "yHotspot")

    def test_di_normal_constant(self):
        from push_client.services.window_capture import DI_NORMAL
        assert DI_NORMAL == 0x0003
