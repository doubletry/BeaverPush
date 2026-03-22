"""window_capture 模块单元测试

测试屏幕捕获相关函数（使用 mock 替代 Win32 API）。
"""

from unittest import mock
import ctypes

from push_client.services.window_capture import (
    _make_even,
    ScreenCaptureFeeder,
    SRCCOPY,
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


class TestCursorDrawingResilience:
    """光标绘制相关测试"""

    def test_cursor_snapshot_used_in_capture(self):
        """capture_screen_frame 通过 CopyIcon 快照绘制鼠标光标"""
        from push_client.services.window_capture import capture_screen_frame
        # _get_cursor_snapshot 返回 (hCursorCopy, draw_x, draw_y)
        fake_snap = (12345, 5, 5)  # 模拟光标句柄和位置
        mock_windll = mock.MagicMock()
        mock_windll.user32.GetDC.return_value = 1
        mock_windll.gdi32.CreateCompatibleDC.return_value = 2
        mock_windll.gdi32.CreateCompatibleBitmap.return_value = 3
        mock_windll.gdi32.SelectObject.return_value = 4
        with mock.patch(
            "push_client.services.window_capture._extract_pixels",
            return_value=b"\x00" * 100,
        ), mock.patch(
            "push_client.services.window_capture._get_cursor_snapshot",
            return_value=fake_snap,
        ) as mock_snap, mock.patch(
            "push_client.services.window_capture.ctypes"
        ) as mock_ctypes:
            mock_ctypes.windll = mock_windll
            result = capture_screen_frame(0, 0, 10, 10)
            assert result is not None
            assert len(result) == 100
            mock_snap.assert_called_once()

    def test_capture_works_when_cursor_invisible(self):
        """光标不可见时 capture_screen_frame 仍正常返回帧数据"""
        from push_client.services.window_capture import capture_screen_frame
        mock_windll = mock.MagicMock()
        mock_windll.user32.GetDC.return_value = 1
        mock_windll.gdi32.CreateCompatibleDC.return_value = 2
        mock_windll.gdi32.CreateCompatibleBitmap.return_value = 3
        mock_windll.gdi32.SelectObject.return_value = 4
        with mock.patch(
            "push_client.services.window_capture._extract_pixels",
            return_value=b"\x00" * 100,
        ), mock.patch(
            "push_client.services.window_capture._get_cursor_snapshot",
            return_value=None,
        ), mock.patch(
            "push_client.services.window_capture.ctypes"
        ) as mock_ctypes:
            mock_ctypes.windll = mock_windll
            result = capture_screen_frame(0, 0, 10, 10)
            assert result is not None
            assert len(result) == 100

    def test_feeder_continues_after_capture_error(self):
        """截图异常时 feeder 跳过当前帧但继续运行"""
        feeder = ScreenCaptureFeeder(0, 0, 320, 240, 30)
        mock_process = mock.MagicMock()
        # 第一次 poll 返回 None（运行中），后续返回 0（退出）
        mock_process.poll.side_effect = [None, None, 0]

        call_count = 0

        def failing_capture(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error")
            return None

        with mock.patch(
            "push_client.services.window_capture.capture_screen_frame",
            side_effect=failing_capture,
        ):
            feeder.start(mock_process)
            import time
            time.sleep(0.2)
            feeder.stop()
        # feeder 应调用了多次（没有在第一次异常后停止）
        assert call_count >= 2

    def test_feeder_stops_after_max_consecutive_errors(self):
        """连续异常超过上限时 feeder 自动停止循环"""
        feeder = ScreenCaptureFeeder(0, 0, 320, 240, 1000)  # 高 fps 加速测试
        mock_process = mock.MagicMock()
        mock_process.poll.return_value = None  # 始终运行中

        call_count = 0

        def always_failing(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent error")

        with mock.patch(
            "push_client.services.window_capture.capture_screen_frame",
            side_effect=always_failing,
        ):
            feeder.start(mock_process)
            # 等待 feeder 线程自行退出（连续错误达到阈值后会 break）
            thread = feeder._thread
            assert thread is not None, "ScreenCaptureFeeder thread was not started."
            thread.join(timeout=5)
            assert not thread.is_alive(), "ScreenCaptureFeeder thread did not exit within 5 seconds."
            feeder.stop()
        # feeder 应在达到 max_consecutive_errors (30) 时停止
        assert 30 <= call_count <= 35
