"""main 入口在不同托盘可用性下的行为测试。"""

from __future__ import annotations

import pytest

from beaverpush import main as main_module


def test_minimized_start_shows_window_when_system_tray_unavailable(monkeypatch):
    """带 --minimized 启动但无托盘时，应回退为显示主窗口。"""

    class _FakeApp:
        def __init__(self, argv):
            self.argv = argv
            self.quit_on_last_window_closed = None
            self.window_shown = False

        def setApplicationName(self, name):
            self.application_name = name

        def setApplicationVersion(self, version):
            self.application_version = version

        def setQuitOnLastWindowClosed(self, value):
            self.quit_on_last_window_closed = value

        def setFont(self, font):
            self.font = font

        def setStyleSheet(self, stylesheet):
            self.stylesheet = stylesheet

        def setWindowIcon(self, icon):
            self.icon = icon

        def exec(self):
            return 0

    class _FakeFont:
        def __init__(self, *args, **kwargs):
            self.point_size = None

        def setPointSize(self, value):
            self.point_size = value

    class _FakeSignal:
        def connect(self, callback):
            self.callback = callback

    class _FakeGuard:
        def __init__(self, *args, **kwargs):
            self.activated = _FakeSignal()

        def try_start(self):
            return True

    class _FakeWindow:
        def __init__(self):
            self.shown = False

        def show(self):
            self.shown = True

    window_holder: dict[str, _FakeWindow] = {}

    class _FakeController:
        def __init__(self, window, app):
            window_holder["window"] = window

        def setup_tray(self):
            return False

        def _show_window(self):
            window_holder["window"].show()

    warnings: list[str] = []

    monkeypatch.setattr(main_module, "QApplication", _FakeApp)
    monkeypatch.setattr(main_module, "QFont", _FakeFont)
    monkeypatch.setattr(main_module, "QIcon", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "SingleInstanceGuard", _FakeGuard)
    monkeypatch.setattr(main_module, "MainWindow", _FakeWindow)
    monkeypatch.setattr(main_module, "AppController", _FakeController)
    monkeypatch.setattr(main_module, "setup_logging", lambda: None)
    monkeypatch.setattr(main_module.logger, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module.logger, "warning", lambda message, *args: warnings.append(message))
    monkeypatch.setattr(main_module.autostart_service, "is_launched_minimized", lambda: True)

    with pytest.raises(SystemExit) as exc_info:
        main_module.main()

    assert exc_info.value.code == 0
    assert window_holder["window"].shown is True
    assert warnings == ["检测到 --minimized，但当前环境不支持系统托盘，改为显示主窗口"]
