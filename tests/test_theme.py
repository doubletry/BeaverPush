from PySide6.QtCore import QFile
from PySide6.QtWidgets import QApplication, QCheckBox, QStyle, QStyleOptionButton

from beaverpush.views.theme import Theme


def test_global_stylesheet_uses_qt_resource_for_checkbox_checkmark():
    stylesheet = Theme.global_stylesheet()

    assert 'image: url(":/assets/checkmark.svg");' in stylesheet
    assert "file:" not in stylesheet
    assert QFile.exists(":/assets/checkmark.svg")


def test_checked_checkbox_renders_checkmark_from_qt_resource():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(Theme.global_stylesheet())

    checkbox = QCheckBox("demo")
    checkbox.setChecked(True)
    checkbox.resize(checkbox.sizeHint())
    checkbox.show()
    app.processEvents()

    try:
        option = QStyleOptionButton()
        checkbox.initStyleOption(option)
        indicator_rect = checkbox.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator,
            option,
            checkbox,
        )
        image = checkbox.grab(indicator_rect).toImage()
        colors = {
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
        }
        assert Theme.BASE.lower() in colors
    finally:
        checkbox.deleteLater()
        app.processEvents()
