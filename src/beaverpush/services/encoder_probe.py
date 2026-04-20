"""
硬件编码器探测
==============

在应用启动时调用 :func:`detect_available_encoders` 探测当前机器实际可用的
视频编码器，UI 只会展示这些编码器供用户选择，避免出现"选了 nvenc 但是机器
没有 N 卡"等运行时报错。

探测流程：
    1. 调用一次 ``ffmpeg -hide_banner -encoders`` 列出 FFmpeg 注册的全部编码器；
       如果某个候选编码器没有出现在输出中，直接判定不可用。
    2. 通过操作系统接口（Windows: ``Get-CimInstance Win32_VideoController``；
       Linux: ``lspci``）枚举显示适配器厂商。若机器上没有 Intel GPU，
       ``*_qsv`` 立即判定不可用；没有 NVIDIA GPU，``*_nvenc`` 立即判定
       不可用。这一步是为了拦截 libmfx 软件回退导致的 QSV 误判——
       仅靠 ``ffmpeg`` 探测在某些 Windows 构建上即使没有 Intel iGPU
       也会"探测成功"，但真正推流时仍会以 ``MFX session: -9`` 失败。
    3. 对剩下的硬件编码器，用一帧极小的
       ``testsrc`` 实际跑一次编码到 ``-f null``，若返回码为 0 则视为可用。
       这一步可以排除"FFmpeg 编进了 nvenc 但驱动 / 硬件不支持"的场景。
    4. 步骤 3 的硬件探测在线程池内并行执行，避免逐个串行的累计耗时
       拖慢 UI 启动。

若步骤 2 因 OS 不支持 / 命令不存在等原因无法判定，则跳过该步并退回
到仅依赖 ffmpeg probe 的旧行为，避免误把可用编码器隐藏掉。
"""

from __future__ import annotations

import subprocess
import sys
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

# 硬件编码器 → 必须存在的 GPU 厂商标签（小写）。
CODEC_REQUIRED_VENDOR: dict[str, str] = {
    "h264_nvenc": "nvidia",
    "hevc_nvenc": "nvidia",
    "h264_qsv": "intel",
    "hevc_qsv": "intel",
}


def _classify_gpu_vendor(name: str) -> str | None:
    """根据 GPU 名字字符串归类为厂商标签。无法归类时返回 ``None``。"""
    n = name.lower()
    if "intel" in n:
        return "intel"
    if "nvidia" in n or "geforce" in n or "quadro" in n or "tesla" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n or "ati " in n:
        return "amd"
    return None


def _detect_gpu_vendors_windows(timeout: float) -> set[str] | None:
    """枚举 Windows 显示适配器厂商。先尝试 PowerShell（Win10/11 通用），
    再退回到 ``wmic``（旧机器）。任意一个命令成功即返回结果集合。
    全部失败时返回 ``None`` 表示"无法判断"。
    """
    cmds = (
        [
            "powershell.exe", "-NoProfile", "-Command",
            "Get-CimInstance Win32_VideoController | "
            "Select-Object -ExpandProperty Name",
        ],
        ["wmic", "path", "win32_VideoController", "get", "name", "/value"],
    )
    for cmd in cmds:
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if r.returncode != 0 or not r.stdout:
            continue
        vendors: set[str] = set()
        for raw in r.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            # ``wmic /value`` 输出形如 ``Name=Intel(R) UHD Graphics``
            if "=" in line and line.lower().startswith("name="):
                line = line.split("=", 1)[1]
            vendor = _classify_gpu_vendor(line)
            if vendor:
                vendors.add(vendor)
        return vendors
    return None


def _detect_gpu_vendors_linux(timeout: float) -> set[str] | None:
    """使用 ``lspci`` 枚举 Linux 显示设备厂商，命令缺失或失败则返回 ``None``。"""
    try:
        r = subprocess.run(
            ["lspci"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    vendors: set[str] = set()
    for line in r.stdout.splitlines():
        ll = line.lower()
        if (
            "vga compatible controller" not in ll
            and "3d controller" not in ll
            and "display controller" not in ll
        ):
            continue
        vendor = _classify_gpu_vendor(line)
        if vendor:
            vendors.add(vendor)
    return vendors


def detect_gpu_vendors(timeout: float = 5.0) -> set[str] | None:
    """检测当前机器上的 GPU 厂商集合，例如 ``{"intel", "nvidia"}``。

    返回:
        * ``set`` —— 检测成功，结果可能为空集合（确实没有任何显示适配器）；
        * ``None`` —— 在当前操作系统上无法判断（未知 OS / 命令不可用 /
          全部探测命令失败），调用方应回退到"只看 ffmpeg probe"的行为，
          以免错把可用编码器隐藏掉。
    """
    try:
        if sys.platform.startswith("win"):
            return _detect_gpu_vendors_windows(timeout)
        if sys.platform.startswith("linux"):
            return _detect_gpu_vendors_linux(timeout)
    except Exception:  # pragma: no cover - 防御式：任何异常都视为无法判定
        return None
    return None


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

    注意 ``-pix_fmt yuv420p`` 是必须的：``testsrc`` 默认输出 ``rgb24`` →
    libavfilter 自动 negotiate 成 ``gbrp``，会让 NVENC 走 ``High 4:4:4``
    profile。这个 profile 在部分驱动/显卡组合下不被支持（或与并发会话冲突），
    导致探测失败而误判 ``h264_nvenc`` 不可用；而真正推流时我们用的是
    ``yuv420p`` 是受支持的，所以这里强制对齐成 ``yuv420p`` 才能反映真实可用性。
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
        "-pix_fmt", "yuv420p",
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

    # OS 层硬件检查：在调用 ffmpeg probe 之前先按 GPU 厂商裁剪，
    # 避免 libmfx 软件回退导致 QSV 被误判为可用。无法判断时（vendors is None）
    # 不做任何裁剪，回退到 ffmpeg probe 的旧行为。
    vendors = detect_gpu_vendors()
    if vendors is not None:
        logger.info("检测到 GPU 厂商: {}", sorted(vendors) or "(无)")
        filtered: list[str] = []
        for name in hw_candidates:
            required = CODEC_REQUIRED_VENDOR.get(name)
            if required is not None and required not in vendors:
                logger.info(
                    "未检测到 {} GPU，跳过硬件编码器 {}", required, name,
                )
                continue
            filtered.append(name)
        hw_candidates = filtered

    # NVENC 没有"软回退"问题（不像 libmfx 在没有 Intel iGPU 时会假装成功），
    # 因此当 OS 层已经确认 NVIDIA GPU 在场、且 ffmpeg 也注册了 nvenc 时，
    # 直接信任并跳过实跑探测——后者依赖具体驱动状态/并发 NVENC 会话数，
    # 容易产生假阴性（例如 testsrc 触发 High 4:4:4 profile，或瞬时会话占满），
    # 让用户看不到本机本应可用的 nvenc 选项。
    nvenc_trusted: set[str] = set()
    if vendors is not None and "nvidia" in vendors:
        for name in list(hw_candidates):
            if name.endswith("_nvenc"):
                nvenc_trusted.add(name)
                hw_candidates.remove(name)
                available.append(name)
                logger.info(
                    "检测到 NVIDIA GPU 且 FFmpeg 已注册 {}，信任厂商检测，"
                    "跳过实跑探测", name,
                )

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
