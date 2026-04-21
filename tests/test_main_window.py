from PySide6.QtWidgets import QApplication, QFrame, QLineEdit, QPushButton

from beaverpush.views import main_window as main_window_module
from beaverpush.views.main_window import MainWindow


def test_auth_fields_grouped_into_cards():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        groups = window.findChildren(QFrame, "authFieldGroup")
        assert len(groups) == 3
        assert window._username_input.minimumWidth() >= 180
        assert window._machine_name_input.minimumWidth() >= 180
        assert window._auth_secret_input.minimumWidth() >= 220
    finally:
        window.deleteLater()
        app.processEvents()


def test_auth_secret_stays_masked():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.set_auth_secret("AKsecret123")
        assert window._auth_secret_input.echoMode() == QLineEdit.EchoMode.Password
        assert window._auth_secret_input.displayText() != "AKsecret123"
        assert not any(
            button.toolTip() == "显示/隐藏授权码"
            for button in window.findChildren(QPushButton)
        )
    finally:
        window.deleteLater()
        app.processEvents()


def test_move_card_swaps_order_in_list_and_layout():
    app = QApplication.instance() or QApplication([])
    from beaverpush.views.stream_card import StreamCardView
    window = MainWindow()
    try:
        c0 = StreamCardView(0, window)
        c1 = StreamCardView(1, window)
        c2 = StreamCardView(2, window)
        window.add_card(c0)
        window.add_card(c1)
        window.add_card(c2)

        assert window.get_cards() == [c0, c1, c2]
        # 下移第一张
        assert window.move_card(c0, +1) is True
        assert window.get_cards() == [c1, c0, c2]
        # 上移最后一张
        assert window.move_card(c2, -1) is True
        assert window.get_cards() == [c1, c2, c0]

        # layout 顺序应与 _cards 一致（layout 中索引偏移 1，因 empty_label 在 0 位）
        layout = window._cards_layout
        for i, card in enumerate(window.get_cards()):
            assert layout.itemAt(i + 1).widget() is card
    finally:
        window.deleteLater()
        app.processEvents()


def test_move_card_at_boundaries_returns_false():
    app = QApplication.instance() or QApplication([])
    from beaverpush.views.stream_card import StreamCardView
    window = MainWindow()
    try:
        c0 = StreamCardView(0, window)
        c1 = StreamCardView(1, window)
        window.add_card(c0)
        window.add_card(c1)

        assert window.move_card(c0, -1) is False  # 第一张不能上移
        assert window.move_card(c1, +1) is False  # 最后一张不能下移
        assert window.get_cards() == [c0, c1]
    finally:
        window.deleteLater()
        app.processEvents()


def test_help_content_includes_runtime_version(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        monkeypatch.setattr(main_window_module, "get_app_version", lambda: "2.3.4")
        monkeypatch.setattr(window, "_load_help_content", lambda: "帮助正文")
        content = window._get_help_content()
        assert content.startswith("当前版本: 2.3.4")
        assert content.endswith("帮助正文")
    finally:
        window.deleteLater()
        app.processEvents()


def test_load_help_content_reads_help_file(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        monkeypatch.setattr(main_window_module, "_ASSETS_DIR", tmp_path)
        (tmp_path / "help.txt").write_text("文件中的帮助正文", encoding="utf-8")
        assert window._load_help_content() == "文件中的帮助正文"
    finally:
        window.deleteLater()
        app.processEvents()


def test_help_content_shows_version_when_file_missing(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        monkeypatch.setattr(main_window_module, "get_app_version", lambda: "9.9.9")
        monkeypatch.setattr(main_window_module, "_ASSETS_DIR", tmp_path)
        content = window._get_help_content()
        assert "当前版本: 9.9.9" in content
        assert "帮助文件未找到。" in content
    finally:
        window.deleteLater()
        app.processEvents()
