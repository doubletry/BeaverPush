from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QValidator

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


def test_bitrate_input_width_matches_fps_input():
    """码率输入框宽度应与帧率输入框相同"""
    app = QApplication.instance() or QApplication([])
    card = StreamCardView(0)
    try:
        assert card._bitrate_input.maximumWidth() == card._fps_input.maximumWidth()
    finally:
        card.deleteLater()
        app.processEvents()


class TestStreamNameValidation:
    """验证流名称输入框的字符校验"""

    def test_stream_name_accepts_ascii_alphanumeric(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            validator = card._stream_name_input.validator()
            assert validator is not None
            result, _, _ = validator.validate("stream1", 0)
            assert result == QValidator.State.Acceptable
        finally:
            card.deleteLater()
            app.processEvents()

    def test_stream_name_accepts_dot_underscore_hyphen(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            validator = card._stream_name_input.validator()
            result, _, _ = validator.validate("my.stream_name-1", 0)
            assert result == QValidator.State.Acceptable
        finally:
            card.deleteLater()
            app.processEvents()

    def test_stream_name_rejects_chinese(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            validator = card._stream_name_input.validator()
            result, _, _ = validator.validate("测试流", 0)
            assert result == QValidator.State.Invalid
        finally:
            card.deleteLater()
            app.processEvents()

    def test_stream_name_rejects_slash(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            validator = card._stream_name_input.validator()
            result1, _, _ = validator.validate("stream/1", 0)
            result2, _, _ = validator.validate("stream\\1", 0)
            assert result1 == QValidator.State.Invalid
            assert result2 == QValidator.State.Invalid
        finally:
            card.deleteLater()
            app.processEvents()

    def test_stream_name_rejects_quotes(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            validator = card._stream_name_input.validator()
            result1, _, _ = validator.validate('stream"1', 0)
            result2, _, _ = validator.validate("stream'1", 0)
            assert result1 == QValidator.State.Invalid
            assert result2 == QValidator.State.Invalid
        finally:
            card.deleteLater()
            app.processEvents()

    def test_stream_name_rejects_spaces(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            validator = card._stream_name_input.validator()
            result, _, _ = validator.validate("stream 1", 0)
            assert result == QValidator.State.Invalid
        finally:
            card.deleteLater()
            app.processEvents()


class TestSourcePathsCache:
    """验证视频源类型切换时的路径缓存"""

    def test_initial_source_type_is_video(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            assert card._current_source_type == "video"
        finally:
            card.deleteLater()
            app.processEvents()

    def test_reconnect_hidden_for_video_source(self):
        """重连配置对非 RTSP 源应隐藏"""
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            # Default source type is video, reconnect should be hidden
            assert not card._reconnect_container.isVisible()
        finally:
            card.deleteLater()
            app.processEvents()

    def test_stream_name_placeholder_settable(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_stream_name_placeholder("stream5")
            assert card._stream_name_input.placeholderText() == "stream5"
        finally:
            card.deleteLater()
            app.processEvents()
