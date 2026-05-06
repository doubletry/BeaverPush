from collections import Counter

import pytest

from PySide6.QtCore import QFile
from PySide6.QtWidgets import QApplication, QCheckBox, QStyle, QStyleOptionButton

from beaverpush.views.theme import Theme


def _indicator_color_counts(checkbox: QCheckBox) -> dict[str, int]:
    option = QStyleOptionButton()
    checkbox.initStyleOption(option)
    indicator_rect = checkbox.style().subElementRect(
        QStyle.SubElement.SE_CheckBoxIndicator,
        option,
        checkbox,
    )
    image = checkbox.grab(indicator_rect).toImage()
    return Counter(
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
    )


def test_global_stylesheet_uses_qt_resource_for_checkbox_checkmark():
    stylesheet = Theme.global_stylesheet()

    assert 'image: url(":/assets/checkmark.svg");' in stylesheet
    assert 'image: url("file:' not in stylesheet
    assert QFile.exists(":/assets/checkmark.svg")


@pytest.mark.parametrize(
    ("checked", "enabled", "dominant_color", "expect_checkmark"),
    [
        (False, True, Theme.SURFACE0.lower(), False),
        (True, True, Theme.BLUE.lower(), True),
        (True, False, Theme.OVERLAY1.lower(), True),
    ],
)
def test_checkbox_indicator_renders_expected_theme_state(
    checked: bool,
    enabled: bool,
    dominant_color: str,
    expect_checkmark: bool,
):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(Theme.global_stylesheet())

    checkbox = QCheckBox("demo")
    checkbox.setChecked(checked)
    checkbox.setEnabled(enabled)
    checkbox.resize(checkbox.sizeHint())
    checkbox.show()
    app.processEvents()

    try:
        color_counts = _indicator_color_counts(checkbox)
        if not color_counts:
            pytest.fail("checkbox indicator did not render any pixels")
        actual_dominant_color = max(color_counts, key=lambda clr: color_counts[clr])
        assert actual_dominant_color == dominant_color
        assert (Theme.BASE.lower() in color_counts) is expect_checkmark
    finally:
        checkbox.deleteLater()
        app.processEvents()
