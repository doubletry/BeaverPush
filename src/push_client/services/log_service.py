"""
日志服务
========

使用 loguru 统一管理应用日志，按天轮转并保留最近 3 天的日志文件。

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
    - 添加文件 handler：按天轮转，保留 3 天，编码 UTF-8
    - 添加 stderr handler（仅 WARNING 及以上）用于调试；
      打包后无控制台时自动跳过，避免写入失败异常
    """
    logger.remove()  # 移除默认 handler

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "push_client_{time:YYYY-MM-DD}.log"

    # 文件日志：记录所有级别，按天轮转，保留 3 天
    logger.add(
        str(log_file),
        rotation="00:00",
        retention="3 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        enqueue=True,  # 线程安全
    )

    # stderr 日志：仅在调试时显示
    # 打包后（无控制台）sys.stderr 可能为 None，跳过以免异常
    if sys.stderr is not None:
        try:
            logger.add(
                sys.stderr,
                format="{time:HH:mm:ss} | {level:<8} | {message}",
                level="WARNING",
            )
        except OSError:
            pass  # stderr 不可写（打包环境），静默跳过

    logger.info("日志系统已初始化，日志目录: {}", LOG_DIR)
