"""
FFmpeg 推流服务模块（向后兼容包装器）
====================================

本模块已拆分为三个子模块：
    - :mod:`services.rtsp_url`      — RTSP URL 构建与规范化
    - :mod:`services.ffmpeg_command` — FFmpeg 命令构建与错误映射
    - :mod:`services.ffmpeg_worker`  — FFmpegWorker QThread 进程管理

为保持向后兼容，所有公开符号仍从本模块导出。
新代码建议直接导入子模块。

架构::

    Controller
        │
        ├─▶ build_ffmpeg_command()  → list[str]
        │
        └─▶ FFmpegWorker (QThread)
              ├── status_changed  (str)    → View.set_status()
              ├── error_occurred  (str)    → View.show_error()
              ├── progress_info   (dict)   → View.set_progress()
              └── stopped         ()       → Controller._on_worker_stopped()
"""

# 向后兼容：从子模块导入所有公开符号
from .rtsp_url import (
    mask_sensitive_cmd as _mask_sensitive_cmd,
    normalize_rtsp_server,
    build_authenticated_rtsp_url,
)
from .ffmpeg_command import (
    CREATE_NO_WINDOW,
    RTSP_TIMEOUT_US,
    _make_even,
    _nvenc_supports_new_presets,
    _low_latency_encode_args,
    build_ffmpeg_command,
    friendly_error,
    check_rtsp_server_reachable,
)
from .ffmpeg_worker import (
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    RTSP_STARTUP_TIMEOUT_SECONDS,
    READY_LINE_KEYWORDS,
    FFmpegWorker,
)

# 保持原有的模块级变量引用
_RTSP_CRED_RE = __import__('re').compile(
    r"(rtsp://[^:/@\s]+:)([^@\s]+)(@)", __import__('re').IGNORECASE
)


def _mask_sensitive_cmd_compat(cmd: list[str]) -> str:
    """向后兼容：返回 ffmpeg 命令行的可读字符串，并把 RTSP URL 中的密码替换为 ``***``。"""
    return _mask_sensitive_cmd(cmd)


# 导出所有原有符号
__all__ = [
    # 常量
    "CREATE_NO_WINDOW",
    "RTSP_TIMEOUT_US",
    "DEFAULT_STARTUP_TIMEOUT_SECONDS",
    "RTSP_STARTUP_TIMEOUT_SECONDS",
    "READY_LINE_KEYWORDS",
    # 函数
    "_mask_sensitive_cmd",
    "_make_even",
    "_nvenc_supports_new_presets",
    "_low_latency_encode_args",
    "normalize_rtsp_server",
    "build_authenticated_rtsp_url",
    "build_ffmpeg_command",
    "friendly_error",
    "check_rtsp_server_reachable",
    # 类
    "FFmpegWorker",
]
