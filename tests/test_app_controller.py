"""AppController 行为测试：序号刷新、上下排序、自动保存。"""

from __future__ import annotations

import time

import pytest
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
    from beaverpush.views import stream_card as sc
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    original = sc.CODEC_OPTIONS[:]
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
        sc.CODEC_OPTIONS = original


def test_async_codec_probe_refreshes_cards_created_before_probe(monkeypatch):
    """关键回归：后台线程探测完成后，必须真正把结果送回 UI 线程。

    之前实现是在 Python 工作线程里直接调用 ``QTimer.singleShot(0, ...)``，
    这在 PySide 下不会把回调投递到主线程事件循环，导致启动时先创建的卡片
    永远保留默认 ``CODEC_OPTIONS``（含 QSV），Windows 用户就会继续看到
    ``h264_qsv`` / ``hevc_qsv``。
    """
    from beaverpush.views import stream_card as sc

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
    original = sc.CODEC_OPTIONS[:]
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
        sc.CODEC_OPTIONS = original
        window.deleteLater()
        app.processEvents()


def test_start_all_queues_staggered_starts(controller, monkeypatch):
    ctrl, window, saves = controller
    a = ctrl.add_stream()
    b = ctrl.add_stream()
    c = ctrl.add_stream()
    calls: list[str] = []

    monkeypatch.setattr(app_ctrl_module, "BULK_START_INTERVAL_MS", 10)

    def make_start(name, stream_ctrl):
        def _start():
            calls.append(name)
            stream_ctrl._state = StreamState.STARTING
        return _start

    monkeypatch.setattr(a, "start_stream", make_start("a", a))
    monkeypatch.setattr(b, "start_stream", make_start("b", b))
    monkeypatch.setattr(c, "start_stream", make_start("c", c))

    ctrl._on_start_all()
    assert calls == []

    app = QApplication.instance() or QApplication([])
    deadline = time.time() + 1.0
    while time.time() < deadline and calls != ["a", "b", "c"]:
        app.processEvents()
        time.sleep(0.01)

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
        def _start():
            calls.append(name)
            stream_ctrl._state = StreamState.STARTING
        return _start

    monkeypatch.setattr(a, "start_stream", make_start("a", a))
    monkeypatch.setattr(c, "start_stream", make_start("c", c))
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
