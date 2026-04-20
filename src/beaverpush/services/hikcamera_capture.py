"""
海康工业相机捕获模块
====================

通过 `hikcamera <https://github.com/doubletry/HiKCamera>`_ 库连接 Hikvision
工业相机（MVS SDK），以 BGR8 格式回调取帧，并把原始字节通过管道喂给 FFmpeg
``-f rawvideo -pixel_format bgr24`` 输入。

核心组件:
    - :func:`probe_hikcamera_size`  — 短暂打开相机，抓取一帧以确定 (W, H)
    - :class:`HikCameraFeeder`      — 持续回调取帧并写入 FFmpeg stdin

设计要点：
    1. ``hikcamera`` 模块在每个函数内部按需 import，避免在 SDK 缺失的开发/CI
       环境下导入失败影响应用启动。
    2. 通过 :meth:`HikCameraFeeder.set_error_callback` 把 SDK 异常上抛给
       :class:`~beaverpush.services.ffmpeg_service.FFmpegWorker`，再由控制层
       走现有的"源失联"重连流程。
    3. 相机断线时主动关闭 FFmpeg ``stdin``，触发 FFmpeg 进程优雅退出，复用
       ``_on_worker_stopped`` 的默认重连路径。

.. note::
    海康相机 SDK (``MvCameraControl.dll`` / ``libMvCameraControl.so``) 必须
    单独安装，路径可通过 ``HIKCAMERA_SDK_PATH`` 环境变量覆盖。
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

from .log_service import logger


def _make_even(v: int) -> int:
    """将值调整为最近的偶数（FFmpeg/编码器要求宽高为偶数）。"""
    return v if v % 2 == 0 else v + 1


def probe_hikcamera_size(serial_number: str, timeout_ms: int = 3000) -> tuple[int, int]:
    """连接指定 SN 的海康相机并抓取一帧，返回 (宽, 高)。

    Args:
        serial_number: 海康相机序列号。
        timeout_ms:    单帧抓取超时（毫秒）。

    Returns:
        ``(width, height)`` 偶数化后的画面尺寸。

    Raises:
        ImportError: ``hikcamera`` 库或其依赖（含 MVS SDK）未安装。
        RuntimeError: 打开相机或抓取首帧失败（错误已链接原始异常）。
    """
    try:
        from hikcamera import Hik, HikCamera  # type: ignore
    except Exception as exc:
        raise ImportError(
            "未找到 hikcamera 库或 Hikvision MVS SDK，请先安装相应组件"
        ) from exc

    sn = (serial_number or "").strip()
    if not sn:
        raise RuntimeError("海康相机 SN 不能为空")

    try:
        cam_factory = HikCamera.from_serial_number(sn)
    except Exception as exc:
        raise RuntimeError(f"无法定位海康相机（SN={sn}）：{exc}") from exc

    try:
        with cam_factory as cam:
            cam.open(Hik.AccessMode.EXCLUSIVE)
            cam.start_grabbing()
            try:
                frame = cam.get_frame(
                    timeout_ms=timeout_ms,
                    output_format=Hik.OutputFormat.BGR8,
                )
            finally:
                try:
                    cam.stop_grabbing()
                except Exception:
                    logger.debug("探测尺寸时 stop_grabbing 异常，已忽略")
    except Exception as exc:
        raise RuntimeError(f"打开海康相机失败：{exc}") from exc

    if frame is None or frame.ndim < 2:
        raise RuntimeError("海康相机返回了无效的画面数据")

    h, w = int(frame.shape[0]), int(frame.shape[1])
    if w <= 0 or h <= 0:
        raise RuntimeError(f"海康相机返回的画面尺寸非法：{w}x{h}")
    return _make_even(w), _make_even(h)


class HikCameraFeeder:
    """持续从海康工业相机抓帧并通过管道喂给 FFmpeg ``stdin``。

    像素格式：始终请求 ``BGR8``（3 字节/像素），与 FFmpeg
    ``-f rawvideo -pixel_format bgr24`` 对应。

    Usage::

        feeder = HikCameraFeeder(sn="00DA1234567", expected_width=1920,
                                 expected_height=1080)
        feeder.set_error_callback(lambda msg: worker.error_occurred.emit(msg))
        feeder.start(ffmpeg_process)
        ...
        feeder.stop()
    """

    def __init__(
        self,
        sn: str,
        expected_width: int,
        expected_height: int,
        fps: int = 30,
    ):
        self.sn = (sn or "").strip()
        self.fps = fps if fps > 0 else 30
        self._expected_w = _make_even(int(expected_width))
        self._expected_h = _make_even(int(expected_height))
        self._frame_bytes = self._expected_w * self._expected_h * 3
        self._process: subprocess.Popen | None = None
        self._cam = None  # type: ignore[assignment]
        self._cam_ctx_active = False
        self._stopped = False
        self._error_callback: Callable[[str], None] | None = None
        self._stdin_lock = threading.Lock()
        self._reported_error = False

    # ── 配置 ──
    def set_error_callback(self, cb: Callable[[str], None] | None):
        """设置错误回调，用于把 SDK 异常透传到 :class:`FFmpegWorker`。"""
        self._error_callback = cb

    def expected_size(self) -> tuple[int, int]:
        return self._expected_w, self._expected_h

    # ── 生命周期 ──
    def start(self, ffmpeg_process: subprocess.Popen):
        """打开相机并启动回调取帧。

        会同步打开相机，若失败抛出异常，调用方应负责清理 FFmpeg 进程。
        """
        try:
            from hikcamera import Hik, HikCamera  # type: ignore
        except Exception as exc:
            raise ImportError(
                "未找到 hikcamera 库或 Hikvision MVS SDK，请先安装相应组件"
            ) from exc

        if not self.sn:
            raise RuntimeError("海康相机 SN 不能为空")

        self._process = ffmpeg_process
        self._stopped = False
        self._reported_error = False

        try:
            cam_factory = HikCamera.from_serial_number(self.sn)
        except Exception as exc:
            raise RuntimeError(f"无法定位海康相机（SN={self.sn}）：{exc}") from exc

        try:
            cam = cam_factory.__enter__()
        except Exception as exc:
            raise RuntimeError(f"打开海康相机失败：{exc}") from exc

        self._cam = cam
        self._cam_ctx_active = True

        try:
            cam.open(Hik.AccessMode.EXCLUSIVE)
            cam.start_grabbing(
                callback=self._on_frame,
                output_format=Hik.OutputFormat.BGR8,
                on_exception=self._on_exception,
            )
        except Exception as exc:
            self._safe_release_camera()
            raise RuntimeError(f"启动海康相机取流失败：{exc}") from exc

        logger.info(
            "海康相机已启动 sn={} size={}x{} fps={}",
            self.sn, self._expected_w, self._expected_h, self.fps,
        )

    def stop(self):
        """停止取帧并释放相机资源（幂等）。"""
        self._stopped = True
        self._safe_release_camera()

    # ── 回调 ──
    def _on_frame(self, image, info):  # noqa: ARG002
        """SDK 回调：把 numpy ndarray 写入 FFmpeg stdin。"""
        if self._stopped:
            return
        proc = self._process
        if proc is None or proc.stdin is None:
            return

        try:
            data = self._coerce_frame_bytes(image)
        except Exception:
            logger.exception("海康相机帧转换异常")
            return

        with self._stdin_lock:
            if self._stopped:
                return
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                # FFmpeg 已退出 / stdin 已关闭，停止后续写入
                self._stopped = True
                logger.debug("FFmpeg stdin 已关闭，停止海康相机喂帧")

    def _on_exception(self, exc):
        """SDK 异常回调：标记停止并关闭 FFmpeg stdin 触发优雅退出。"""
        message = f"海康相机断开：{exc}"
        logger.warning("{} sn={}", message, self.sn)
        self._stopped = True

        if not self._reported_error and self._error_callback:
            self._reported_error = True
            try:
                self._error_callback(message)
            except Exception:
                logger.exception("海康相机错误回调失败")

        proc = self._process
        if proc is not None and proc.stdin is not None:
            with self._stdin_lock:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

    # ── 内部工具 ──
    def _coerce_frame_bytes(self, image) -> bytes:
        """把回调收到的 numpy 帧转换为符合 FFmpeg 期望尺寸的 bgr24 字节。

        相机一般固定分辨率，但若与探测到的尺寸不一致（例如热插拔后变化），
        进行截断/补零，保证字节长度恒等于 ``expected_w * expected_h * 3``。
        """
        if image is None:
            return b"\x00" * self._frame_bytes

        h = int(image.shape[0]) if image.ndim >= 2 else 0
        w = int(image.shape[1]) if image.ndim >= 2 else 0
        if h <= 0 or w <= 0:
            return b"\x00" * self._frame_bytes

        if w == self._expected_w and h == self._expected_h:
            return bytes(image.tobytes())

        try:
            import numpy as np
        except ImportError:
            # 不太可能：hikcamera 已依赖 numpy，但保险起见返回零帧
            return b"\x00" * self._frame_bytes

        out = np.zeros((self._expected_h, self._expected_w, 3), dtype=np.uint8)
        copy_h = min(h, self._expected_h)
        copy_w = min(w, self._expected_w)
        out[:copy_h, :copy_w] = image[:copy_h, :copy_w]
        return bytes(out.tobytes())

    def _safe_release_camera(self):
        cam = self._cam
        ctx_active = self._cam_ctx_active
        self._cam = None
        self._cam_ctx_active = False
        if cam is not None:
            try:
                cam.stop_grabbing()
            except Exception:
                logger.debug("海康相机 stop_grabbing 异常，已忽略")
            if ctx_active:
                try:
                    cam.__exit__(None, None, None)
                except Exception:
                    logger.debug("海康相机上下文退出异常，已忽略")
