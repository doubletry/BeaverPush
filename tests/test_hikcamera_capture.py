"""hikcamera_capture 模块单元测试

由于 CI 环境无 Hikvision MVS SDK，这里通过 mock 替换 ``hikcamera`` 模块，
验证 :class:`HikCameraFeeder` 的回调写帧、断线处理与资源释放逻辑。
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import numpy as np
import pytest

from beaverpush.services.hikcamera_capture import (
    HikCameraFeeder, _make_even, probe_hikcamera_size,
)


# ---------------------------------------------------------------------------
# 公用 fixture：在 sys.modules 中注入伪造的 hikcamera 模块
# ---------------------------------------------------------------------------

class _FakeAccessMode:
    EXCLUSIVE = "EXCLUSIVE"


class _FakeOutputFormat:
    BGR8 = "BGR8"


class _FakeHik:
    AccessMode = _FakeAccessMode
    OutputFormat = _FakeOutputFormat


class _FakeCamera:
    """模拟 ``hikcamera.HikCamera`` 实例。"""

    instances: list["_FakeCamera"] = []

    def __init__(self, sn: str, frames: list[np.ndarray] | None = None):
        self.sn = sn
        self._frames = frames or []
        self.opened = False
        self.grabbing = False
        self.closed = False
        self.callback = None
        self.on_exception = None
        self.exit_called = False
        self.use_sdk_decode_calls: list[bool] = []
        _FakeCamera.instances.append(self)

    # context manager
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exit_called = True
        return False

    # camera api subset
    def open(self, mode):  # noqa: ARG002
        self.opened = True

    def set_use_sdk_decode(self, enable):
        self.use_sdk_decode_calls.append(bool(enable))

    def start_grabbing(self, callback=None, output_format=None, on_exception=None):  # noqa: ARG002
        self.grabbing = True
        self.callback = callback
        self.on_exception = on_exception

    def stop_grabbing(self):
        self.grabbing = False

    def get_frame(self, timeout_ms=1000, output_format=None):  # noqa: ARG002
        if self._frames:
            return self._frames.pop(0)
        return np.zeros((480, 640, 3), dtype=np.uint8)


def _install_fake_hikcamera(camera_factory):
    """安装一个伪造的 ``hikcamera`` 模块到 ``sys.modules``。"""
    fake_module = types.ModuleType("hikcamera")
    fake_module.Hik = _FakeHik

    class _FakeHikCamera:
        @staticmethod
        def from_serial_number(sn: str):
            return camera_factory(sn)

    fake_module.HikCamera = _FakeHikCamera
    sys.modules["hikcamera"] = fake_module
    return fake_module


@pytest.fixture
def fake_hikcamera():
    """每个测试自动卸载伪造模块以避免互相污染。"""
    _FakeCamera.instances = []
    yield
    sys.modules.pop("hikcamera", None)


# ---------------------------------------------------------------------------
# 帮助类
# ---------------------------------------------------------------------------

class _FakeStdin:
    def __init__(self, fail_after: int | None = None):
        self.writes: list[bytes] = []
        self.flushed = 0
        self.closed = False
        self._fail_after = fail_after

    def write(self, data: bytes):
        if self._fail_after is not None and len(self.writes) >= self._fail_after:
            raise BrokenPipeError("stdin closed")
        self.writes.append(bytes(data))

    def flush(self):
        self.flushed += 1

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, stdin: _FakeStdin | None = None):
        self.stdin = stdin

    def poll(self):
        return None


# ---------------------------------------------------------------------------
# probe_hikcamera_size
# ---------------------------------------------------------------------------

class TestProbeHikCameraSize:
    def test_returns_even_dimensions(self, fake_hikcamera):
        frame = np.zeros((721, 1281, 3), dtype=np.uint8)
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn, [frame]))
        w, h = probe_hikcamera_size("SN001")
        assert (w, h) == (1282, 722)

    def test_releases_camera_via_context_manager(self, fake_hikcamera):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn, [frame]))
        probe_hikcamera_size("SN001")
        assert _FakeCamera.instances[0].exit_called is True
        assert _FakeCamera.instances[0].grabbing is False

    def test_empty_sn_raises(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        with pytest.raises(RuntimeError):
            probe_hikcamera_size("   ")

    def test_missing_library_raises_import_error(self):
        sys.modules.pop("hikcamera", None)
        with mock.patch.dict(sys.modules, {"hikcamera": None}):
            with pytest.raises(ImportError):
                probe_hikcamera_size("SN001")

    def test_open_failure_propagates_as_runtime_error(self, fake_hikcamera):
        class _BadCamera(_FakeCamera):
            def open(self, mode):
                raise OSError("device busy")

        _install_fake_hikcamera(lambda sn: _BadCamera(sn))
        with pytest.raises(RuntimeError):
            probe_hikcamera_size("SN001")


# ---------------------------------------------------------------------------
# HikCameraFeeder.start / stop
# ---------------------------------------------------------------------------

class TestHikCameraFeederLifecycle:
    def test_start_opens_camera_and_starts_grabbing(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        feeder = HikCameraFeeder("SN001", 1920, 1080, 30)
        process = _FakeProcess(_FakeStdin())
        feeder.start(process)
        cam = _FakeCamera.instances[0]
        assert cam.opened is True
        assert cam.grabbing is True
        assert cam.callback is not None
        feeder.stop()
        assert cam.grabbing is False
        assert cam.exit_called is True

    def test_stop_is_idempotent(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        feeder = HikCameraFeeder("SN001", 640, 480, 30)
        feeder.start(_FakeProcess(_FakeStdin()))
        feeder.stop()
        feeder.stop()  # should not raise

    def test_empty_sn_raises(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        feeder = HikCameraFeeder("", 640, 480, 30)
        with pytest.raises(RuntimeError):
            feeder.start(_FakeProcess(_FakeStdin()))

    def test_start_grabbing_failure_releases_camera(self, fake_hikcamera):
        class _BadCamera(_FakeCamera):
            def start_grabbing(self, **kwargs):
                raise OSError("sdk failure")

        _install_fake_hikcamera(lambda sn: _BadCamera(sn))
        feeder = HikCameraFeeder("SN001", 640, 480, 30)
        with pytest.raises(RuntimeError):
            feeder.start(_FakeProcess(_FakeStdin()))
        assert _FakeCamera.instances[0].exit_called is True


# ---------------------------------------------------------------------------
# HikCameraFeeder._on_frame
# ---------------------------------------------------------------------------

class TestHikCameraFeederOnFrame:
    def test_frame_written_to_stdin_when_size_matches(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        stdin = _FakeStdin()
        feeder = HikCameraFeeder("SN001", 640, 480, 30)
        feeder.start(_FakeProcess(stdin))
        frame = np.full((480, 640, 3), 7, dtype=np.uint8)
        _FakeCamera.instances[0].callback(frame, {"frame_num": 1})
        assert len(stdin.writes) == 1
        assert len(stdin.writes[0]) == 640 * 480 * 3
        assert stdin.flushed >= 1
        feeder.stop()

    def test_mismatched_size_padded_to_expected_bytes(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        stdin = _FakeStdin()
        feeder = HikCameraFeeder("SN001", 640, 480, 30)
        feeder.start(_FakeProcess(stdin))
        smaller = np.full((240, 320, 3), 5, dtype=np.uint8)
        _FakeCamera.instances[0].callback(smaller, {"frame_num": 1})
        bigger = np.full((720, 1280, 3), 9, dtype=np.uint8)
        _FakeCamera.instances[0].callback(bigger, {"frame_num": 2})
        assert all(len(buf) == 640 * 480 * 3 for buf in stdin.writes)
        feeder.stop()

    def test_broken_pipe_stops_subsequent_writes(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        stdin = _FakeStdin(fail_after=0)
        feeder = HikCameraFeeder("SN001", 320, 240, 30)
        feeder.start(_FakeProcess(stdin))
        cb = _FakeCamera.instances[0].callback
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        cb(frame, {})  # triggers BrokenPipeError → marks stopped
        cb(frame, {})  # should be a no-op
        assert len(stdin.writes) == 0
        assert feeder._stopped is True
        feeder.stop()

    def test_no_write_after_stop(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        stdin = _FakeStdin()
        feeder = HikCameraFeeder("SN001", 320, 240, 30)
        feeder.start(_FakeProcess(stdin))
        cb = _FakeCamera.instances[0].callback
        feeder.stop()
        cb(np.zeros((240, 320, 3), dtype=np.uint8), {})
        assert len(stdin.writes) == 0


# ---------------------------------------------------------------------------
# HikCameraFeeder._on_exception (断线处理)
# ---------------------------------------------------------------------------

class TestHikCameraFeederOnException:
    def test_disconnect_invokes_callback_and_closes_stdin(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        stdin = _FakeStdin()
        feeder = HikCameraFeeder("SN001", 320, 240, 30)
        captured: list[str] = []
        feeder.set_error_callback(captured.append)
        feeder.start(_FakeProcess(stdin))

        _FakeCamera.instances[0].on_exception(RuntimeError("cable unplugged"))

        assert feeder._stopped is True
        assert stdin.closed is True
        assert len(captured) == 1
        assert "海康相机断开" in captured[0]
        assert "cable unplugged" in captured[0]
        feeder.stop()

    def test_disconnect_callback_only_emitted_once(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        feeder = HikCameraFeeder("SN001", 320, 240, 30)
        captured: list[str] = []
        feeder.set_error_callback(captured.append)
        feeder.start(_FakeProcess(_FakeStdin()))
        cb = _FakeCamera.instances[0].on_exception
        cb(RuntimeError("first"))
        cb(RuntimeError("second"))
        assert len(captured) == 1


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

def test_make_even_helper():
    assert _make_even(0) == 0
    assert _make_even(2) == 2
    assert _make_even(3) == 4


# ---------------------------------------------------------------------------
# set_use_sdk_decode 透传
# ---------------------------------------------------------------------------

class TestUseSdkDecodePassthrough:
    def test_probe_default_calls_set_use_sdk_decode_true(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        probe_hikcamera_size("SN001")
        assert _FakeCamera.instances[0].use_sdk_decode_calls == [True]

    def test_probe_can_disable_sdk_decode(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        probe_hikcamera_size("SN001", use_sdk_decode=False)
        assert _FakeCamera.instances[0].use_sdk_decode_calls == [False]

    def test_feeder_default_calls_set_use_sdk_decode_true(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        feeder = HikCameraFeeder("SN001", 640, 480, 30)
        feeder.start(_FakeProcess(_FakeStdin()))
        assert _FakeCamera.instances[0].use_sdk_decode_calls == [True]
        feeder.stop()

    def test_feeder_can_disable_sdk_decode(self, fake_hikcamera):
        _install_fake_hikcamera(lambda sn: _FakeCamera(sn))
        feeder = HikCameraFeeder("SN001", 640, 480, 30, use_sdk_decode=False)
        feeder.start(_FakeProcess(_FakeStdin()))
        assert _FakeCamera.instances[0].use_sdk_decode_calls == [False]
        feeder.stop()

    def test_camera_without_set_use_sdk_decode_is_tolerated(self, fake_hikcamera):
        # 模拟旧版 SDK：相机对象不存在 set_use_sdk_decode 方法
        class _BareCamera:
            instances: list = []

            def __init__(self, sn):
                self.sn = sn
                _BareCamera.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def open(self, mode):  # noqa: ARG002
                pass

            def start_grabbing(self, callback=None, output_format=None, on_exception=None):  # noqa: ARG002
                pass

            def stop_grabbing(self):
                pass

            def get_frame(self, timeout_ms=1000, output_format=None):  # noqa: ARG002
                return np.zeros((480, 640, 3), dtype=np.uint8)

        _install_fake_hikcamera(lambda sn: _BareCamera(sn))
        # 不应抛错
        probe_hikcamera_size("SN001")
