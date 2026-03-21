"""
应用主窗口
==========

``MainWindow`` 是应用的顶层窗口，包含：

    - 顶部工具栏：RTSP 服务器地址输入、测试连接、添加通道、保存配置
    - 中部内容区：可滚动的推流通道卡片列表（每路一个 ``StreamCardView``）
    - 底部状态栏：显示当前操作状态

设计原则：
    - 窗口自身 **不包含业务逻辑**，只负责布局和发出用户交互信号
    - Controller 通过公共方法操作窗口内容
"""

from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea,
    QMessageBox,
)

from .theme import Theme
from .stream_card import StreamCardView


class MainWindow(QMainWindow):
    """RTSP 推流客户端主窗口。

    Signals:
        test_clicked():            用户点击"测试连接"
        add_stream_clicked():      用户点击"＋ 添加通道"
        save_config_clicked():     用户点击"💾 保存配置"
        server_changed(str):       用户编辑 RTSP 服务器地址
        close_requested(event):    用户关闭窗口（携带 QCloseEvent）
    """

    test_clicked       = Signal()
    add_stream_clicked = Signal()
    save_config_clicked = Signal()
    server_changed     = Signal(str)
    # 客户端 ID 变更信号
    client_id_changed  = Signal(str)
    # 全部开始/停止推流信号
    start_all_clicked  = Signal()
    stop_all_clicked   = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RTSP 推流客户端")
        self.resize(1100, 650)
        self.setMinimumSize(900, 400)

        self._cards: list[StreamCardView] = []
        self._build_ui()

    # ==================================================================
    #  UI 构建
    # ==================================================================

    def _build_ui(self):
        """构建主窗口布局：工具栏 + 全局参数 + 卡片列表 + 状态栏。"""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── 顶部工具栏 ──
        root.addLayout(self._build_toolbar())
        # ── 客户端 ID + 全部开始/停止 ──
        root.addLayout(self._build_client_row())
        # ── 卡片滚动区域 ──
        root.addWidget(self._build_scroll_area(), 1)
        # ── 底部状态栏 ──
        root.addWidget(self._build_status_bar())

    def _build_toolbar(self) -> QHBoxLayout:
        """构建顶部工具栏。"""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        toolbar.addWidget(QLabel("RTSP 服务器地址:"))

        self._server_input = QLineEdit()
        self._server_input.setPlaceholderText("如 rtsp://192.168.1.100:8554")
        self._server_input.textChanged.connect(self.server_changed.emit)
        toolbar.addWidget(self._server_input, 1)

        # 锁定/解锁 RTSP 地址
        self._lock_btn = QPushButton("🔓")
        self._lock_btn.setFixedWidth(36)
        self._lock_btn.setToolTip("锁定 RTSP 地址，防止误修改")
        self._lock_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.SURFACE1};
                color: {Theme.TEXT};
                border: 1px solid {Theme.SURFACE2};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.SURFACE2}; }}
        """)
        self._lock_btn.clicked.connect(self._toggle_server_lock)
        toolbar.addWidget(self._lock_btn)

        # 测试连接
        self._test_btn = QPushButton("测试连接")
        self._test_btn.setFixedWidth(90)
        self._test_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.MAUVE};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.MAUVE};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.LAVENDER}; }}
        """)
        self._test_btn.clicked.connect(self.test_clicked.emit)
        toolbar.addWidget(self._test_btn)

        # 添加通道
        add_btn = QPushButton("＋ 添加通道")
        add_btn.setFixedWidth(110)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BLUE};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.BLUE};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.SAPPHIRE}; }}
        """)
        add_btn.clicked.connect(self.add_stream_clicked.emit)
        toolbar.addWidget(add_btn)

        # 保存配置
        save_btn = QPushButton("💾 保存配置")
        save_btn.setFixedWidth(100)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.YELLOW};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.YELLOW};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.PINK}; }}
        """)
        save_btn.clicked.connect(self.save_config_clicked.emit)
        toolbar.addWidget(save_btn)

        return toolbar

    def _toggle_server_lock(self):
        """切换 RTSP 服务器地址的锁定/解锁状态。"""
        self.set_server_locked(not self._server_input.isReadOnly())

    def set_server_locked(self, locked: bool):
        """设置 RTSP 服务器地址的锁定状态。

        Args:
            locked: ``True`` 表示锁定（只读）。
        """
        self._server_input.setReadOnly(locked)
        if locked:
            self._lock_btn.setText("🔒")
            self._lock_btn.setToolTip("点击解锁 RTSP 地址")
            self._server_input.setStyleSheet(
                f"background-color: {Theme.CRUST}; color: {Theme.SUBTEXT0};"
            )
        else:
            self._lock_btn.setText("🔓")
            self._lock_btn.setToolTip("锁定 RTSP 地址，防止误修改")
            self._server_input.setStyleSheet("")

    def get_server_locked(self) -> bool:
        """获取 RTSP 服务器地址的锁定状态。"""
        return self._server_input.isReadOnly()

    def _build_client_row(self) -> QHBoxLayout:
        """构建客户端 ID 输入 + 全部开始/停止推流按钮行。"""
        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("客户端 ID:"))
        self._client_id_input = QLineEdit()
        self._client_id_input.setPlaceholderText("如 client01")
        self._client_id_input.setFixedWidth(160)
        self._client_id_input.textChanged.connect(self.client_id_changed.emit)
        row.addWidget(self._client_id_input)

        row.addStretch()

        # 全部开始推流
        self._start_all_btn = QPushButton("▶ 全部开始推流")
        self._start_all_btn.setFixedWidth(120)
        self._start_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.GREEN};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.GREEN};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.TEAL}; }}
        """)
        self._start_all_btn.clicked.connect(self.start_all_clicked.emit)
        row.addWidget(self._start_all_btn)

        # 全部停止推流
        self._stop_all_btn = QPushButton("■ 全部停止推流")
        self._stop_all_btn.setFixedWidth(120)
        self._stop_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.RED};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.RED};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.MAROON}; }}
        """)
        self._stop_all_btn.clicked.connect(self.stop_all_clicked.emit)
        row.addWidget(self._stop_all_btn)

        return row

    def _build_scroll_area(self) -> QScrollArea:
        """构建可滚动的卡片容器。"""
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 6, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch()  # 底部弹簧

        # 空状态提示
        self._empty_label = QLabel("暂无推流通道\n点击上方「＋ 添加通道」开始")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_font = QFont(Theme.FONT_FAMILY)
        empty_font.setPointSize(Theme.FONT_SIZE_LARGE)
        self._empty_label.setFont(empty_font)
        self._empty_label.setStyleSheet(f"color: {Theme.OVERLAY0};")
        self._cards_layout.insertWidget(0, self._empty_label)

        self._scroll_area.setWidget(self._cards_container)
        return self._scroll_area

    def _build_status_bar(self) -> QWidget:
        """构建底部状态栏。"""
        bar = QWidget()
        bar.setFixedHeight(28)
        bar.setStyleSheet(f"""
            background-color: {Theme.MANTLE};
            border: 1px solid {Theme.SURFACE0};
            border-radius: {Theme.RADIUS_SMALL}px;
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 12, 0)

        self._status_label = QLabel("就绪")
        status_font = QFont(Theme.FONT_FAMILY)
        status_font.setPointSize(Theme.FONT_SIZE_SMALL)
        self._status_label.setFont(status_font)
        self._status_label.setStyleSheet(
            f"color: {Theme.SUBTEXT0}; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(self._status_label)
        return bar

    # ==================================================================
    #  公共方法：供 Controller 调用
    # ==================================================================

    def get_server(self) -> str:
        """获取 RTSP 服务器地址输入框文本。"""
        return self._server_input.text()

    def set_server(self, url: str):
        """设置 RTSP 服务器地址（不触发信号）。"""
        self._server_input.blockSignals(True)
        self._server_input.setText(url)
        self._server_input.blockSignals(False)

    def set_status(self, message: str):
        """更新底部状态栏文本。"""
        self._status_label.setText(message)

    def set_test_button_testing(self, testing: bool):
        """切换测试按钮状态。"""
        self._test_btn.setEnabled(not testing)
        self._test_btn.setText("测试中..." if testing else "测试连接")

    # ── 客户端 ID ──

    def get_client_id(self) -> str:
        return self._client_id_input.text()

    def set_client_id(self, cid: str):
        self._client_id_input.blockSignals(True)
        self._client_id_input.setText(cid)
        self._client_id_input.blockSignals(False)

    def add_card(self, card: StreamCardView):
        """向卡片列表添加一张卡片。"""
        self._cards.append(card)
        # 插入到 stretch 之前
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        self._empty_label.setVisible(False)

    def remove_card(self, card: StreamCardView):
        """从卡片列表移除一张卡片。"""
        if card in self._cards:
            self._cards.remove(card)
            self._cards_layout.removeWidget(card)
            card.deleteLater()
            self._empty_label.setVisible(len(self._cards) == 0)

    def get_cards(self) -> list[StreamCardView]:
        """获取所有卡片引用。"""
        return list(self._cards)

    def show_test_result(self, success: bool, message: str):
        """弹出测试结果对话框。"""
        icon = QMessageBox.Icon.Information if success else QMessageBox.Icon.Warning
        title = "测试结果"
        prefix = "✅ " if success else "❌ "
        QMessageBox(icon, title, prefix + message, QMessageBox.StandardButton.Ok, self).exec()

    def confirm_close(self, streaming_count: int) -> bool:
        """弹出退出确认对话框。

        Args:
            streaming_count: 当前正在推流的通道数量。

        Returns:
            用户是否确认退出。
        """
        result = QMessageBox.question(
            self, "确认退出",
            f"当前有 {streaming_count} 路推流正在进行，确定退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    # ==================================================================
    #  事件重写
    # ==================================================================

    def closeEvent(self, event):
        """重写关闭事件，交给 Controller 处理。

        Controller 会在 ``close_requested`` 之前通过 ``confirm_close``
        询问用户是否退出，这里直接接受/拒绝不做额外逻辑。
        """
        # Controller 在初始化时会替换此行为
        super().closeEvent(event)
