"""
RTSP 推流客户端入口
===================

启动流程::

    1. 创建 QApplication，应用 Catppuccin Mocha 全局样式
    2. 创建 MainWindow（View）
    3. 创建 AppController（Controller），自动加载配置
    4. 初始化系统托盘
    5. 进入 Qt 事件循环

架构概览::

    ┌──────────┐      信号       ┌──────────────┐     调用      ┌──────────┐
    │  Views   │ ──────────────→ │ Controllers  │ ────────────→ │ Models   │
    │(QWidgets)│ ←────────────── │ (QObject)    │ ←──────────── │ Services │
    └──────────┘   set_* 方法    └──────────────┘    返回值      └──────────┘

退出流程:
    AppController._cleanup_and_quit():
        save_config() → force_stop() → tray.hide() → app.quit()
"""

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from push_client import APP_NAME, APP_ICON_PATH
from push_client.views.theme import Theme
from push_client.views.main_window import MainWindow
from push_client.controllers.app_controller import AppController
from push_client.services.log_service import setup_logging, logger
from push_client.services.single_instance import SingleInstanceGuard


def main():
    """应用程序入口函数。"""
    setup_logging()
    logger.info("应用启动")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    # ── 单实例保护 ──
    guard = SingleInstanceGuard("BeaverPush-SingleInstance", parent=app)
    if not guard.try_start():
        logger.info("检测到已有实例运行，发送激活消息后退出")
        return

    # ── 全局字体 ──
    font = QFont(Theme.FONT_FAMILY)
    font.setPointSize(Theme.FONT_SIZE_NORMAL)
    app.setFont(font)

    # ── 全局主题样式表（Catppuccin Mocha）──
    app.setStyleSheet(Theme.global_stylesheet())

    # ── 窗口图标 ──
    app.setWindowIcon(QIcon(APP_ICON_PATH))

    # ── 创建 View ──
    window = MainWindow()

    # ── 创建 Controller（自动加载配置、连接信号）──
    controller = AppController(window, app)  # noqa: F841
    controller.setup_tray()

    # ── 单实例激活信号 → 显示窗口 ──
    guard.activated.connect(controller._show_window)

    # ── 显示窗口 ──
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
