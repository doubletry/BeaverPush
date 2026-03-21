"""
应用级控制器
============

``AppController`` 是整个应用的顶层控制器，协调：
    - ``MainWindow``（View）的工具栏交互
    - 推流通道集合的增删管理
    - 设备枚举服务调用
    - 配置持久化（加载 / 保存）
    - RTSP 服务器连接测试
    - 应用退出流程

每一路推流通道由一个 ``StreamController`` 管理，
``AppController`` 持有所有 ``StreamController`` 的引用。
"""

from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QAction, QCloseEvent

from ..models.config import (
    AppConfig, StreamConfig, load_config, save_config,
)
from ..services.device_service import (
    list_cameras, list_screens, list_windows,
)
from ..services.ffmpeg_path import get_ffmpeg
from ..views.main_window import MainWindow
from ..views.stream_card import StreamCardView
from .stream_controller import StreamController


class AppController(QObject):
    """应用级控制器。

    管理 MainWindow 的所有用户交互，以及推流通道集合的生命周期。

    Args:
        window: 主窗口实例。
        app: QApplication 实例（用于退出）。
        parent: 父 QObject。
    """

    def __init__(self, window: MainWindow, app: QApplication,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._window = window
        self._app = app
        self._config = load_config()
        self._rtsp_server = self._config.rtsp_server
        self._server_locked = self._config.server_locked
        self._client_id = self._config.client_id
        self._controllers: list[StreamController] = []
        self._tray: QSystemTrayIcon | None = None

        # 同步初始状态到 View
        self._window.set_server(self._rtsp_server)
        self._window.set_server_locked(self._server_locked)
        self._window.set_client_id(self._client_id)

        # 连接 View 信号 → Controller
        self._connect_signals()

        # 加载已保存的通道配置
        self._load_saved_config()

    # ==================================================================
    #  信号连接
    # ==================================================================

    def _connect_signals(self):
        """将 MainWindow 的信号连接到对应的处理方法。"""
        w = self._window
        w.server_changed.connect(self._on_server_changed)
        w.test_clicked.connect(self._on_test)
        w.add_stream_clicked.connect(self.add_stream)
        w.save_config_clicked.connect(self.save_config)
        # 客户端 ID
        w.client_id_changed.connect(self._on_client_id_changed)
        # 全部开始/停止
        w.start_all_clicked.connect(self._on_start_all)
        w.stop_all_clicked.connect(self._on_stop_all)

        # 替换窗口的 closeEvent
        w.closeEvent = self._on_close

    # ==================================================================
    #  工具栏交互处理
    # ==================================================================

    def _on_server_changed(self, url: str):
        """用户修改 RTSP 服务器地址。"""
        self._rtsp_server = url

    def _on_client_id_changed(self, cid: str):
        """用户修改客户端 ID。"""
        self._client_id = cid

    def _on_start_all(self):
        """全部开始推流。"""
        started = 0
        for ctrl in self._controllers:
            if not ctrl.is_streaming:
                ctrl.start_stream()
                started += 1
        self._window.set_status(f"已启动 {started} 路推流")

    def _on_stop_all(self):
        """全部停止推流。"""
        stopped = 0
        for ctrl in self._controllers:
            if ctrl.is_streaming:
                ctrl.stop_stream()
                stopped += 1
        self._window.set_status(f"已停止 {stopped} 路推流")

    def _on_test(self):
        """测试 RTSP 服务器连接。

        使用 FFmpeg 发送一段 1 秒的测试视频到服务器，
        根据返回码和 stderr 判断服务器是否可达。
        """
        if not self._rtsp_server:
            self._window.show_test_result(False, "请输入 RTSP 服务器地址")
            return

        self._window.set_test_button_testing(True)
        self._window.set_status("正在测试连接...")

        try:
            result = subprocess.run(
                [
                    get_ffmpeg(), "-y",
                    "-f", "lavfi", "-i",
                    "testsrc=duration=1:size=320x240:rate=1",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-t", "1",
                    "-f", "rtsp", "-rtsp_transport", "tcp",
                    f"{self._rtsp_server.rstrip('/')}/__connection_test__",
                ],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stderr = result.stderr.lower()
            if result.returncode == 0:
                self._window.show_test_result(True, "连接成功！RTSP 服务器可达。")
            elif "connection refused" in stderr:
                self._window.show_test_result(False, "连接被拒绝，请检查服务器是否启动。")
            elif "no route" in stderr or "unreachable" in stderr:
                self._window.show_test_result(False, "主机不可达，请检查网络和地址。")
            elif "timeout" in stderr:
                self._window.show_test_result(False, "连接超时。")
            else:
                self._window.show_test_result(True, "服务器已响应，连接正常。")
        except subprocess.TimeoutExpired:
            self._window.show_test_result(False, "连接超时，请检查地址和网络。")
        except FileNotFoundError:
            self._window.show_test_result(False, "未找到 ffmpeg，请确认已安装并添加到 PATH。")
        except Exception as e:
            self._window.show_test_result(False, f"测试失败: {e}")
        finally:
            self._window.set_test_button_testing(False)
            self._window.set_status("连接测试完成")

    # ==================================================================
    #  推流通道管理
    # ==================================================================

    def _next_channel_index(self) -> int:
        """计算下一个可用的通道编号（从 0 开始，复用已删除的编号）。"""
        used = {ctrl.channel_index for ctrl in self._controllers}
        idx = 0
        while idx in used:
            idx += 1
        return idx

    def add_stream(self) -> StreamController:
        """添加一路推流通道。

        创建卡片 View + StreamController，挂载到主窗口。
        通道编号会复用已删除通道的编号。

        Returns:
            新创建的 StreamController。
        """
        channel_index = self._next_channel_index()

        # 创建 View（卡片）
        card = StreamCardView(channel_index, self._window)
        self._window.add_card(card)

        # 创建 Controller
        ctrl = StreamController(
            card=card,
            channel_index=channel_index,
            rtsp_server_getter=lambda: self._rtsp_server,
            client_id_getter=lambda: self._client_id,
            parent=self,
        )
        self._controllers.append(ctrl)

        # 连接卡片的"移除"按钮、"刷新"按钮和"源类型切换"
        card.remove_clicked.connect(lambda: self._remove_stream(ctrl))
        card.refresh_clicked.connect(
            lambda: self._refresh_devices(card.get_source_type(), card)
        )
        # 切换视频源类型时自动刷新设备列表
        card.source_type_changed.connect(
            lambda key: self._refresh_devices(key, card)
        )

        self._window.set_status(f"已添加推流通道，共 {len(self._controllers)} 路")
        return ctrl

    def _remove_stream(self, ctrl: StreamController):
        """移除一路推流通道。

        推流中的通道不允许移除。
        """
        if ctrl.is_streaming:
            self._window.set_status("请先停止推流再移除")
            return

        ctrl.force_stop()
        self._window.remove_card(ctrl.card)
        self._controllers.remove(ctrl)
        ctrl.deleteLater()
        self._window.set_status(f"已移除推流通道，剩余 {len(self._controllers)} 路")

    # ==================================================================
    #  设备枚举
    # ==================================================================

    def _refresh_devices(self, source_type: str, card: StreamCardView):
        """根据视频源类型刷新设备列表并填充到卡片下拉框。

        对于非设备类型（video / rtsp）直接跳过。

        Args:
            source_type: ``"camera"`` / ``"screen"`` / ``"window"``
                         （其他值直接返回）。
            card: 目标卡片。
        """
        if source_type not in ("camera", "screen", "window"):
            return

        items: list[tuple[str, str]] = []

        if source_type == "camera":
            cameras = list_cameras()
            if cameras:
                items = [(c.name, c.device_path) for c in cameras]
            else:
                items = [("未检测到摄像头", "")]

        elif source_type == "screen":
            screens = list_screens()
            if screens:
                for s in screens:
                    display = f"{s.name} ({s.width}x{s.height})"
                    value = f"offset:{s.x},{s.y},{s.width},{s.height}"
                    items.append((display, value))
            else:
                items = [("未检测到显示器", "")]

        elif source_type == "window":
            windows = list_windows()
            if windows:
                for w in windows:
                    w_px = w.right - w.left
                    h_px = w.bottom - w.top
                    display = f"{w.title[:50]} ({w_px}x{h_px})"
                    value = f"hwnd:{w.hwnd}"
                    items.append((display, value))
            else:
                items = [("未检测到窗口", "")]

        card.set_device_items(items)

    # ==================================================================
    #  配置持久化
    # ==================================================================

    def save_config(self):
        """保存当前所有通道配置到文件。"""
        self._server_locked = self._window.get_server_locked()
        cfg = AppConfig(
            rtsp_server=self._rtsp_server,
            server_locked=self._server_locked,
            client_id=self._client_id,
            streams=[],
        )
        for ctrl in self._controllers:
            cfg.add_stream(ctrl.to_config())
        save_config(cfg)
        self._window.set_status("配置已保存")

    def _load_saved_config(self):
        """从文件加载已保存的通道配置。

        若通道保存时处于推流状态 (``auto_start=True``)，则在
        所有通道恢复完毕后自动开始推流。使用 ``QTimer.singleShot``
        延迟启动，确保 UI 初始化和信号传播完成。
        """
        if not self._config.streams:
            return

        auto_start_ctrls: list = []

        for stream_data in self._config.streams:
            try:
                cfg = StreamConfig(**stream_data)
                ctrl = self.add_stream()
                ctrl.from_config(cfg)
                # 如果是设备类型，自动刷新设备列表
                if cfg.source_type in ("camera", "screen", "window"):
                    self._refresh_devices(cfg.source_type, ctrl.card)
                # 记录需要自动启动的通道
                if cfg.auto_start:
                    auto_start_ctrls.append(ctrl)
            except Exception:
                pass

        # 延迟启动，确保所有 UI 就绪
        if auto_start_ctrls:
            QTimer.singleShot(
                500,
                lambda ctrls=auto_start_ctrls: [c.start_stream() for c in ctrls],
            )

    # ==================================================================
    #  系统托盘
    # ==================================================================

    def setup_tray(self):
        """创建并显示系统托盘图标和右键菜单。"""
        icon = self._app.style().standardIcon(
            self._app.style().StandardPixmap.SP_MediaPlay
        )

        self._tray = QSystemTrayIcon(icon, self._app)
        self._tray.setToolTip("RTSP 推流客户端")

        menu = QMenu()
        show_action = QAction("显示主窗口", menu)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """双击托盘图标时显示主窗口。"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self):
        """显示并激活主窗口。"""
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    # ==================================================================
    #  退出流程
    # ==================================================================

    def _on_close(self, event: QCloseEvent):
        """主窗口关闭事件处理。

        关闭窗口时最小化到系统托盘而非退出应用。
        退出只能通过托盘右键菜单「退出」来触发。
        """
        event.ignore()
        self._window.hide()
        if self._tray:
            self._tray.showMessage(
                "RTSP 推流客户端",
                "窗口已最小化到托盘，双击图标可重新打开",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )

    def _quit(self):
        """从托盘菜单触发的退出。"""
        streaming_count = sum(1 for c in self._controllers if c.is_streaming)
        if streaming_count > 0:
            if not self._window.confirm_close(streaming_count):
                return
        self._cleanup_and_quit()

    def _cleanup_and_quit(self):
        """执行退出清理：保存配置 → 停止所有推流 → 隐藏托盘 → 退出。"""
        self.save_config()
        for ctrl in self._controllers:
            ctrl.force_stop()
        if self._tray:
            self._tray.hide()
        self._app.quit()
