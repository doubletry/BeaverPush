"""
日志服务
========

使用 loguru 统一管理应用日志，自动写入文件并按大小轮转。

日志文件位置:
    - Windows: ``%APPDATA%/PushClient/logs/``
    - 其他:    ``~/PushClient/logs/``

使用方式::

    from push_client.services.log_service import logger

    logger.info("推流已启动")
    logger.error("连接失败: {}", err)
"""

import os
import sys
from pathlib import Path

from loguru import logger

LOG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PushClient" / "logs"


def setup_logging():
    """初始化日志配置。

    - 移除 loguru 默认的 stderr handler
    - 添加文件 handler：按 5MB 轮转，保留最近 3 个文件，编码 UTF-8
    - 添加 stderr handler（仅 WARNING 及以上）用于调试
    """
    logger.remove()  # 移除默认 handler

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "push_client.log"

    # 文件日志：记录所有级别
    logger.add(
        str(log_file),
        rotation="5 MB",
        retention=3,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        enqueue=True,  # 线程安全
    )

    # stderr 日志：仅在调试时显示
    logger.add(
        sys.stderr,
        format="{time:HH:mm:ss} | {level:<8} | {message}",
        level="WARNING",
    )

    logger.info("日志系统已初始化，日志目录: {}", LOG_DIR)
