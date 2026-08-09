"""
services/ffmpeg_command.py -- FFmpeg 命令构建
=============================================

从 ffmpeg_service.py 提取的命令构建相关纯函数。
负责根据视频源类型构建完整的 FFmpeg 推流命令行。
"""

from __future__ import annotations

import re
import subprocess

from .ffmpeg_path import get_ffmpeg
from .window_capture import get_window_rect
from .log_service import logger

# Windows-only subprocess flag; on Unix the attribute does not exist and falls back to 0.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# RTSP 输入超时，单位微秒（10 秒）。
RTSP_TIMEOUT_US = "10000000"


def _make_even(v: int) -> int:
    """将值调整为最近的偶数（FFmpeg 要求宽高为偶数）。"""
    return v if v % 2 == 0 else v + 1


def _nvenc_supports_new_presets() -> bool:
    """探测当前 ``ffmpeg`` 的 ``h264_nvenc`` 是否支持 ``p1..p7`` 预设。

    NVENC 在 FFmpeg n5.0 之后才引入 ``-preset p1..p7`` + ``-tune ll/hq/ull``
    这套新预设；较老的发行版（如 n4.x，常见于第三方 PortableApps / 系统
    PATH 上的旧二进制）只认旧预设 ``default/slow/medium/fast/hp/hq/bd/ll/
    llhq/llhp/...``，并且没有 ``-tune`` 选项。如果我们对老版本仍传 ``-preset p1``
    就会直接报 ``Error setting option preset to value p1``，导致硬件加速推流
    无法启动。

    这里跑一次 ``ffmpeg -h encoder=h264_nvenc``，根据帮助输出里有没有出现
    ``p1`` 这个 token 来决定，并把结果缓存到模块级，避免每次 build 命令都
    付出一次进程拉起开销。任何探测异常都按"不支持新预设"处理，回退到
    旧预设是兼容性最高的安全选择。
    """
    global _NVENC_NEW_PRESETS_CACHE
    cached = _NVENC_NEW_PRESETS_CACHE
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            [get_ffmpeg(), "-hide_banner", "-h", "encoder=h264_nvenc"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        # 帮助输出里 ``p1`` 一定独立成行，例如 ``       p1              12 ...``。
        # 用单词边界匹配避免误中 ``cap1`` / ``mp1`` 之类的子串。
        supports = bool(re.search(r"\bp1\b", result.stdout or ""))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        supports = False
    _NVENC_NEW_PRESETS_CACHE = supports
    return supports


# ``_nvenc_supports_new_presets`` 的进程级缓存。``None`` 表示尚未探测。
_NVENC_NEW_PRESETS_CACHE: bool | None = None


def _low_latency_encode_args(codec: str) -> list[str]:
    """为给定编码器返回低延迟相关的 ``-preset`` / ``-tune`` 参数。

    不同编码器对 ``-preset`` 的合法取值完全不同，混用会让 FFmpeg 报
    ``Error setting option preset``。因此这里按编码器分发：

    - ``libx264`` / ``libx265``: ``-preset ultrafast -tune zerolatency``
    - ``h264_nvenc`` / ``hevc_nvenc``:
        * 新版 FFmpeg (>= 5.0)：``-preset p1 -tune ll``（NVENC 新低延迟预设）
        * 老版 FFmpeg (n4.x 等)：``-preset llhp``（``low latency hp``，
          旧预设里语义最接近 ``p1+ll`` 的安全选项；没有 ``-tune``）
    - ``h264_qsv`` / ``hevc_qsv``: ``-preset veryfast``（QSV 没有 ``zerolatency`` tune）
    - 其他编码器：返回空列表，沿用 FFmpeg 默认参数
    """
    if codec in ("libx264", "libx265"):
        return ["-preset", "ultrafast", "-tune", "zerolatency"]
    if codec in ("h264_nvenc", "hevc_nvenc"):
        if _nvenc_supports_new_presets():
            return ["-preset", "p1", "-tune", "ll"]
        # 旧版 nvenc：不能用 p1，也没有 -tune；llhp 等价于 low latency hp。
        return ["-preset", "llhp"]
    if codec in ("h264_qsv", "hevc_qsv"):
        return ["-preset", "veryfast"]
    return []


def build_ffmpeg_command(
    source_type: str,
    source_path: str,
    rtsp_url: str,
    loop: bool = False,
    video_codec: str = "",
    width: str = "",
    height: str = "",
    framerate: str = "",
    bitrate: str = "",
) -> list[str]:
    """根据视频源类型构建完整的 FFmpeg 推流命令行。

    Args:
        source_type: 视频源类型 (``"video"``/``"camera"``/``"rtsp"``/``"screen"``/``"window"``/``"hikcamera"``)
        source_path: 视频源路径。各类型的格式：

            - ``video``  : 文件绝对路径
            - ``camera`` : DirectShow 设备名
            - ``rtsp``   : RTSP 拉流 URL
            - ``screen`` : ``"offset:x,y,w,h"``（屏幕偏移与尺寸）
            - ``window`` : ``"hwnd:<句柄>"`` 或窗口标题
            - ``hikcamera`` : 海康相机 SN（实际帧由 ``HikCameraFeeder`` 写入 stdin）

        rtsp_url:    RTSP 推流目标地址
        loop:        是否循环播放（仅 ``video`` 类型有效）
        video_codec: 视频编码器，空字符串表示自动选择 ``libx264``
        width:       输出宽度（空字符串表示不缩放）
        height:      输出高度
        framerate:   输出帧率
        bitrate:     输出码率（如 ``"2M"``）

    Returns:
        可直接传给 ``subprocess.Popen`` 的命令行参数列表。

    Raises:
        ValueError: 不支持的 ``source_type``。

    Note:
        屏幕捕获统一使用 ``"offset:x,y,w,h"`` 格式（包括主屏幕），
        确保只捕获选定的单个屏幕，而不是整个虚拟桌面。
    """
    # FFmpeg's human-readable stats refresh the same terminal line with ``\r``.
    # A pipe reader using ``readline()`` therefore cannot observe the first
    # encoded frame until the process exits. Request newline-delimited progress
    # on stderr so startup readiness is based on a real frame, not a banner.
    cmd = [get_ffmpeg(), "-y", "-nostats", "-progress", "pipe:2"]

    # ---- 输入部分 ----
    if source_type == "video":
        if loop:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-re", "-i", source_path]

    elif source_type == "camera":
        # [改进] 支持摄像头分辨率设置和 mjpeg 解码
        # 目的：让摄像头以最高分辨率打开（默认 640x480 太低）
        # 思路：通过 -video_size 指定分辨率，-vcodec mjpeg 指定输入解码器
        #       mjpeg 编码在高分辨率下帧率更高（1920x1080@30fps vs yuyv422@5fps）
        if width and height:
            w = _make_even(int(width))
            h = _make_even(int(height))
            cmd += ["-video_size", f"{w}x{h}"]
        if framerate:
            cmd += ["-framerate", framerate]
        # 指定 mjpeg 解码器，DirectShow 会优先选择 mjpeg 编码的高分辨率模式
        cmd += ["-vcodec", "mjpeg"]
        cmd += ["-f", "dshow", "-i", f"video={source_path}"]

    elif source_type == "rtsp":
        cmd += [
            "-rtsp_transport", "tcp",
            "-timeout", RTSP_TIMEOUT_US,
            "-i", source_path,
        ]

    elif source_type == "screen":
        # 屏幕捕获：使用 rawvideo 管道模式，通过 BitBlt + DrawIconEx
        # 替代 gdigrab，彻底解决鼠标闪烁问题
        # source_path 格式: "offset:x,y,w,h"
        if source_path.startswith("offset:"):
            parts = source_path.split(":", 1)[1].split(",")
            if len(parts) != 4:
                raise ValueError("屏幕捕获源路径格式错误，应为 offset:x,y,w,h")
            try:
                ow, oh = int(parts[2]), int(parts[3])
            except ValueError:
                raise ValueError("屏幕捕获源路径格式错误，宽度或高度必须为整数值")
            w = _make_even(ow)
            h = _make_even(oh)
            fps = framerate if framerate else "30"
            cmd += [
                "-use_wallclock_as_timestamps", "1",
                "-f", "rawvideo",
                "-pixel_format", "bgra",
                "-video_size", f"{w}x{h}",
                "-framerate", fps,
                "-i", "pipe:0",
            ]
        else:
            raise ValueError("屏幕捕获源路径格式错误，应为 offset:x,y,w,h")

    elif source_type == "window":
        # 窗口捕获：rawvideo 管道
        if source_path.startswith("hwnd:"):
            hwnd = int(source_path.split(":")[1])
            _, _, w, h = get_window_rect(hwnd)
            w = _make_even(w)
            h = _make_even(h)
            fps = framerate if framerate else "30"
            cmd += [
                "-use_wallclock_as_timestamps", "1",
                "-f", "rawvideo",
                "-pixel_format", "bgra",
                "-video_size", f"{w}x{h}",
                "-framerate", fps,
                "-i", "pipe:0",
            ]
        else:
            input_args = ["-f", "gdigrab"]
            if framerate:
                input_args += ["-framerate", framerate]
            else:
                input_args += ["-framerate", "30"]
            input_args += ["-i", f"title={source_path}"]
            cmd += input_args

    elif source_type == "hikcamera":
        # 海康工业相机：通过 HikCameraFeeder 把 BGR8 帧写入 stdin
        # source_path 为相机 SN；宽高必须由控制器在调用前探测好
        if not (width and height):
            raise ValueError("海康相机源需要先探测画面尺寸")
        try:
            ow, oh = int(width), int(height)
        except ValueError as exc:
            raise ValueError("海康相机源宽高必须为整数") from exc
        w = _make_even(ow)
        h = _make_even(oh)
        fps = framerate if framerate else "30"
        cmd += [
            "-use_wallclock_as_timestamps", "1",
            "-f", "rawvideo",
            "-pixel_format", "bgr24",
            "-video_size", f"{w}x{h}",
            "-framerate", fps,
            "-i", "pipe:0",
        ]

    else:
        raise ValueError(f"不支持的视频源类型: {source_type}")

    # ---- 滤镜 ----
    filters = []
    codec = video_codec if video_codec else "libx264"

    # copy 模式不能使用滤镜；管道源(screen/window/hikcamera)尺寸已在输入参数中指定
    need_scale = (
        width and height
        and codec != "copy"
        and source_type not in ("screen", "window", "hikcamera")
    )
    if need_scale:
        w_val = int(width) if width.isdigit() else width
        h_val = int(height) if height.isdigit() else height
        if isinstance(w_val, int):
            w_val = _make_even(w_val)
        if isinstance(h_val, int):
            h_val = _make_even(h_val)
        filters.append(f"scale={w_val}:{h_val}")

    # ---- 编码 ----
    if source_type in ("screen", "window", "camera", "hikcamera"):
        codec = video_codec if video_codec else "libx264"
        cmd += ["-c:v", codec] + _low_latency_encode_args(codec)
    elif source_type == "rtsp":
        codec = video_codec if video_codec else "libx264"
        cmd += ["-c:v", codec]
        cmd += _low_latency_encode_args(codec)
        if codec == "copy":
            cmd += ["-c:a", "copy"]
    else:
        codec = video_codec if video_codec else "libx264"
        cmd += ["-c:v", codec]
        cmd += _low_latency_encode_args(codec)
        if codec == "copy":
            cmd += ["-c:a", "copy"]

    # ---- 输出参数 ----
    if filters:
        cmd += ["-vf", ",".join(filters)]

    if framerate and source_type not in ("camera", "screen", "window", "hikcamera"):
        cmd += ["-r", framerate]

    if bitrate:
        cmd += ["-b:v", bitrate]

    if codec != "copy":
        cmd += ["-pix_fmt", "yuv420p"]

    # WebRTC 兼容：NVENC / QSV 默认带 B 帧（``-bf -1`` auto），mediamtx 转
    # WebRTC 后浏览器 H.264 实现不支持 B 帧会黑屏；同时 GOP 默认 250 太长，
    # WebRTC 客户端等待首个 IDR 时间过久。这里强制：
    #   * ``-bf 0``：禁用 B 帧
    #   * ``-g <fps*2>``：把关键帧间隔压到 ~2 秒，确保 WebRTC 首帧及时
    #   * h264_nvenc 额外加 ``-profile:v main``：对齐主流浏览器的支持子集
    if codec in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv"):
        try:
            fps_int = int(round(float(framerate))) if framerate else 30
        except (TypeError, ValueError):
            fps_int = 30
        gop = max(1, fps_int * 2)
        cmd += ["-bf", "0", "-g", str(gop)]
        if codec == "h264_nvenc":
            cmd += ["-profile:v", "main"]

    cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", rtsp_url]
    return cmd


def friendly_error(msg: str) -> str:
    """将 FFmpeg 原始错误信息映射为用户友好的中文提示。

    会在原始信息前附加中文说明，方便用户排查问题。
    如果没有匹配到已知关键词，则原样返回。

    Args:
        msg: FFmpeg 输出的错误文本。

    Returns:
        包含中文说明和原始信息的字符串。
    """
    lower = msg.lower()
    # [改进] 添加 i/o error 的友好错误提示
    # 目的：当摄像头打开失败时，显示更友好的中文提示信息
    # 思路：在错误映射表中添加 "i/o error" 关键词及其对应的中文说明
    mapping = [
        ("i/o error", "摄像头 I/O 错误，请检查摄像头是否被其他程序占用或连接是否正常。"),
        ("connection refused", "连接被拒绝，请检查 RTSP 服务器是否已启动。"),
        ("no route to host", "主机不可达，请检查网络连接。"),
        ("timed out", "连接超时，请检查网络。"),
        ("timeout", "连接超时，请检查网络。"),
        ("no such file", "文件不存在，请检查路径。"),
        ("does not exist", "文件不存在，请检查路径。"),
        ("permission denied", "权限不足。"),
        ("could not open", "无法打开源，请检查输入。"),
        ("invalid data", "无效的数据格式。"),
        ("error initializing output stream", "编码器初始化失败，建议宽高设为偶数。"),
        ("incorrect parameters", "编码参数不兼容。"),
        ("海康相机断开", "海康相机已断开，等待重连。"),
        ("海康相机", "海康相机异常，请检查相机连接和 MVS SDK。"),
    ]
    for keyword, friendly in mapping:
        if keyword in lower:
            return f"{friendly}\n\n原始信息:\n{msg}"
    return msg


def check_rtsp_server_reachable(
    rtsp_server: str,
    timeout: int = 10,
    username: str = "",
    auth_secret: str = "",
    machine_name: str = "",
) -> tuple[bool, str]:
    """检测 RTSP 推流服务器是否可达（v2：支持认证 + 三级路径）。"""
    from .rtsp_url import build_authenticated_rtsp_url

    try:
        if username and auth_secret:
            test_url = build_authenticated_rtsp_url(
                rtsp_server,
                [username, machine_name or "_test", "__connection_test__"],
                username=username,
                auth_secret=auth_secret,
            )
        else:
            test_url = build_authenticated_rtsp_url(
                rtsp_server,
                ["__connection_test__"],
            )
    except ValueError as exc:
        return False, str(exc)

    try:
        result = subprocess.run(
            [
                get_ffmpeg(), "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-t", "1",
                "-f", "rtsp", "-rtsp_transport", "tcp",
                test_url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        stderr = result.stderr.lower()
        if result.returncode == 0:
            return True, "连接成功！RTSP 服务器可达。"
        if "401" in stderr or "unauthorized" in stderr:
            return False, "认证失败，请检查用户名和授权码。"
        if "connection refused" in stderr:
            return False, "连接被拒绝，请检查服务器是否启动。"
        if "no route" in stderr or "unreachable" in stderr:
            return False, "主机不可达，请检查网络和地址。"
        if "timeout" in stderr or "timed out" in stderr:
            return False, "连接超时。"
        return False, friendly_error(result.stderr.strip() or "RTSP 服务器不可用")
    except subprocess.TimeoutExpired:
        return False, "连接超时，请检查地址和网络。"
    except FileNotFoundError:
        return False, "未找到 ffmpeg，请确认已安装并添加到 PATH。"
    except Exception as e:
        logger.exception("RTSP 服务器连接测试异常")
        return False, f"测试失败: {e}"
