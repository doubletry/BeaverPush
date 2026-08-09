"""
models/source_type_plugin.py -- 视频源类型插件接口
=================================================

定义 SourceTypePlugin Protocol，封装每种视频源类型的差异逻辑。
每种源类型实现一个 Plugin 类，降低添加新源类型时的修改范围。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SourceInputArgs:
    """FFmpeg 输入参数。

    Attributes:
        args: FFmpeg 命令行输入参数列表。
        use_pipe: 是否使用管道模式（stdin 输入）。
        needs_dimensions: 是否需要预先探测分辨率（如海康相机）。
    """
    args: list[str]
    use_pipe: bool = False
    needs_dimensions: bool = False


@dataclass
class PreflightTask:
    """预检任务。

    Attributes:
        stage: 阶段描述文本。
        checker: 检查函数，返回 (ok, message)。
        error_prefix: 错误消息前缀。
    """
    stage: str
    checker: callable
    error_prefix: str


class SourceTypePlugin(Protocol):
    """视频源类型插件接口。

    每种视频源类型实现此接口，封装：
    - 输入参数构建
    - 默认编码器
    - 默认帧率
    - 预检任务
    - 输入验证

    使用方式::

        class CameraPlugin:
            def get_key(self) -> str:
                return "camera"

            def get_display_name(self) -> str:
                return "本地摄像头"

            def build_input_args(self, source_path, width, height, framerate) -> SourceInputArgs:
                ...

            def get_default_codec(self) -> str:
                return "libx264"

            def get_default_framerate(self) -> str:
                return "30"
    """

    def get_key(self) -> str:
        """获取源类型标识符。"""
        ...

    def get_display_name(self) -> str:
        """获取显示名称。"""
        ...

    def build_input_args(
        self,
        source_path: str,
        width: str = "",
        height: str = "",
        framerate: str = "",
    ) -> SourceInputArgs:
        """构建 FFmpeg 输入参数。

        Args:
            source_path: 视频源路径。
            width: 输出宽度。
            height: 输出高度。
            framerate: 帧率。

        Returns:
            SourceInputArgs 对象。
        """
        ...

    def get_default_codec(self) -> str:
        """获取默认编码器。"""
        ...

    def get_default_framerate(self) -> str:
        """获取默认帧率。空字符串表示不设置默认值。"""
        ...

    def validate_source_path(self, source_path: str) -> str | None:
        """验证源路径。返回错误消息或 None。"""
        ...

    def get_preflight_tasks(self, source_path: str) -> list[PreflightTask]:
        """获取预检任务列表。"""
        ...

    def is_text_input(self) -> bool:
        """是否为文本输入类型（需要输入框）。"""
        ...

    def is_device_input(self) -> bool:
        """是否为设备输入类型（需要设备下拉框）。"""
        ...

    def show_reconnect_config(self) -> bool:
        """是否显示重连配置。"""
        ...

    def show_loop_option(self) -> bool:
        """是否显示循环选项。"""
        ...


class VideoPlugin:
    """本地视频源插件。"""

    def get_key(self) -> str:
        return "video"

    def get_display_name(self) -> str:
        return "本地视频"

    def build_input_args(
        self,
        source_path: str,
        width: str = "",
        height: str = "",
        framerate: str = "",
    ) -> SourceInputArgs:
        from ..services.ffmpeg_command import _make_even
        args = ["-re", "-i", source_path]
        return SourceInputArgs(args=args, use_pipe=False)

    def get_default_codec(self) -> str:
        return "copy"

    def get_default_framerate(self) -> str:
        return ""

    def validate_source_path(self, source_path: str) -> str | None:
        import os
        if not os.path.isfile(source_path):
            return "视频文件不存在，请检查路径"
        return None

    def get_preflight_tasks(self, source_path: str) -> list[PreflightTask]:
        return []

    def is_text_input(self) -> bool:
        return True

    def is_device_input(self) -> bool:
        return False

    def show_reconnect_config(self) -> bool:
        return False

    def show_loop_option(self) -> bool:
        return True


class CameraPlugin:
    """本地摄像头源插件。"""

    def get_key(self) -> str:
        return "camera"

    def get_display_name(self) -> str:
        return "本地摄像头"

    def build_input_args(
        self,
        source_path: str,
        width: str = "",
        height: str = "",
        framerate: str = "",
    ) -> SourceInputArgs:
        from ..services.ffmpeg_command import _make_even
        args = []
        if width and height:
            w = _make_even(int(width))
            h = _make_even(int(height))
            args += ["-video_size", f"{w}x{h}"]
        if framerate:
            args += ["-framerate", framerate]
        args += ["-vcodec", "mjpeg", "-f", "dshow", "-i", f"video={source_path}"]
        return SourceInputArgs(args=args, use_pipe=False)

    def get_default_codec(self) -> str:
        return "libx264"

    def get_default_framerate(self) -> str:
        return "30"

    def validate_source_path(self, source_path: str) -> str | None:
        if not source_path.strip():
            return "请选择摄像头设备"
        return None

    def get_preflight_tasks(self, source_path: str) -> list[PreflightTask]:
        return []

    def is_text_input(self) -> bool:
        return False

    def is_device_input(self) -> bool:
        return True

    def show_reconnect_config(self) -> bool:
        return False

    def show_loop_option(self) -> bool:
        return False


class RtspPlugin:
    """RTSP 源插件。"""

    def get_key(self) -> str:
        return "rtsp"

    def get_display_name(self) -> str:
        return "RTSP 源"

    def build_input_args(
        self,
        source_path: str,
        width: str = "",
        height: str = "",
        framerate: str = "",
    ) -> SourceInputArgs:
        from ..services.ffmpeg_command import RTSP_TIMEOUT_US
        args = [
            "-rtsp_transport", "tcp",
            "-timeout", RTSP_TIMEOUT_US,
            "-i", source_path,
        ]
        return SourceInputArgs(args=args, use_pipe=False)

    def get_default_codec(self) -> str:
        return "copy"

    def get_default_framerate(self) -> str:
        return ""

    def validate_source_path(self, source_path: str) -> str | None:
        if not source_path.startswith("rtsp://"):
            return "RTSP 地址格式不正确，应以 rtsp:// 开头"
        return None

    def get_preflight_tasks(self, source_path: str) -> list[PreflightTask]:
        from ..services.device_service import check_rtsp_reachable
        return [
            PreflightTask(
                stage="正在检查 RTSP 源...",
                checker=lambda: check_rtsp_reachable(source_path),
                error_prefix="RTSP 源不可用：",
            )
        ]

    def is_text_input(self) -> bool:
        return True

    def is_device_input(self) -> bool:
        return False

    def show_reconnect_config(self) -> bool:
        return True

    def show_loop_option(self) -> bool:
        return False


class ScreenPlugin:
    """全屏画面源插件。"""

    def get_key(self) -> str:
        return "screen"

    def get_display_name(self) -> str:
        return "全屏画面"

    def build_input_args(
        self,
        source_path: str,
        width: str = "",
        height: str = "",
        framerate: str = "",
    ) -> SourceInputArgs:
        from ..services.ffmpeg_command import _make_even
        if not source_path.startswith("offset:"):
            raise ValueError("屏幕捕获源路径格式错误，应为 offset:x,y,w,h")
        parts = source_path.split(":", 1)[1].split(",")
        if len(parts) != 4:
            raise ValueError("屏幕捕获源路径格式错误，应为 offset:x,y,w,h")
        ow, oh = int(parts[2]), int(parts[3])
        w = _make_even(ow)
        h = _make_even(oh)
        fps = framerate if framerate else "30"
        args = [
            "-use_wallclock_as_timestamps", "1",
            "-f", "rawvideo",
            "-pixel_format", "bgra",
            "-video_size", f"{w}x{h}",
            "-framerate", fps,
            "-i", "pipe:0",
        ]
        return SourceInputArgs(args=args, use_pipe=True)

    def get_default_codec(self) -> str:
        return "libx264"

    def get_default_framerate(self) -> str:
        return ""

    def validate_source_path(self, source_path: str) -> str | None:
        if not source_path.startswith("offset:"):
            return "屏幕捕获源路径格式错误"
        return None

    def get_preflight_tasks(self, source_path: str) -> list[PreflightTask]:
        return []

    def is_text_input(self) -> bool:
        return False

    def is_device_input(self) -> bool:
        return True

    def show_reconnect_config(self) -> bool:
        return False

    def show_loop_option(self) -> bool:
        return False


class WindowPlugin:
    """应用窗口源插件。"""

    def get_key(self) -> str:
        return "window"

    def get_display_name(self) -> str:
        return "应用窗口"

    def build_input_args(
        self,
        source_path: str,
        width: str = "",
        height: str = "",
        framerate: str = "",
    ) -> SourceInputArgs:
        from ..services.ffmpeg_command import _make_even
        from ..services.window_capture import get_window_rect
        if source_path.startswith("hwnd:"):
            hwnd = int(source_path.split(":")[1])
            _, _, w, h = get_window_rect(hwnd)
            w = _make_even(w)
            h = _make_even(h)
            fps = framerate if framerate else "30"
            args = [
                "-use_wallclock_as_timestamps", "1",
                "-f", "rawvideo",
                "-pixel_format", "bgra",
                "-video_size", f"{w}x{h}",
                "-framerate", fps,
                "-i", "pipe:0",
            ]
            return SourceInputArgs(args=args, use_pipe=True)
        else:
            args = ["-f", "gdigrab"]
            args += ["-framerate", framerate if framerate else "30"]
            args += ["-i", f"title={source_path}"]
            return SourceInputArgs(args=args, use_pipe=False)

    def get_default_codec(self) -> str:
        return "libx264"

    def get_default_framerate(self) -> str:
        return "30"

    def validate_source_path(self, source_path: str) -> str | None:
        if not source_path.strip():
            return "请选择窗口"
        return None

    def get_preflight_tasks(self, source_path: str) -> list[PreflightTask]:
        return []

    def is_text_input(self) -> bool:
        return False

    def is_device_input(self) -> bool:
        return True

    def show_reconnect_config(self) -> bool:
        return False

    def show_loop_option(self) -> bool:
        return False


class HikCameraPlugin:
    """海康工业相机源插件。"""

    def get_key(self) -> str:
        return "hikcamera"

    def get_display_name(self) -> str:
        return "海康工业相机"

    def build_input_args(
        self,
        source_path: str,
        width: str = "",
        height: str = "",
        framerate: str = "",
    ) -> SourceInputArgs:
        from ..services.ffmpeg_command import _make_even
        if not (width and height):
            raise ValueError("海康相机源需要先探测画面尺寸")
        ow, oh = int(width), int(height)
        w = _make_even(ow)
        h = _make_even(oh)
        fps = framerate if framerate else "30"
        args = [
            "-use_wallclock_as_timestamps", "1",
            "-f", "rawvideo",
            "-pixel_format", "bgr24",
            "-video_size", f"{w}x{h}",
            "-framerate", fps,
            "-i", "pipe:0",
        ]
        return SourceInputArgs(args=args, use_pipe=True, needs_dimensions=True)

    def get_default_codec(self) -> str:
        return "libx264"

    def get_default_framerate(self) -> str:
        return "30"

    def validate_source_path(self, source_path: str) -> str | None:
        if not source_path.strip():
            return "请输入海康相机 SN"
        return None

    def get_preflight_tasks(self, source_path: str) -> list[PreflightTask]:
        return []

    def is_text_input(self) -> bool:
        return True

    def is_device_input(self) -> bool:
        return False

    def show_reconnect_config(self) -> bool:
        return True

    def show_loop_option(self) -> bool:
        return False


# 源类型插件注册表
_PLUGINS: dict[str, SourceTypePlugin] = {}


def register_plugin(plugin: SourceTypePlugin) -> None:
    """注册源类型插件。"""
    _PLUGINS[plugin.get_key()] = plugin


def get_plugin(key: str) -> SourceTypePlugin | None:
    """获取源类型插件。"""
    return _PLUGINS.get(key)


def get_all_plugins() -> list[SourceTypePlugin]:
    """获取所有已注册的插件。"""
    return list(_PLUGINS.values())


def get_source_types() -> list[tuple[str, str]]:
    """获取所有源类型（key, display_name）。"""
    return [(p.get_key(), p.get_display_name()) for p in _PLUGINS.values()]


# 注册内置插件
register_plugin(VideoPlugin())
register_plugin(CameraPlugin())
register_plugin(RtspPlugin())
register_plugin(ScreenPlugin())
register_plugin(WindowPlugin())
register_plugin(HikCameraPlugin())
