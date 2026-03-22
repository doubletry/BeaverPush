"""
Catppuccin Mocha 深色主题
=========================

提供全局统一的颜色常量和 QSS 样式表，供所有 QWidgets 组件引用。
基于 Catppuccin Mocha 色板：https://catppuccin.com/palette

使用方式::

    from push_client.views.theme import Theme
    label.setStyleSheet(f"color: {Theme.TEXT};")
    app.setStyleSheet(Theme.global_stylesheet())
"""

from __future__ import annotations

from .. import ASSETS_DIR as _ASSETS_DIR


class Theme:
    """Catppuccin Mocha 主题色板与全局样式表。

    所有颜色均为 ``#RRGGBB`` 格式的字符串常量，
    可直接在 QSS 或 QPalette 中使用。
    """

    # ── 基础色（由深到浅）──
    BASE   = "#1e1e2e"  # 主背景
    MANTLE = "#181825"  # 次级背景（卡片、侧栏）
    CRUST  = "#11111b"  # 最深背景

    # ── 表面色（输入框、边框、分隔线）──
    SURFACE0 = "#313244"
    SURFACE1 = "#45475a"
    SURFACE2 = "#585b70"

    # ── 覆盖色（禁用文本、占位符）──
    OVERLAY0 = "#6c7086"
    OVERLAY1 = "#7f849c"
    OVERLAY2 = "#9399b2"

    # ── 副文本色 ──
    SUBTEXT0 = "#a6adc8"
    SUBTEXT1 = "#bac2de"

    # ── 主文本色 ──
    TEXT = "#cdd6f4"

    # ── 强调色（彩色调色板）──
    ROSEWATER = "#f5e0dc"
    FLAMINGO  = "#f2cdcd"
    PINK      = "#f5c2e7"
    MAUVE     = "#cba6f7"
    RED       = "#f38ba8"
    MAROON    = "#eba0ac"
    PEACH     = "#fab387"
    YELLOW    = "#f9e2af"
    GREEN     = "#a6e3a1"
    TEAL      = "#94e2d5"
    SKY       = "#89dceb"
    SAPPHIRE  = "#74c7ec"
    BLUE      = "#89b4fa"
    LAVENDER  = "#b4befe"

    # ── 语义色 ──
    ACCENT  = BLUE
    SUCCESS = GREEN
    ERROR   = RED
    WARNING = YELLOW

    # ── 字体 ──
    FONT_FAMILY = "Microsoft YaHei UI"
    FONT_SIZE_SMALL  = 8    # pt
    FONT_SIZE_NORMAL = 9    # pt
    FONT_SIZE_LARGE  = 11   # pt

    # ── 圆角半径 ──
    RADIUS_SMALL  = 4
    RADIUS_NORMAL = 8
    RADIUS_LARGE  = 12

    @classmethod
    def global_stylesheet(cls) -> str:
        """生成应用级全局 QSS 样式表。

        包含 QWidget、QPushButton、QLineEdit、QComboBox、QScrollArea、
        QCheckBox、QLabel、QDialog、QToolTip 等组件的默认样式。

        Returns:
            完整的 QSS 字符串，可通过 ``QApplication.setStyleSheet()`` 应用。
        """
        return f"""
        /* ── 全局基础 ── */
        QWidget {{
            background-color: {cls.BASE};
            color: {cls.TEXT};
        }}

        /* ── 标签透明底 ── */
        QLabel {{
            background-color: transparent;
        }}

        /* ── 按钮 ── */
        QPushButton {{
            background-color: {cls.SURFACE1};
            color: {cls.TEXT};
            border: 1px solid {cls.SURFACE2};
            border-radius: {cls.RADIUS_NORMAL}px;
            padding: 5px 14px;
        }}
        QPushButton:hover {{
            background-color: {cls.SURFACE2};
        }}
        QPushButton:pressed {{
            background-color: {cls.SURFACE0};
        }}
        QPushButton:disabled {{
            background-color: {cls.SURFACE0};
            color: {cls.OVERLAY0};
            border-color: {cls.SURFACE0};
        }}

        /* ── 输入框 ── */
        QLineEdit {{
            background-color: {cls.SURFACE0};
            color: {cls.TEXT};
            border: 1px solid {cls.SURFACE1};
            border-radius: {cls.RADIUS_NORMAL}px;
            padding: 4px 8px;
            selection-background-color: {cls.BLUE};
            selection-color: {cls.BASE};
        }}
        QLineEdit:focus {{
            border-color: {cls.BLUE};
        }}
        QLineEdit:read-only {{
            background-color: {cls.CRUST};
            color: {cls.OVERLAY0};
            border-color: {cls.SURFACE0};
        }}

        /* ── 下拉框 ── */
        QComboBox {{
            background-color: {cls.SURFACE0};
            color: {cls.TEXT};
            border: 1px solid {cls.SURFACE1};
            border-radius: {cls.RADIUS_NORMAL}px;
            padding: 4px 8px;
        }}
        QComboBox:hover {{
            border-color: {cls.BLUE};
        }}
        QComboBox:disabled {{
            background-color: {cls.CRUST};
            color: {cls.OVERLAY0};
            border-color: {cls.SURFACE0};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {cls.SURFACE0};
            color: {cls.TEXT};
            border: 1px solid {cls.SURFACE1};
            border-radius: {cls.RADIUS_SMALL}px;
            selection-background-color: {cls.SURFACE1};
            selection-color: {cls.TEXT};
        }}

        /* ── 复选框 ── */
        QCheckBox {{
            color: {cls.TEXT};
            spacing: 6px;
            background-color: transparent;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {cls.SURFACE1};
            border-radius: {cls.RADIUS_SMALL}px;
            background-color: {cls.SURFACE0};
        }}
        QCheckBox::indicator:checked {{
            border-color: {cls.BLUE};
            background-color: {cls.BLUE};
            image: url("{(_ASSETS_DIR / 'checkmark.svg').as_uri()}");
        }}

        /* ── 滚动区域 ── */
        QScrollArea {{
            border: none;
            background-color: transparent;
        }}
        QScrollBar:vertical {{
            background-color: {cls.MANTLE};
            width: 8px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background-color: {cls.SURFACE1};
            border-radius: 4px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: {cls.SURFACE2};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}

        /* ── 工具提示 ── */
        QToolTip {{
            background-color: {cls.SURFACE0};
            color: {cls.TEXT};
            border: 1px solid {cls.SURFACE1};
            border-radius: {cls.RADIUS_SMALL}px;
            padding: 4px;
        }}

        /* ── 对话框 ── */
        QDialog {{
            background-color: {cls.BASE};
        }}
        QMessageBox {{
            background-color: {cls.BASE};
        }}
        """
