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


class TestHikCameraSourceType:
    """海康工业相机视频源 UI 行为"""

    def test_hikcamera_option_present(self):
        from beaverpush.views.stream_card import SOURCE_TYPES
        keys = [key for key, _ in SOURCE_TYPES]
        assert "hikcamera" in keys

    def test_switch_to_hikcamera_shows_text_input_only(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_source_type("hikcamera")
            app.processEvents()
            assert card._current_source_type == "hikcamera"
            assert not card._source_input.isHidden()
            assert card._device_combo.isHidden()
            assert card._browse_btn.isHidden()
            assert card._refresh_btn.isHidden()
            assert card._loop_check.isHidden()
            assert "SN" in card._source_input.placeholderText()
        finally:
            card.deleteLater()
            app.processEvents()

    def test_reconnect_visible_for_hikcamera_source(self):
        """重连配置对海康相机源应可见，与 RTSP 一致。"""
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_source_type("hikcamera")
            app.processEvents()
            assert not card._reconnect_container.isHidden()
        finally:
            card.deleteLater()
            app.processEvents()

    def test_hikcamera_sn_cached_across_source_type_switch(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_source_type("hikcamera")
            app.processEvents()
            card._source_input.setText("00DA1234567")
            card.set_source_type("video")
            app.processEvents()
            # 切回 hikcamera 应恢复之前输入的 SN
            card.set_source_type("hikcamera")
            app.processEvents()
            assert card._source_input.text() == "00DA1234567"
        finally:
            card.deleteLater()
            app.processEvents()


class TestPositionBadge:
    """验证卡片左上角的序号徽标。"""

    def test_initial_badge_text(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(2)
        try:
            assert card._position_badge.text() == "#3"
            assert card.get_position_index() == 2
        finally:
            card.deleteLater()
            app.processEvents()

    def test_set_position_index_updates_badge(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_position_index(4)
            assert card._position_badge.text() == "#5"
            assert card.get_position_index() == 4
        finally:
            card.deleteLater()
            app.processEvents()

    def test_set_position_index_ignores_negative(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_position_index(-1)
            assert card._position_badge.text() == "#1"
        finally:
            card.deleteLater()
            app.processEvents()


class TestMoveButtons:
    """验证卡片右下角的上下移动按钮。"""

    def test_move_buttons_disabled_by_default(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            assert not card._move_up_btn.isEnabled()
            assert not card._move_down_btn.isEnabled()
        finally:
            card.deleteLater()
            app.processEvents()

    def test_set_move_buttons_enabled_applies_flags(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_move_buttons_enabled(can_up=True, can_down=False)
            assert card._move_up_btn.isEnabled()
            assert not card._move_down_btn.isEnabled()
            card.set_move_buttons_enabled(can_up=False, can_down=True)
            assert not card._move_up_btn.isEnabled()
            assert card._move_down_btn.isEnabled()
        finally:
            card.deleteLater()
            app.processEvents()

    def test_streaming_disables_move_buttons(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_move_buttons_enabled(can_up=True, can_down=True)
            card.set_buttons_streaming(True)
            assert not card._move_up_btn.isEnabled()
            assert not card._move_down_btn.isEnabled()
            # 停止推流后恢复
            card.set_buttons_streaming(False)
            assert card._move_up_btn.isEnabled()
            assert card._move_down_btn.isEnabled()
        finally:
            card.deleteLater()
            app.processEvents()

    def test_move_up_button_emits_signal(self):
        app = QApplication.instance() or QApplication([])
        card = StreamCardView(0)
        try:
            card.set_move_buttons_enabled(can_up=True, can_down=True)
            received = []
            card.move_up_clicked.connect(lambda: received.append("up"))
            card.move_down_clicked.connect(lambda: received.append("down"))
            card._move_up_btn.click()
            card._move_down_btn.click()
            assert received == ["up", "down"]
        finally:
            card.deleteLater()
            app.processEvents()
