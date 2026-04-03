from PySide6.QtWidgets import QApplication

from beaverpush.views.stream_card import StreamCardView


def test_bitrate_placeholder_uses_fixed_m_unit_text():
    app = QApplication.instance() or QApplication([])
    card = StreamCardView(0)
    try:
        assert app is not None
        assert card._bitrate_input.placeholderText() == ""
        assert card._bitrate_input.text() == ""
    finally:
        card.deleteLater()
        app.processEvents()
