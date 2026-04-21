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

    PowerShell 在 Win11 冷启动时可能超过 5 秒，因此本函数内部把每条命令的
    超时强制至少抬到 10 秒，避免 CIM 子系统冷启时被超时打回 ``None`` 后
    fallback 到只看 ffmpeg probe 的旧逻辑（那条路径在没有 vendor 信息时
    会去实跑硬件 probe，反而更容易出现假阴性）。
    """
    timeout = max(timeout, 10.0)
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


def _nvenc_device_specs() -> tuple[str, ...]:
    """返回 NVENC ``-init_hw_device`` 候选规范。

    ``cuda=cu`` 在所有平台都是文档化的写法；这里用同样的 try-list 结构
    主要为了对称，方便日后补充 d3d11/vaapi 后端。
    """
    return ("cuda=cu",)


# rc=0 但 stderr 中出现以下任一标记时，仍判定该编码器不可用。
# 这些标记必须严格指向「硬件初始化 / MFX 会话」失败，避免误伤
# 例如 ``Failed to create a D3D11 device, trying D3D9.`` 这种 FFmpeg
# 在 QSV 路径上常见的回退提示——它打印之后通常仍然能成功跑完。
_HARDWARE_FAILURE_STDERR_MARKERS: tuple[str, ...] = (
    # ``-init_hw_device`` 阶段失败时打印；rc 通常已经非 0，这里兜底
    "device creation failed",
    # libmfx / oneVPL 在 session 创建阶段的标准失败信息
    "error creating a mfx session",
    # FFmpeg 6.x 起 oneVPL 也会用 "initialise" / "initialize" 两种拼写
    "failed to initialise mfx session",
    "failed to initialize mfx session",
    # 显式说明没有可用硬件设备
    "no device available for encoder",
    # MFX dispatcher 自己加载失败（缺驱动 / runtime）。限定 mfx 前缀
    # 避免命中 ``Cannot load avcodec_open2`` 之类无关行
    "cannot load mfx",
)


def _probe_encoder(
    name: str,
    timeout: float = 8.0,
) -> tuple[bool, int | None, str]:
    """尝试用 1 帧 ``testsrc`` 实际跑一次该编码器。

    返回 ``(available, last_returncode, last_stderr)``：
        * ``available`` —— 是否至少有一条 spec 跑成功；
        * 后两个字段是 **最后一次失败** 的进程信息，仅供调用方在 vendor
          已确认存在却仍探测失败时打 WARNING 日志，便于定位驱动 / oneVPL
          问题。``available=True`` 时这两个字段无意义。

    NVENC 仍显式加上 ``-init_hw_device``，强制 FFmpeg 创建 CUDA 设备，
    避免把"编进了 nvenc 但当前驱动 / 会话不可用"误判为可用。

    QSV 则刻意 **不** 前置 ``-init_hw_device``：最新实机日志表明，某些
    Intel iGPU + oneVPL / D3D11 组合在显式 QSV device init 路径下会直接
    以 ``Error creating a MFX session: -9.`` 失败，但用户用普通命令
    ``ffmpeg -i input.mp4 -c:v hevc_qsv ...`` 却能成功。对这类机器，probe
    必须尽量贴近真实使用方式，让 FFmpeg 自己走默认的 QSV 初始化路径。

    输入统一使用 ``testsrc`` + ``-pix_fmt yuv420p``。其中 ``yuv420p`` 对
    NVENC 是必须的：默认 ``rgb24`` 经自动协商后容易变成 ``gbrp``，触发
    ``High 4:4:4`` profile，进而误判 nvenc 不可用；QSV 也可以正常接受
    这条更简单的 probe 路径。
    """
    # 选出该编码器要尝试的硬件设备规范候选；软件编码器为空 tuple 表示一次直跑。
    if name.endswith("_nvenc"):
        device_specs = _nvenc_device_specs()
    else:
        device_specs = ()

    last_returncode: int | None = None
    last_stderr: str = ""

    # 软件编码器：device_specs 为空，循环退化成单次直跑
    for spec in device_specs or (None,):
        cmd = [get_ffmpeg(), "-hide_banner", "-y"]
        if spec is not None:
            cmd += ["-init_hw_device", spec]
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
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            last_returncode = None
            last_stderr = f"{type(e).__name__}: {e}"
            continue

        stderr_text = result.stderr or ""
        if result.returncode == 0:
            stderr_lower = stderr_text.lower()
            if not any(m in stderr_lower for m in _HARDWARE_FAILURE_STDERR_MARKERS):
                return True, result.returncode, stderr_text
            # rc=0 但 stderr 命中明确失败标记（例如 libmfx 软回退后仍打印
            # ``Error creating a MFX session``）。把它当成失败再尝试下一个 spec。
        last_returncode = result.returncode
        last_stderr = stderr_text

    return False, last_returncode, last_stderr


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
            ok, rc, stderr_text = results[name]
            if ok:
                available.append(name)
                logger.info("硬件编码器 {} 可用", name)
            else:
                # 当 OS 层已经确认对应厂商在场，却 probe 失败时，把 ffmpeg
                # stderr 的尾部抬到 WARNING 级别，方便用户/维护者一眼看出
                # 是驱动 / oneVPL / runtime 哪一层挂了，避免再出现「QSV
                # 神秘消失」的盲点。
                required_vendor = CODEC_REQUIRED_VENDOR.get(name)
                vendor_present = (
                    vendors is not None
                    and required_vendor is not None
                    and required_vendor in vendors
                )
                stderr_tail = "\n".join(
                    (stderr_text or "").splitlines()[-20:]
                )
                if vendor_present:
                    logger.warning(
                        "检测到 {} GPU 但硬件编码器 {} 探测失败 "
                        "(returncode={}); ffmpeg stderr 尾部:\n{}",
                        required_vendor, name, rc, stderr_tail,
                    )
                else:
                    logger.info(
                        "硬件编码器 {} 探测失败，硬件/驱动可能不支持 "
                        "(returncode={})", name, rc,
                    )

    return available
