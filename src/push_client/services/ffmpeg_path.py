"""
FFmpeg 可执行文件路径解析
=========================

自动查找 ``ffmpeg`` / ``ffplay`` / ``ffprobe`` 的完整路径。

查找顺序：
    1. 程序自带的 ``ffmpeg/`` 子目录（安装包内嵌）
    2. 系统 ``PATH`` 环境变量

使用方式::

    from push_client.services.ffmpeg_path import get_ffmpeg, get_ffplay, get_ffprobe

    cmd = [get_ffmpeg(), "-y", "-i", ...]
"""

from __future__ import annotations

import os
import sys
import shutil


def _app_dir() -> str:
    """获取应用程序所在目录。

    打包后 (Nuitka standalone) ``sys.argv[0]`` 指向 exe 所在目录；
    开发模式下同样使用 ``sys.argv[0]`` 所在目录。
    """
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def _find_executable(name: str) -> str:
    """查找可执行文件的完整路径。

    Args:
        name: 可执行文件名（不含 ``.exe`` 后缀）。

    Returns:
        完整路径字符串。如果在内嵌目录和 PATH 中都找不到，
        则返回原始 ``name``，让后续 ``subprocess`` 抛出
        ``FileNotFoundError``。
    """
    exe_name = f"{name}.exe" if os.name == "nt" else name

    # 1. 程序目录下的 ffmpeg/ 子目录
    app_bundled = os.path.join(_app_dir(), "ffmpeg", exe_name)
    if os.path.isfile(app_bundled):
        return app_bundled

    # 2. 程序目录本身（有些用户直接放在同目录）
    app_same_dir = os.path.join(_app_dir(), exe_name)
    if os.path.isfile(app_same_dir):
        return app_same_dir

    # 3. 系统 PATH
    found = shutil.which(name)
    if found:
        return found

    # 4. 回退：返回原始名字，让 subprocess 报错
    return name


def get_ffmpeg() -> str:
    """获取 ``ffmpeg`` 可执行文件路径。"""
    return _find_executable("ffmpeg")


def get_ffplay() -> str:
    """获取 ``ffplay`` 可执行文件路径。"""
    return _find_executable("ffplay")


def get_ffprobe() -> str:
    """获取 ``ffprobe`` 可执行文件路径。"""
    return _find_executable("ffprobe")
