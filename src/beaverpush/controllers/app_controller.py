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

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QPixmap

from .. import APP_NAME, APP_ICON_PATH
from ..models.config import (
    AppConfig, StreamConfig, load_config, save_config, load_stream_config,
)
from ..services.device_service import (
    list_cameras, list_screens, list_windows, get_motherboard_uuid,
)
from ..services.ffmpeg_service import check_rtsp_server_reachable
from ..services.connectivity_service import ConnectivityCheckWorker
from ..services.encoder_probe import detect_available_encoders
from ..views.main_window import MainWindow
from ..views import stream_card as stream_card_module
from ..views.stream_card import StreamCardView
from .stream_controller import StreamController
from ..services.log_service import logger


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
        self._username = self._config.username
        self._machine_name = self._config.machine_name
        self._auth_secret = self._config.auth_secret
        self._server_reconnect_interval = self._config.server_reconnect_interval
        self._server_reconnect_max_attempts = self._config.server_reconnect_max_attempts
        self._controllers: list[StreamController] = []
        self._tray: QSystemTrayIcon | None = None
        self._test_worker: ConnectivityCheckWorker | None = None
        # 加载配置过程中跳过自动保存，避免在恢复期间反复写盘。
        self._loading_config: bool = False

        # 获取主板 UUID 作为默认设备名
        self._default_machine_name = get_motherboard_uuid()

        # 探测当前机器实际可用的编码器，UI 中只展示这些编码器，
        # 避免选了 nvenc/qsv 但硬件不支持时启动后才报错。
        # 探测会拉起若干次 ffmpeg 子进程，可能耗时数百毫秒到数秒，
        # 所以推迟到事件循环开始后执行；早于此时新建的卡片暂时使用默认全集，
        # 探测完成后调用 :func:`stream_card.set_available_codecs` 影响后续新建卡片。
        QTimer.singleShot(0, self._detect_and_apply_codecs)

        # 同步初始状态到 View
        self._window.set_server(self._rtsp_server)
        self._window.set_server_locked(self._server_locked)
        self._window.set_username(self._username)
        self._window.set_machine_name(self._machine_name)
        self._window.set_auth_secret(self._auth_secret)
        # 设置设备名的 placeholder 为默认值（主板 UUID）
        if self._default_machine_name:
            self._window.set_machine_name_placeholder(self._default_machine_name)
        self._window.set_server_reconnect_interval(self._server_reconnect_interval)
        self._window.set_server_reconnect_max_attempts(self._server_reconnect_max_attempts)

        # 连接 View 信号 → Controller
        self._connect_signals()

        # 加载已保存的通道配置
        self._load_saved_config()

        logger.info("AppController 初始化完成，加载 {} 路通道", len(self._controllers))

    # ==================================================================
    #  信号连接
    # ==================================================================

    def _connect_signals(self):
        """将 MainWindow 的信号连接到对应的处理方法。"""
        w = self._window
        w.server_changed.connect(self._on_server_changed)
        w.server_reconnect_interval_changed.connect(self._on_server_reconnect_interval_changed)
        w.server_reconnect_max_attempts_changed.connect(self._on_server_reconnect_max_attempts_changed)
        w.test_clicked.connect(self._on_test)
        w.add_stream_clicked.connect(self.add_stream)
        w.save_config_clicked.connect(self.save_config)
        # v2 认证字段
        w.username_changed.connect(self._on_username_changed)
        w.machine_name_changed.connect(self._on_machine_name_changed)
        w.auth_secret_changed.connect(self._on_auth_secret_changed)
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

    def _on_username_changed(self, name: str):
        """用户修改用户名。"""
        self._username = name

    def _on_machine_name_changed(self, name: str):
        """用户修改设备名。"""
        self._machine_name = name

    def _on_auth_secret_changed(self, secret: str):
        """用户修改授权码。"""
        self._auth_secret = secret

    def _on_server_reconnect_interval_changed(self, value: str):
        self._server_reconnect_interval = self._parse_positive_int(value, 5)

    def _on_server_reconnect_max_attempts_changed(self, value: str):
        self._server_reconnect_max_attempts = self._parse_non_negative_int(value, 0)

    def _detect_and_apply_codecs(self):
        """异步在后台线程探测可用编码器并应用到 UI。

        放在后台线程是为了避免「启动后窗口先空白显示一段时间」——之前
        通过 ``QTimer.singleShot(0, ...)`` 调用是同步执行的，
        ``detect_available_encoders()`` 内部要拉起若干个 ffmpeg 子进程，
        在主事件循环里阻塞数百毫秒到 1 秒，正好对应用户看到的"空白窗口"。
        改为：
            * 后台 ``threading.Thread`` 跑探测；
            * 探测完成后用 ``QTimer.singleShot(0, ...)`` 把结果回传到 UI 线程
              再修改 ``stream_card`` 的下拉框（保证只在 UI 线程动 UI 状态）。
        """
        import threading

        def _worker():
            try:
                codecs = detect_available_encoders()
            except Exception:
                logger.exception("编码器探测失败，回退使用全部编码器选项")
                codecs = []
            QTimer.singleShot(0, lambda c=codecs: self._apply_detected_codecs(c))

        threading.Thread(
            target=_worker, name="encoder-probe", daemon=True,
        ).start()

    def _apply_detected_codecs(self, codecs: list[str]):
        """在 UI 线程把探测结果应用到 ``stream_card`` 模块级配置。"""
        if codecs:
            stream_card_module.set_available_codecs(codecs)
            logger.info("可用编码器: {}", codecs)
        else:
            logger.warning("未探测到任何可用编码器，保留默认编码器选项")

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
        if not self._username:
            self._window.show_test_result(False, "请输入用户名")
            return
        if not self._auth_secret:
            self._window.show_test_result(False, "请输入授权码")
            return

        effective_machine = self._machine_name or self._default_machine_name
        if not effective_machine:
            self._window.show_test_result(False, "请输入设备名（或等待自动检测主板 UUID）")
            return

        self._window.set_test_button_testing(True)
        self._window.set_status("正在测试连接...")
        worker = ConnectivityCheckWorker(
            [("正在检测 RTSP 服务器...", lambda: check_rtsp_server_reachable(
                self._rtsp_server,
                username=self._username,
                auth_secret=self._auth_secret,
                machine_name=effective_machine,
            ), "")],
            self,
        )
        self._test_worker = worker
        worker.stage_changed.connect(lambda stage: self._window.set_status(stage))
        worker.check_completed.connect(self._on_test_completed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_test_completed(self, ok: bool, message: str):
        if self.sender() is not self._test_worker:
            return
        self._test_worker = None
        self._window.show_test_result(ok, message)
        self._window.set_test_button_testing(False)
        self._window.set_status("连接测试完成")
        # 测试连接通过后自动保存配置
        if ok:
            self.save_config()

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
            username_getter=lambda: self._username,
            machine_name_getter=lambda: self._machine_name or self._default_machine_name,
            auth_secret_getter=lambda: self._auth_secret,
            server_reconnect_interval_getter=lambda: self._server_reconnect_interval,
            server_reconnect_max_attempts_getter=lambda: self._server_reconnect_max_attempts,
            status_reporter=self._window.set_status,
            duplicate_name_checker=self._is_duplicate_stream_name,
            parent=self,
        )
        self._controllers.append(ctrl)

        # 设置流名称 placeholder 为下一个可用默认名称
        self._update_stream_name_placeholders()

        # 连接卡片的"移除"按钮、"刷新"按钮和"源类型切换"
        card.remove_clicked.connect(lambda: self._remove_stream(ctrl))
        card.refresh_clicked.connect(
            lambda: self._refresh_devices(card.get_source_type(), card)
        )
        # 切换视频源类型时自动刷新设备列表
        card.source_type_changed.connect(
            lambda key: self._refresh_devices(key, card)
        )
        # 上下移动卡片
        card.move_up_clicked.connect(lambda: self._move_stream(ctrl, -1))
        card.move_down_clicked.connect(lambda: self._move_stream(ctrl, +1))
        # 点击"开始推流"立即自动保存配置（不论后续推流是否成功）
        card.start_clicked.connect(self._autosave)

        logger.info("添加推流通道 #{}", channel_index + 1)
        self._window.set_status(f"已添加推流通道，共 {len(self._controllers)} 路")
        self._refresh_card_positions()
        self._autosave()
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
        logger.info("移除推流通道，剩余 {} 路", len(self._controllers))
        self._window.set_status(f"已移除推流通道，剩余 {len(self._controllers)} 路")
        # 更新未设置流名称的通道的 placeholder
        self._update_stream_name_placeholders()
        self._refresh_card_positions()
        self._autosave()

    def _update_stream_name_placeholders(self):
        """为所有未设置流名称的通道更新 placeholder（stream1, stream2, ...）。

        已有自定义名称或已被持久化名称的通道不受影响。
        """
        idx = 1
        for ctrl in self._controllers:
            default_name = f"stream{idx}"
            ctrl.card.set_stream_name_placeholder(default_name)
            ctrl.set_default_stream_name(default_name)
            idx += 1

    def _refresh_card_positions(self):
        """刷新所有卡片的位置序号徽标与上下移动按钮的可用状态。"""
        n = len(self._controllers)
        for i, ctrl in enumerate(self._controllers):
            ctrl.card.set_position_index(i)
            ctrl.card.set_move_buttons_enabled(can_up=i > 0, can_down=i < n - 1)

    def _move_stream(self, ctrl: StreamController, delta: int):
        """在卡片列表中将指定通道上移或下移一位。

        Args:
            ctrl:  目标通道控制器。
            delta: ``-1`` 表示上移，``+1`` 表示下移。

        推流中的通道不允许移动，行为与"移除"按钮一致。
        """
        if delta not in (-1, 1):
            return
        if ctrl.is_streaming:
            self._window.set_status("请先停止推流再移动")
            return
        if ctrl not in self._controllers:
            return
        old_index = self._controllers.index(ctrl)
        new_index = old_index + delta
        if new_index < 0 or new_index >= len(self._controllers):
            return
        # 同步移动 UI 卡片
        if not self._window.move_card(ctrl.card, delta):
            return
        # 同步移动 controller 列表
        self._controllers.pop(old_index)
        self._controllers.insert(new_index, ctrl)
        logger.info(
            "移动推流通道：通道 #{} 从位置 {} 移动到位置 {}",
            ctrl.channel_index + 1, old_index + 1, new_index + 1,
        )
        self._window.set_status(
            f"已将卡片从位置 {old_index + 1} 移动到位置 {new_index + 1}"
        )
        self._refresh_card_positions()
        self._autosave()

    def _autosave(self):
        """自动保存当前配置（加载阶段不保存，且不覆盖状态栏文案）。"""
        if self._loading_config:
            return
        try:
            self.save_config(update_status=False)
        except Exception:
            logger.exception("自动保存配置失败")

    def _get_all_effective_stream_names(self) -> list[str]:
        """获取所有通道的有效流名称（含默认名称）。"""
        names = []
        for ctrl in self._controllers:
            names.append(ctrl.get_effective_stream_name())
        return names

    def _is_duplicate_stream_name(self, name: str, channel_index: int) -> bool:
        """检查指定流名称是否与其他通道重复。

        Args:
            name: 待检查的流名称。
            channel_index: 当前通道索引（排除自身）。

        Returns:
            ``True`` 表示存在重复。
        """
        for ctrl in self._controllers:
            if ctrl.channel_index == channel_index:
                continue
            if ctrl.get_effective_stream_name() == name:
                return True
        return False

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

    def save_config(self, *, update_status: bool = True):
        """保存当前所有通道配置到文件。"""
        self._server_locked = self._window.get_server_locked()
        cfg = AppConfig(
            rtsp_server=self._rtsp_server,
            server_locked=self._server_locked,
            username=self._username,
            machine_name=self._machine_name,
            auth_secret=self._auth_secret,
            server_reconnect_interval=self._server_reconnect_interval,
            server_reconnect_max_attempts=self._server_reconnect_max_attempts,
            streams=[],
        )
        for ctrl in self._controllers:
            cfg.add_stream(ctrl.to_config())
        save_config(cfg)
        logger.info("配置已保存，共 {} 路通道", len(cfg.streams))
        if update_status:
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

        self._loading_config = True
        try:
            for stream_data in self._config.streams:
                try:
                    cfg = load_stream_config(stream_data)
                    ctrl = self.add_stream()
                    ctrl.from_config(cfg)
                    # 如果是设备类型，自动刷新设备列表
                    if cfg.source_type in ("camera", "screen", "window"):
                        self._refresh_devices(cfg.source_type, ctrl.card)
                    # 记录需要自动启动的通道
                    if cfg.auto_start:
                        auto_start_ctrls.append(ctrl)
                except Exception:
                    logger.exception("加载通道配置失败: {}", stream_data)
        finally:
            self._loading_config = False

        # 加载完成后刷新一次序号/移动按钮状态
        self._refresh_card_positions()

        # 延迟启动，确保所有 UI 就绪；并按 ~250ms 间隔逐个启动而不是
        # 一口气全部并发，避免：
        #   1) 多路编码器同时初始化抢 GPU/CPU，
        #   2) 主线程在循环里同步执行 ``probe_hikcamera_size`` / ``Popen``
        #      等阻塞调用造成 UI 长时间卡死。
        if auto_start_ctrls:
            stagger_ms = 250
            for i, c in enumerate(auto_start_ctrls):
                QTimer.singleShot(
                    500 + i * stagger_ms,
                    lambda ctrl=c: ctrl.start_stream(),
                )

    # ==================================================================
    #  系统托盘
    # ==================================================================

    def setup_tray(self):
        """创建并显示系统托盘图标和右键菜单。"""
        # 从 ico 文件加载后，补充系统托盘需要的小尺寸 pixmap
        icon = QIcon(APP_ICON_PATH)
        if not icon.isNull():
            for size in (16, 24, 32, 48):
                pm = QPixmap(APP_ICON_PATH).scaled(
                    size, size,
                    aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
                    mode=Qt.TransformationMode.SmoothTransformation,
                )
                icon.addPixmap(pm)

        self._tray = QSystemTrayIcon(icon, self._app)
        self._tray.setToolTip(APP_NAME)

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
                APP_NAME,
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

    @staticmethod
    def _parse_positive_int(value: str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _parse_non_negative_int(value: str, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default
