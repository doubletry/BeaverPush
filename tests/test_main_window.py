from PySide6.QtWidgets import QApplication, QFrame, QLineEdit, QPushButton

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
