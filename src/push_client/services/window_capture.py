"""
窗口捕获模块
=============

使用 Win32 API 实时捕获指定窗口的画面，并通过管道喂给 FFmpeg。

捕获策略（双模式自动切换）:
    1. **PrintWindow** (``PW_RENDERFULLCONTENT``) — 首选方案，
       适用于大部分窗口，即使窗口被遮挡也能捕获到正确画面。
    2. **BitBlt** — 备用方案，当 PrintWindow 返回黑屏时自动回退。
       适用于使用 DirectComposition 渲染的窗口（如微信）。
       缺点是窗口被遮挡时会截取到遮挡物的画面。

核心组件:
    - :func:`get_window_rect`                  — 获取窗口实际矩形（优先 DWM）
    - :func:`capture_window_frame_printwindow` — PrintWindow 单帧捕获
    - :func:`capture_window_frame_bitblt`      — BitBlt 单帧捕获
    - :func:`capture_window_frame`             — 自动选择模式的单帧捕获
    - :class:`WindowCaptureFeeder`             — 持续喂帧线程

像素格式:
    所有函数返回的原始数据均为 **BGRA** 格式（每像素 4 字节），
    与 FFmpeg ``rawvideo -pixel_format bgra`` 对应。

.. note::
    本模块仅支持 Windows 平台（依赖 ``ctypes.windll``）。
"""

import ctypes
import ctypes.wintypes
import time
import subprocess
import threading

from .log_service import logger

# Win32 常量
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
PW_CLIENTONLY = 1
PW_RENDERFULLCONTENT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 3),
    ]


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """获取窗口的实际可见矩形。

    优先使用 DWM (``DwmGetWindowAttribute``) 获取扩展帧边界，
    回退到 ``GetWindowRect``。DWM 方式能排除窗口阴影区域，
    得到更准确的可见区域。

    Args:
        hwnd: 窗口句柄。

    Returns:
        ``(left, top, width, height)`` 元组。
    """

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rect = RECT()
    try:
        dwmapi = ctypes.windll.dwmapi
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect),
        )
        if hr == 0:
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w > 0 and h > 0:
                return rect.left, rect.top, w, h
    except Exception:
        pass

    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    return rect.left, rect.top, w, h


def _make_even(v: int) -> int:
    return v if v % 2 == 0 else v + 1


def _extract_pixels(dc, bitmap, w: int, h: int) -> bytes:
    """从 DC 和 bitmap 提取 BGRA 像素数据"""
    gdi32 = ctypes.windll.gdi32
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # 负值 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf_size = w * h * 4
    buf = ctypes.create_string_buffer(buf_size)
    gdi32.GetDIBits(dc, bitmap, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    return bytes(buf)


def _is_frame_blank(data: bytes, sample_step: int = 4096) -> bool:
    """快速检测帧是否全黑（采样检测），用于判断 PrintWindow 是否有效"""
    length = len(data)
    if length == 0:
        return True
    # 每隔 sample_step 字节检查 RGB 通道（BGRA 格式，每4字节一个像素）
    for offset in range(0, length - 3, sample_step):
        b, g, r = data[offset], data[offset + 1], data[offset + 2]
        if b > 5 or g > 5 or r > 5:
            return False
    return True


def capture_window_frame_printwindow(hwnd: int, w: int, h: int) -> bytes | None:
    """使用 PrintWindow 捕获窗口（适用于大多数窗口，但微信等可能返回黑屏）"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None

    try:
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)

        result = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not result:
            gdi32.SelectObject(mem_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            return None

        data = _extract_pixels(mem_dc, bitmap, w, h)
        gdi32.SelectObject(mem_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        return data
    finally:
        user32.ReleaseDC(hwnd, hwnd_dc)


def capture_window_frame_bitblt(hwnd: int) -> tuple[bytes, int, int] | None:
    """使用 BitBlt 从屏幕 DC 截取窗口区域（适用于微信等 DirectComposition 窗口）

    这种方式从屏幕上截取窗口所在位置的画面，
    适用于 PrintWindow 返回黑屏的情况。
    缺点：窗口被遮挡时会截取到遮挡物。
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    left, top, w, h = get_window_rect(hwnd)
    if w <= 0 or h <= 0:
        return None

    w = _make_even(w)
    h = _make_even(h)

    # 从屏幕 DC 截取
    screen_dc = user32.GetDC(0)  # 0 = 整个屏幕
    if not screen_dc:
        return None

    try:
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)

        # 从屏幕 DC 复制窗口区域
        gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, left, top, SRCCOPY)

        data = _extract_pixels(mem_dc, bitmap, w, h)
        gdi32.SelectObject(mem_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        return data, w, h
    finally:
        user32.ReleaseDC(0, screen_dc)


def capture_window_frame(hwnd: int, use_bitblt_fallback: bool = False
                         ) -> tuple[bytes, int, int] | None:
    """捕获窗口一帧画面，返回 (BGRA_raw_data, width, height)

    Args:
        hwnd: 窗口句柄
        use_bitblt_fallback: 是否已知需要使用 BitBlt 模式（跳过 PrintWindow 尝试）
    """
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return None

    _, _, w, h = get_window_rect(hwnd)
    if w <= 0 or h <= 0:
        return None

    w = _make_even(w)
    h = _make_even(h)

    # 如果已知需要 bitblt（比如之前检测到 PrintWindow 黑屏），直接使用
    if use_bitblt_fallback:
        return capture_window_frame_bitblt(hwnd)

    # 先尝试 PrintWindow
    data = capture_window_frame_printwindow(hwnd, w, h)
    if data is not None:
        return data, w, h

    # PrintWindow 失败，回退到 BitBlt
    return capture_window_frame_bitblt(hwnd)


class WindowCaptureFeeder:
    """持续捕获窗口画面，通过管道喂给 FFmpeg stdin。

    工作流程:
        1. 调用 :meth:`start` 传入 FFmpeg 进程，启动后台喂帧线程
        2. 第一帧时自动检测 PrintWindow 是否返回全黑帧
        3. 如果全黑（如微信窗口），后续帧自动切换到 BitBlt 模式
        4. 按指定 FPS 持续截帧并写入 ``process.stdin``
        5. 窗口尺寸变化时自动裁剪/填充以保持与初始尺寸一致

    Usage::

        feeder = WindowCaptureFeeder(hwnd=12345, fps=30)
        feeder.start(ffmpeg_process)
        ...
        feeder.stop()
    """

    def __init__(self, hwnd: int, fps: int = 30):
        self.hwnd = hwnd
        self.fps = fps
        self._running = False
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._current_w = 0
        self._current_h = 0
        self._use_bitblt = False  # 是否使用 BitBlt 模式

    def get_initial_size(self) -> tuple[int, int]:
        """获取窗口初始尺寸（偶数化）"""
        _, _, w, h = get_window_rect(self.hwnd)
        return _make_even(w), _make_even(h)

    def start(self, ffmpeg_process: subprocess.Popen):
        """开始喂帧"""
        self._process = ffmpeg_process
        self._running = True
        self._current_w, self._current_h = self.get_initial_size()
        self._use_bitblt = False
        self._thread = threading.Thread(target=self._feed_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止喂帧"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _feed_loop(self):
        interval = 1.0 / self.fps
        first_frame = True

        while self._running and self._process and self._process.poll() is None:
            start_time = time.perf_counter()
            try:
                result = capture_window_frame(
                    self.hwnd, use_bitblt_fallback=self._use_bitblt
                )
                if result is None:
                    time.sleep(interval)
                    continue

                data, w, h = result

                # 第一帧检测是否黑屏，如果是则切换模式
                if first_frame:
                    first_frame = False
                    if not self._use_bitblt and _is_frame_blank(data):
                        self._use_bitblt = True
                        # 用新模式重新截取
                        result2 = capture_window_frame_bitblt(self.hwnd)
                        if result2 is not None:
                            data, w, h = result2

                # 尺寸变化时调整帧
                if w != self._current_w or h != self._current_h:
                    data = self._resize_frame(
                        data, w, h, self._current_w, self._current_h
                    )

                self._process.stdin.write(data)
                self._process.stdin.flush()

            except (BrokenPipeError, OSError):
                logger.debug("窗口捕获管道已关闭")
                break
            except Exception:
                logger.exception("窗口捕获循环异常")
                break

            elapsed = time.perf_counter() - start_time
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    @staticmethod
    def _resize_frame(
        data: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int
    ) -> bytes:
        """简单处理帧大小不匹配：截取或填充黑色"""
        bpp = 4  # BGRA
        src_stride = src_w * bpp
        dst_stride = dst_w * bpp

        result = bytearray(dst_w * dst_h * bpp)
        copy_w = min(src_w, dst_w) * bpp
        copy_h = min(src_h, dst_h)

        for y in range(copy_h):
            src_offset = y * src_stride
            dst_offset = y * dst_stride
            result[dst_offset:dst_offset + copy_w] = data[
                src_offset:src_offset + copy_w
            ]

        return bytes(result)


# ==================================================================
#  屏幕捕获（BitBlt 管道模式，避免 gdigrab 鼠标闪烁）
# ==================================================================

DI_NORMAL = 0x0003


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", ctypes.wintypes.POINT),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.wintypes.BOOL),
        ("xHotspot", ctypes.wintypes.DWORD),
        ("yHotspot", ctypes.wintypes.DWORD),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


def _get_cursor_snapshot():
    """获取当前光标的快照信息（位置、热点、图标副本）。

    通过 CopyIcon 复制光标句柄，确保在后续绘制时句柄不会被系统回收。

    Returns:
        ``(hCursorCopy, draw_x, draw_y)`` 或 ``None``（光标不可见/获取失败）。
        调用方使用完毕后必须调用 ``DestroyIcon(hCursorCopy)`` 释放资源。
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return None
    if not (ci.flags & 0x00000001) or not ci.hCursor:  # CURSOR_SHOWING
        return None

    # 复制光标句柄，使其不受系统光标切换影响
    h_copy = user32.CopyIcon(ci.hCursor)
    if not h_copy:
        return None

    screen_x = ci.ptScreenPos.x
    screen_y = ci.ptScreenPos.y
    hotspot_x = 0
    hotspot_y = 0

    icon_info = ICONINFO()
    if user32.GetIconInfo(h_copy, ctypes.byref(icon_info)):
        hotspot_x = icon_info.xHotspot
        hotspot_y = icon_info.yHotspot
        if icon_info.hbmMask:
            gdi32.DeleteObject(icon_info.hbmMask)
        if icon_info.hbmColor:
            gdi32.DeleteObject(icon_info.hbmColor)

    return h_copy, screen_x - hotspot_x, screen_y - hotspot_y


def capture_screen_frame(x: int, y: int, w: int, h: int) -> bytes | None:
    """使用 BitBlt 捕获屏幕区域并绘制鼠标光标。

    流程：
        1. 调用 :func:`_get_cursor_snapshot` 获取光标快照（CopyIcon 副本）
        2. BitBlt(SRCCOPY) 捕获屏幕内容（不含硬件光标，不闪烁）
        3. DrawIconEx 将光标副本绘制到离屏 DC
        4. DestroyIcon 释放光标副本

    SRCCOPY 不含 CAPTUREBLT，避免系统在 BitBlt 期间隐藏硬件光标
    导致显示器鼠标闪烁。CopyIcon 确保光标句柄在整个绘制过程中保持有效，
    杜绝因句柄失效导致的推流画面鼠标闪烁。

    Args:
        x: 屏幕区域左上角 X 坐标
        y: 屏幕区域左上角 Y 坐标
        w: 捕获宽度（像素，应为偶数）
        h: 捕获高度（像素，应为偶数）

    Returns:
        BGRA 格式原始像素数据，捕获失败时返回 None。
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # ① 在 BitBlt 之前获取光标快照，减少时序差异
    cursor_snap = _get_cursor_snapshot()

    screen_dc = user32.GetDC(0)
    if not screen_dc:
        if cursor_snap:
            user32.DestroyIcon(cursor_snap[0])
        return None

    try:
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)

        # ② BitBlt(SRCCOPY)：捕获屏幕内容，不含硬件光标
        gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, SRCCOPY)

        # ③ 用光标副本在离屏 DC 上绘制鼠标
        if cursor_snap:
            h_cursor, abs_x, abs_y = cursor_snap
            draw_x = abs_x - x
            draw_y = abs_y - y
            if -64 <= draw_x < w and -64 <= draw_y < h:
                user32.DrawIconEx(
                    mem_dc, draw_x, draw_y, h_cursor,
                    0, 0, 0, 0, DI_NORMAL,
                )
            user32.DestroyIcon(h_cursor)

        data = _extract_pixels(mem_dc, bitmap, w, h)
        gdi32.SelectObject(mem_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        return data
    finally:
        user32.ReleaseDC(0, screen_dc)


class ScreenCaptureFeeder:
    """持续捕获屏幕区域，通过管道喂给 FFmpeg stdin。

    使用 BitBlt + DrawIconEx 替代 gdigrab，彻底解决鼠标闪烁问题。

    Usage::

        feeder = ScreenCaptureFeeder(x=0, y=0, w=1920, h=1080, fps=30)
        feeder.start(ffmpeg_process)
        ...
        feeder.stop()
    """

    def __init__(self, x: int, y: int, w: int, h: int, fps: int = 30):
        self.x = x
        self.y = y
        self.w = _make_even(w)
        self.h = _make_even(h)
        self.fps = fps
        self._running = False
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def start(self, ffmpeg_process: subprocess.Popen):
        """开始喂帧。"""
        self._process = ffmpeg_process
        self._running = True
        self._thread = threading.Thread(target=self._feed_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止喂帧。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _feed_loop(self):
        interval = 1.0 / self.fps
        while self._running and self._process and self._process.poll() is None:
            start_time = time.perf_counter()
            try:
                data = capture_screen_frame(self.x, self.y, self.w, self.h)
                if data:
                    self._process.stdin.write(data)
                    self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                logger.debug("屏幕捕获管道已关闭")
                break
            except Exception:
                # 截图异常时跳过当前帧，继续尝试
                logger.exception("屏幕捕获帧时发生未预期异常，将跳过该帧并继续尝试")

            elapsed = time.perf_counter() - start_time
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
