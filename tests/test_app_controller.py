"""AppController 行为测试：序号刷新、上下排序、自动保存。"""

from __future__ import annotations

import time

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from beaverpush.controllers import app_controller as app_ctrl_module
from beaverpush.controllers.app_controller import AppController
from beaverpush.models.config import AppConfig
from beaverpush.models.stream_model import StreamState
from beaverpush.views.main_window import MainWindow


@pytest.fixture
def empty_config(monkeypatch):
    """避免读取磁盘上的真实配置；并拦截 save_config 写盘调用并计数。"""
    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: AppConfig())
    saves: list[AppConfig] = []
    monkeypatch.setattr(
        app_ctrl_module, "save_config", lambda cfg: saves.append(cfg)
    )
    return saves


@pytest.fixture
def controller(empty_config, monkeypatch):
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        yield ctrl, window, empty_config
    finally:
        window.deleteLater()
        app.processEvents()


def test_add_stream_refreshes_positions_and_autosaves(controller):
    ctrl, window, saves = controller
    saves.clear()
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    c = ctrl.add_stream()
    # 每次 add 触发一次自动保存
    assert len(saves) == 3
    # 序号徽标按位置刷新（与 channel_index 解耦）
    assert a.card._position_badge.text() == "#1"
    assert b.card._position_badge.text() == "#2"
    assert c.card._position_badge.text() == "#3"
    # 边界按钮：首张禁用上移，末张禁用下移
    assert not a.card._move_up_btn.isEnabled()
    assert a.card._move_down_btn.isEnabled()
    assert b.card._move_up_btn.isEnabled()
    assert b.card._move_down_btn.isEnabled()
    assert c.card._move_up_btn.isEnabled()
    assert not c.card._move_down_btn.isEnabled()


def test_move_stream_swaps_controllers_and_autosaves(controller):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    c = ctrl.add_stream()
    saves.clear()

    # 通过点击卡片的下移按钮触发：a 下移 → 顺序变为 [b, a, c]
    a.card._move_down_btn.click()
    assert ctrl._controllers == [b, a, c]
    assert window.get_cards() == [b.card, a.card, c.card]
    # 序号同步刷新
    assert b.card._position_badge.text() == "#1"
    assert a.card._position_badge.text() == "#2"
    assert c.card._position_badge.text() == "#3"
    # 触发了一次自动保存
    assert len(saves) == 1

    # 再点击 c 的上移：顺序变为 [b, c, a]
    c.card._move_up_btn.click()
    assert ctrl._controllers == [b, c, a]
    assert len(saves) == 2


def test_remove_stream_autosaves_and_refreshes_positions(controller):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    c = ctrl.add_stream()
    saves.clear()

    ctrl._remove_stream(b)
    assert ctrl._controllers == [a, c]
    assert a.card._position_badge.text() == "#1"
    assert c.card._position_badge.text() == "#2"
    # 移除后的边界按钮
    assert not a.card._move_up_btn.isEnabled()
    assert not c.card._move_down_btn.isEnabled()
    assert len(saves) == 1


def test_clicking_start_button_triggers_autosave(controller, monkeypatch):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    saves.clear()
    # 阻断真正的推流逻辑，仅验证 start_clicked 触发了自动保存
    monkeypatch.setattr(a, "start_stream", lambda: None)
    a.card._start_btn.click()
    assert len(saves) == 1


def test_loading_config_skips_autosave(monkeypatch):
    """加载阶段不应触发自动保存（即使添加了通道）。"""
    cfg = AppConfig(streams=[{"name": "stream1", "source_type": "video"}])
    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: cfg)
    saves: list[AppConfig] = []
    monkeypatch.setattr(
        app_ctrl_module, "save_config", lambda c: saves.append(c)
    )
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        AppController(window, app)
        # 加载期间 _loading_config=True，所有 _autosave 调用应跳过
        assert saves == []
    finally:
        window.deleteLater()
        app.processEvents()


def test_move_blocked_when_streaming(controller):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    saves.clear()
    # 模拟 a 正在推流（直接设置状态，避免触发真正的 ffmpeg 启动）
    from beaverpush.models.stream_model import StreamState
    a._state = StreamState.STREAMING
    # 直接调用 _move_stream（按钮虽已禁用，但要保证后端拦截）
    ctrl._move_stream(a, +1)
    assert ctrl._controllers == [a, b]
    assert saves == []


def test_apply_detected_codecs_refreshes_existing_cards(controller):
    from beaverpush.services.codec_registry import CodecRegistry
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    try:
        a.card.set_codec("h264_qsv")
        b.card.set_codec("hevc_qsv")

        ctrl._apply_detected_codecs(["libx264", "libx265", "h264_nvenc"])

        a_items = [a.card._codec_combo.itemText(i) for i in range(a.card._codec_combo.count())]
        b_items = [b.card._codec_combo.itemText(i) for i in range(b.card._codec_combo.count())]

        assert "h264_qsv" not in a_items
        assert "hevc_qsv" not in b_items
        assert "h264_nvenc" in a_items
        assert a.card.get_codec() == "自动"
        assert b.card.get_codec() == "自动"
    finally:
        CodecRegistry.reset()


def test_async_codec_probe_refreshes_cards_created_before_probe(monkeypatch):
    """关键回归：后台线程探测完成后，必须真正把结果送回 UI 线程。

    之前实现是在 Python 工作线程里直接调用 ``QTimer.singleShot(0, ...)``，
    这在 PySide 下不会把回调投递到主线程事件循环，导致启动时先创建的卡片
    永远保留默认编码器列表（含 QSV），Windows 用户就会继续看到
    ``h264_qsv`` / ``hevc_qsv``。
    """
    from beaverpush.services.codec_registry import CodecRegistry

    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: AppConfig())
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda cfg: None)

    def fake_detect_available_encoders():
        # 给主线程一个机会先创建 stream card，再由后台线程回刷编码器列表。
        time.sleep(0.05)
        return ["libx264", "libx265", "h264_nvenc"]

    monkeypatch.setattr(
        app_ctrl_module, "detect_available_encoders", fake_detect_available_encoders,
    )

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        stream = ctrl.add_stream()
        stream.card.set_codec("h264_qsv")

        deadline = time.time() + 2.0
        while time.time() < deadline:
            app.processEvents()
            items = [
                stream.card._codec_combo.itemText(i)
                for i in range(stream.card._codec_combo.count())
            ]
            if "h264_qsv" not in items and "h264_nvenc" in items:
                break
            time.sleep(0.01)

        items = [
            stream.card._codec_combo.itemText(i)
            for i in range(stream.card._codec_combo.count())
        ]
        assert "h264_qsv" not in items
        assert "hevc_qsv" not in items
        assert "h264_nvenc" in items
        assert stream.card.get_codec() == "自动"
    finally:
        CodecRegistry.reset()
        window.deleteLater()
        app.processEvents()


def test_start_all_waits_for_streaming_before_starting_next(controller, monkeypatch):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    c = ctrl.add_stream()
    calls: list[str] = []

    def make_start(name, stream_ctrl):
        def _start(reconnect=False):  # noqa: ARG001
            calls.append(name)
            stream_ctrl._set_state(StreamState.STARTING)
        return _start

    monkeypatch.setattr(a, "run_scheduled_start", make_start("a", a))
    monkeypatch.setattr(b, "run_scheduled_start", make_start("b", b))
    monkeypatch.setattr(c, "run_scheduled_start", make_start("c", c))

    ctrl._on_start_all()
    assert calls == []

    app = QApplication.instance() or QApplication([])
    deadline = time.time() + 0.5
    while time.time() < deadline and calls != ["a"]:
        app.processEvents()
        time.sleep(0.01)
    assert calls == ["a"]

    # A fixed delay must not release the next start while A is still STARTING.
    deadline = time.time() + 0.35
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert calls == ["a"]

    a._set_state(StreamState.STREAMING)
    app.processEvents()
    assert calls == ["a", "b"]

    b._set_state(StreamState.STREAMING)
    app.processEvents()
    assert calls == ["a", "b", "c"]



def test_start_all_skips_streaming_and_stop_all_cancels_remaining(controller, monkeypatch):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    c = ctrl.add_stream()
    b._state = StreamState.STREAMING
    calls: list[str] = []

    monkeypatch.setattr(app_ctrl_module, "BULK_START_INTERVAL_MS", 120)

    def make_start(name, stream_ctrl):
        def _start(reconnect=False):  # noqa: ARG001
            calls.append(name)
            stream_ctrl._state = StreamState.STARTING
        return _start

    monkeypatch.setattr(a, "run_scheduled_start", make_start("a", a))
    monkeypatch.setattr(c, "run_scheduled_start", make_start("c", c))
    monkeypatch.setattr(a, "stop_stream", lambda: calls.append("stop-a"))

    ctrl._on_start_all()

    app = QApplication.instance() or QApplication([])
    deadline = time.time() + 0.5
    while time.time() < deadline and calls != ["a"]:
        app.processEvents()
        time.sleep(0.01)

    assert calls == ["a"]

    ctrl._on_stop_all()

    deadline = time.time() + 0.4
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert calls == ["a", "stop-a"]


def test_reconnecting_stream_releases_slot_for_next_channel(controller, monkeypatch):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    calls: list[str] = []

    def make_start(name, stream_ctrl):
        def _start(reconnect=False):  # noqa: ARG001
            calls.append(name)
            stream_ctrl._set_state(StreamState.STARTING)
        return _start

    monkeypatch.setattr(a, "run_scheduled_start", make_start("a", a))
    monkeypatch.setattr(b, "run_scheduled_start", make_start("b", b))

    ctrl._on_start_all()
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    assert calls == ["a"]

    a._set_state(StreamState.RECONNECTING)
    app.processEvents()

    assert calls == ["a", "b"]


def test_twenty_three_streams_never_have_multiple_startups_in_flight(controller, monkeypatch):
    ctrl, window, saves = controller
    streams = [ctrl.add_stream() for _ in range(23)]
    calls: list[int] = []

    for index, stream in enumerate(streams):
        def start(reconnect=False, *, current=stream, number=index):  # noqa: ARG001
            calls.append(number)
            current._set_state(StreamState.STARTING)
        monkeypatch.setattr(stream, "run_scheduled_start", start)

    ctrl._on_start_all()
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    assert calls == [0]

    for index, stream in enumerate(streams):
        assert calls == list(range(index + 1))
        stream._set_state(StreamState.STREAMING)
        app.processEvents()

    assert calls == list(range(23))


def test_reconnect_request_waits_behind_current_startup(controller, monkeypatch):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    calls: list[tuple[str, bool]] = []

    def start_a(reconnect=False):
        calls.append(("a", reconnect))
        a._set_state(StreamState.STARTING)

    def start_b(reconnect=False):
        calls.append(("b", reconnect))
        b._set_state(StreamState.STARTING)

    monkeypatch.setattr(a, "run_scheduled_start", start_a)
    monkeypatch.setattr(b, "run_scheduled_start", start_b)

    ctrl._on_start_all()
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    a._set_state(StreamState.RECONNECTING)
    app.processEvents()
    assert calls == [("a", False), ("b", False)]

    a._attempt_reconnect()
    app.processEvents()
    assert calls == [("a", False), ("b", False)]

    b._set_state(StreamState.STREAMING)
    app.processEvents()
    assert calls == [("a", False), ("b", False), ("a", True)]


def test_stop_cancels_a_manual_start_that_has_not_spawned_yet(controller, monkeypatch):
    ctrl, window, saves = controller
    stream = ctrl.add_stream()
    starts: list[bool] = []
    monkeypatch.setattr(
        stream,
        "run_scheduled_start",
        lambda reconnect=False: starts.append(reconnect),
    )

    for _ in range(100):
        stream.start_stream()
    stream.stop_stream()

    app = QApplication.instance() or QApplication([])
    app.processEvents()
    assert starts == []


def test_quit_waits_for_process_supervisor_after_stopping_streams(controller, monkeypatch):
    ctrl, window, saves = controller
    stream = ctrl.add_stream()
    calls: list[str] = []
    monkeypatch.setattr(ctrl, "save_config", lambda: calls.append("save"))
    monkeypatch.setattr(stream, "force_stop", lambda: calls.append("stop"))
    monkeypatch.setattr(
        app_ctrl_module.process_supervisor,
        "shutdown",
        lambda timeout_seconds=5.0: calls.append("reap"),
    )
    ctrl._app = mock_app = type(
        "FakeApp", (), {"quit": lambda self: calls.append("quit")}
    )()

    ctrl._cleanup_and_quit()

    assert calls == ["save", "stop", "reap", "quit"]


def test_quit_is_deferred_until_supervisor_confirms_no_processes(controller, monkeypatch):
    ctrl, window, saves = controller
    callbacks = []
    quits = []
    remaining = iter([1, 0])
    monkeypatch.setattr(ctrl, "save_config", lambda: None)
    monkeypatch.setattr(
        app_ctrl_module.process_supervisor,
        "shutdown",
        lambda timeout_seconds=5.0: None,
    )
    monkeypatch.setattr(
        app_ctrl_module.process_supervisor,
        "active_count",
        lambda: next(remaining),
    )
    monkeypatch.setattr(
        app_ctrl_module.QTimer,
        "singleShot",
        lambda _delay, callback: callbacks.append(callback),
    )
    ctrl._app = type("FakeApp", (), {"quit": lambda self: quits.append(True)})()

    ctrl._cleanup_and_quit()

    assert quits == []
    assert len(callbacks) == 1
    callbacks.pop()()
    assert quits == [True]


def test_loading_config_uses_bulk_start_queue(monkeypatch):
    """自动恢复推流应与手动“全部开始”共用同一批量调度入口。"""
    cfg = AppConfig(
        streams=[{"name": "stream1", "source_type": "video", "auto_start": True}]
    )
    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: cfg)
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda c: None)
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)

    queued: dict[str, object] = {}

    def fake_queue(self, controllers, *, initial_delay_ms):
        queued["count"] = len(controllers)
        queued["delay"] = initial_delay_ms
        return len(controllers)

    monkeypatch.setattr(AppController, "_queue_bulk_start", fake_queue)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        AppController(window, app)
        assert queued == {"count": 1, "delay": app_ctrl_module.AUTO_BULK_START_INITIAL_DELAY_MS}
    finally:
        window.deleteLater()
        app.processEvents()


# ==================================================================
#  开机自启动（launch_at_startup）
# ==================================================================

def test_launch_at_startup_initial_sync_with_config(monkeypatch):
    """启动期 AppController 应根据配置调用 autostart_service.sync 对账。"""
    from beaverpush.services import autostart_service

    monkeypatch.setattr(
        app_ctrl_module, "load_config", lambda: AppConfig(launch_at_startup=True)
    )
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda cfg: None)
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)

    sync_calls: list[bool] = []
    monkeypatch.setattr(autostart_service, "is_supported", lambda: True)
    monkeypatch.setattr(autostart_service, "sync", lambda enabled: sync_calls.append(enabled) or True)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        # 应当调用一次 sync(True) 把注册表与配置对齐
        assert sync_calls == [True]
        # UI 状态同步
        assert window.get_launch_at_startup() is True
        assert ctrl._launch_at_startup is True
    finally:
        window.deleteLater()
        app.processEvents()


def test_launch_at_startup_initial_sync_failure_reverts_ui_and_status(monkeypatch):
    """启动期对账失败时应回滚 UI/内存状态，并提示用户查看日志。"""
    from beaverpush.services import autostart_service

    monkeypatch.setattr(
        app_ctrl_module, "load_config", lambda: AppConfig(launch_at_startup=True)
    )
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda cfg: None)
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)
    monkeypatch.setattr(autostart_service, "is_supported", lambda: True)
    monkeypatch.setattr(autostart_service, "sync", lambda enabled: False)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        assert ctrl._launch_at_startup is False
        assert ctrl._config.launch_at_startup is False
        assert window.get_launch_at_startup() is False
        assert window._status_label.text() == "开机自启动对账失败，请查看日志"
    finally:
        window.deleteLater()
        app.processEvents()


def test_toggle_launch_at_startup_persists_and_calls_service(monkeypatch):
    """用户勾选/取消勾选时应调用 autostart_service.sync 并自动持久化。"""
    from beaverpush.services import autostart_service

    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: AppConfig())
    saves: list[AppConfig] = []
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda cfg: saves.append(cfg))
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)

    sync_calls: list[bool] = []

    def fake_sync(enabled: bool) -> bool:
        sync_calls.append(enabled)
        return True

    monkeypatch.setattr(autostart_service, "is_supported", lambda: True)
    monkeypatch.setattr(autostart_service, "sync", fake_sync)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        # 启动期会触发一次 sync(False)
        sync_calls.clear()
        saves.clear()

        window.launch_at_startup_changed.emit(True)
        assert sync_calls == [True]
        assert ctrl._launch_at_startup is True
        # 自动保存到 AppConfig 中
        assert len(saves) == 1
        assert saves[-1].launch_at_startup is True

        window.launch_at_startup_changed.emit(False)
        assert sync_calls == [True, False]
        assert ctrl._launch_at_startup is False
        assert saves[-1].launch_at_startup is False
    finally:
        window.deleteLater()
        app.processEvents()


def test_toggle_launch_at_startup_failure_reverts_ui(monkeypatch):
    """sync 返回 False 时应回滚 checkbox，不改变内部状态，也不持久化。"""
    from beaverpush.services import autostart_service

    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: AppConfig())
    saves: list[AppConfig] = []
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda cfg: saves.append(cfg))
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)

    monkeypatch.setattr(autostart_service, "is_supported", lambda: True)
    monkeypatch.setattr(autostart_service, "sync", lambda enabled: False)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        saves.clear()

        window.launch_at_startup_changed.emit(True)
        # 内部状态仍为 False，UI 复原
        assert ctrl._launch_at_startup is False
        assert window.get_launch_at_startup() is False
        # 失败时不应触发自动保存
        assert saves == []
    finally:
        window.deleteLater()
        app.processEvents()


def test_setup_tray_skips_when_system_tray_unavailable(monkeypatch):
    """无系统托盘时，setup_tray 应跳过初始化并返回 False。"""
    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: AppConfig())
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda cfg: None)
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)
    monkeypatch.setattr(
        app_ctrl_module.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False),
    )

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        assert ctrl.setup_tray() is False
        assert ctrl._tray is None
    finally:
        window.deleteLater()
        app.processEvents()


def test_close_event_quits_when_system_tray_missing(monkeypatch):
    """没有系统托盘时，关闭窗口应直接退出而不是隐藏到托盘。"""
    monkeypatch.setattr(app_ctrl_module, "load_config", lambda: AppConfig())
    monkeypatch.setattr(app_ctrl_module, "save_config", lambda cfg: None)
    monkeypatch.setattr(AppController, "_detect_and_apply_codecs", lambda self: None)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        ctrl = AppController(window, app)
        cleanup_calls: list[str] = []
        monkeypatch.setattr(
            ctrl, "_cleanup_and_quit", lambda: cleanup_calls.append("quit"),
        )

        event = QCloseEvent()
        ctrl._tray = None
        ctrl._on_close(event)

        assert event.isAccepted()
        assert cleanup_calls == ["quit"]
    finally:
        window.deleteLater()
        app.processEvents()
