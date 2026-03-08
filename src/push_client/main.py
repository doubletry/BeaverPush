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

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from push_client.views.theme import Theme
from push_client.views.main_window import MainWindow
from push_client.controllers.app_controller import AppController


def main():
    """应用程序入口函数。"""
    app = QApplication(sys.argv)
    app.setApplicationName("RTSP 推流客户端")
    app.setQuitOnLastWindowClosed(False)

    # ── 全局字体 ──
    font = QFont(Theme.FONT_FAMILY)
    font.setPointSize(Theme.FONT_SIZE_NORMAL)
    app.setFont(font)

    # ── 全局主题样式表 ──
    app.setStyleSheet(Theme.global_stylesheet())

    # ── 窗口图标 ──
    icon = app.style().standardIcon(app.style().StandardPixmap.SP_MediaPlay)
    app.setWindowIcon(icon)

    # ── 创建 View ──
    window = MainWindow()

    # ── 创建 Controller（自动加载配置、连接信号）──
    controller = AppController(window, app)  # noqa: F841
    controller.setup_tray()

    # ── 显示窗口 ──
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
