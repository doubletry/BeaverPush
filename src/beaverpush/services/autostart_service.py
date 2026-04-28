"""
开机自启动服务
==============

通过写入 Windows 注册表 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
下名为 ``BeaverPush`` 的字符串值，实现「当前用户登录时自动启动」。

设计要点:
    - 使用 ``HKCU`` 而不是 ``HKLM``，普通用户权限即可写入，无需 UAC 提升。
    - 写入的命令带 ``--minimized`` 参数，让应用启动后只驻留系统托盘，
      由现有 ``StreamConfig.auto_start`` 机制恢复上次推流状态。
    - 仅 Windows 平台支持，其他平台所有方法均为安全的 no-op。
    - 仅依赖标准库 ``winreg``，按平台条件导入，不引入新依赖。

典型用法::

    from beaverpush.services import autostart_service

    if autostart_service.is_supported():
        if user_checked:
            autostart_service.enable()
        else:
            autostart_service.disable()
        current = autostart_service.is_enabled()
"""

from __future__ import annotations

import sys
from pathlib import Path

from .log_service import logger

# 注册表项名（出现在「任务管理器 → 启动」中）
RUN_VALUE_NAME = "BeaverPush"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

# 启动应用时附带的参数，让 main.py 跳过显示主窗口、只驻留托盘
MINIMIZED_FLAG = "--minimized"


def is_supported() -> bool:
    """是否在当前平台支持开机自启动。

    开机自启动功能仅 Windows 支持，其他平台返回 ``False``。
    """
    return sys.platform == "win32"


def _executable_command() -> str:
    """构造写入注册表的命令行字符串。

    打包后 ``sys.executable`` 指向 ``BeaverPush.exe``，直接使用即可；
    开发模式下 ``sys.executable`` 是 Python 解释器，写入的命令会执行
    ``python -m beaverpush --minimized``，便于本地联调。

    返回的命令使用引号包裹可执行文件路径，避免路径含空格时被截断。
    """
    exe = Path(sys.executable).resolve()
    exe_str = str(exe)

    name = exe.name.lower()
    if name in ("python.exe", "pythonw.exe", "python", "pythonw"):
        # 开发模式：用 -m 调用入口模块
        return f'"{exe_str}" -m beaverpush {MINIMIZED_FLAG}'
    return f'"{exe_str}" {MINIMIZED_FLAG}'


def _open_run_key(write: bool):
    """打开 ``HKCU\\...\\Run`` 注册表项。

    Args:
        write: 是否需要写权限。

    Returns:
        winreg key 句柄，调用方负责 ``CloseKey``。
    """
    import winreg  # type: ignore[import-not-found]

    access = winreg.KEY_WRITE if write else winreg.KEY_READ
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)


def is_enabled() -> bool:
    """检测当前是否已设置开机自启动。

    仅判断注册表值是否存在，不校验内容，避免用户手动改过路径时被误判为关闭。
    """
    if not is_supported():
        return False
    try:
        import winreg  # type: ignore[import-not-found]

        with _open_run_key(write=False) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("查询开机自启动注册表失败")
        return False


def get_registered_command() -> str | None:
    """返回当前已写入注册表的命令字符串，未设置时返回 ``None``。"""
    if not is_supported():
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with _open_run_key(write=False) as key:
            value, _vtype = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return value
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("读取开机自启动注册表值失败")
        return None


def enable() -> bool:
    """启用开机自启动。

    无论之前是否已设置，都用当前 ``sys.executable`` 重写一次，
    用以在用户重装 / 换路径后修正注册表里的旧路径。

    Returns:
        ``True`` 表示写入成功；``False`` 表示当前平台不支持或写入失败。
    """
    if not is_supported():
        logger.warning("当前平台不支持开机自启动，已忽略 enable()")
        return False
    try:
        import winreg  # type: ignore[import-not-found]

        command = _executable_command()
        with _open_run_key(write=True) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)
        logger.info("已设置开机自启动: {}", command)
        return True
    except OSError:
        logger.exception("设置开机自启动失败")
        return False


def disable() -> bool:
    """关闭开机自启动；若注册表中本就无该项，视为成功。"""
    if not is_supported():
        logger.warning("当前平台不支持开机自启动，已忽略 disable()")
        return False
    try:
        import winreg  # type: ignore[import-not-found]

        with _open_run_key(write=True) as key:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
                logger.info("已取消开机自启动")
            except FileNotFoundError:
                # 已经不存在，幂等
                pass
        return True
    except OSError:
        logger.exception("取消开机自启动失败")
        return False


def sync(enabled: bool) -> bool:
    """根据配置把注册表与期望状态对齐。

    与 ``enable``/``disable`` 等价，但不区分两者的 logging，方便启动期对账时
    一行调用：``autostart_service.sync(config.launch_at_startup)``。
    """
    return enable() if enabled else disable()


def is_launched_minimized(argv: list[str] | None = None) -> bool:
    """判断进程是否带 ``--minimized`` 启动。"""
    args = argv if argv is not None else sys.argv
    return MINIMIZED_FLAG in args


__all__ = [
    "RUN_VALUE_NAME",
    "RUN_KEY_PATH",
    "MINIMIZED_FLAG",
    "is_supported",
    "is_enabled",
    "get_registered_command",
    "enable",
    "disable",
    "sync",
    "is_launched_minimized",
]
