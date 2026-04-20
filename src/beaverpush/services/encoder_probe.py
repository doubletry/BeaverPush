"""
硬件编码器探测
==============

在应用启动时调用 :func:`detect_available_encoders` 探测当前机器实际可用的
视频编码器，UI 只会展示这些编码器供用户选择，避免出现"选了 nvenc 但是机器
没有 N 卡"等运行时报错。

探测流程：
    1. 调用一次 ``ffmpeg -hide_banner -encoders`` 列出 FFmpeg 注册的全部编码器；
       如果某个候选编码器没有出现在输出中，直接判定不可用。
    2. 对剩下的硬件编码器（``*_nvenc`` / ``*_qsv``），用一帧极小的
       ``testsrc`` 实际跑一次编码到 ``-f null``，若返回码为 0 则视为可用。
       这一步可以排除"FFmpeg 编进了 nvenc 但驱动 / 硬件不支持"的场景。
    3. 步骤 2 的硬件探测在线程池内并行执行，避免逐个串行的累计耗时
       拖慢 UI 启动。
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor

from .ffmpeg_path import get_ffmpeg
from .log_service import logger

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 可能展示给用户的全部编码器（顺序即 UI 顺序）。
# 软件编码器认为始终可用；硬件编码器需要实际探测。
SOFTWARE_ENCODERS: tuple[str, ...] = ("libx264", "libx265")
HARDWARE_ENCODERS: tuple[str, ...] = (
    "h264_nvenc", "hevc_nvenc",
    "h264_qsv", "hevc_qsv",
)


def _list_ffmpeg_encoders(timeout: float = 5.0) -> set[str]:
    """一次性获取 ``ffmpeg -encoders`` 中列出的全部编码器名字。

    返回名字集合（取每行第二列）。任何异常都返回空集合，
    避免逐个候选都启动一次 ffmpeg 进程造成的明显启动延迟。
    """
    try:
        result = subprocess.run(
            [get_ffmpeg(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        # 合法的编码器行至少 3 列：flags、name、description...
        if len(parts) >= 2:
            names.add(parts[1])
    return names


def _ffmpeg_lists_encoder(name: str, timeout: float = 5.0) -> bool:
    """判断 FFmpeg 是否注册了某个编码器。

    单次查询使用方便，但内部仍然会启动一次 ffmpeg 进程；
    需要批量判断时请直接使用 :func:`_list_ffmpeg_encoders`。
    """
    return name in _list_ffmpeg_encoders(timeout=timeout)


def _probe_encoder(name: str, timeout: float = 8.0) -> bool:
    """尝试用 1 帧 ``testsrc`` 实际跑一次该编码器，返回是否成功。

    对硬件编码器 (``*_qsv`` / ``*_nvenc``) 显式加上 ``-init_hw_device``，
    强制 FFmpeg 创建对应的硬件会话——若机器上没有 Intel iGPU 或
    NVIDIA GPU，对应的 device init 会失败，进程返回非零，从而避免出现
    "ffmpeg 内置了 QSV 但用户机器只有 N 卡也被探测为可用" 的误报。
    """
    cmd = [get_ffmpeg(), "-hide_banner", "-y"]
    # 硬件初始化前置：不存在对应硬件时 ffmpeg 会直接退出非 0
    if name.endswith("_qsv"):
        cmd += ["-init_hw_device", "qsv=hw:hw_any"]
    elif name.endswith("_nvenc"):
        cmd += ["-init_hw_device", "cuda=cu"]
    cmd += [
        "-f", "lavfi",
        "-i", "testsrc=duration=1:size=320x240:rate=1",
        "-frames:v", "1",
        "-c:v", name,
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    # 即使返回 0，也再扫一遍 stderr 中 device 初始化相关的失败标志，
    # 因为某些 QSV 实现会在软件回退后仍然返回 0。
    stderr_lower = (result.stderr or "").lower()
    bad_markers = (
        "device creation failed",
        "failed to create",
        "cannot load",
        "no device available",
        "error initializing",
        "error creating a mfx session",
    )
    if any(m in stderr_lower for m in bad_markers):
        return False
    return True


def detect_available_encoders() -> list[str]:
    """探测当前机器实际可用的编码器列表。

    返回示例：``["libx264", "libx265", "h264_qsv", "hevc_qsv"]``

    规则：
        * 软件编码器只检查 ``ffmpeg -encoders`` 列表，缺失则跳过。
        * 硬件编码器除列表检查外，还会实际跑一次极短编码确认硬件可用；
          多个硬件编码器并行探测，把整体启动等待时间压缩到「单次最慢探测」。
        * 任何探测异常都视为该编码器不可用，不抛出。
    """
    listed = _list_ffmpeg_encoders()
    available: list[str] = []

    for name in SOFTWARE_ENCODERS:
        if name in listed:
            available.append(name)
        else:
            logger.info("FFmpeg 未注册编码器 {}，跳过", name)

    # 只对存在于 -encoders 列表中的硬件编码器执行实际探测，
    # 其余直接跳过；剩余的多个候选并行跑，避免串行累计延迟拖慢启动。
    hw_candidates = [n for n in HARDWARE_ENCODERS if n in listed]
    skipped = [n for n in HARDWARE_ENCODERS if n not in listed]
    for name in skipped:
        logger.info("FFmpeg 未注册硬件编码器 {}，跳过", name)

    if hw_candidates:
        with ThreadPoolExecutor(max_workers=len(hw_candidates)) as ex:
            results = dict(zip(hw_candidates, ex.map(_probe_encoder, hw_candidates)))
        for name in HARDWARE_ENCODERS:
            if name not in results:
                continue
            if results[name]:
                available.append(name)
                logger.info("硬件编码器 {} 可用", name)
            else:
                logger.info("硬件编码器 {} 探测失败，硬件/驱动可能不支持", name)

    return available
