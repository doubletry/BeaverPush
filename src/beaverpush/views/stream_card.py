"""
单路推流通道卡片控件
====================

``StreamCardView`` 是一个自定义 QFrame 控件，对应一路推流通道的 UI。
包含三行布局：

    第 1 行：视频源类型选择、路径/设备输入、浏览/刷新按钮、循环选项
    第 2 行：推流参数（流名称、编码器、分辨率、帧率、码率、预览开关）
    第 3 行：控制按钮（开始/停止/移除）、状态标签、进度信息

设计原则：
    - 控件自身 **不包含业务逻辑**，只负责展示和发出用户交互信号
    - 所有按钮点击、下拉选择、文本变更均通过回调 / 公共方法暴露给 Controller
    - Controller 调用 ``set_*`` / ``get_*`` 系列方法读写 UI 状态
"""

from __future__ import annotations

from PySide6.QtCore import Signal, Qt, QRegularExpression
from PySide6.QtGui import QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QCheckBox, QFileDialog, QMessageBox,
    QWidget,
)

from .theme import Theme


# ── 视频源类型常量（key → 显示文本）──
SOURCE_TYPES: list[tuple[str, str]] = [
    ("video",     "本地视频"),
    ("camera",    "本地摄像头"),
    ("rtsp",      "RTSP 源"),
    ("screen",    "全屏画面"),
    ("window",    "应用窗口"),
    ("hikcamera", "海康工业相机"),
]

# ── 编码器选项 ──
# 默认包含全部可能的编码器；运行时由 :func:`set_available_codecs` 根据硬件
# 探测结果裁剪（不可用的硬件编码器会被移除，避免用户选了之后启动失败）。
ALL_CODEC_OPTIONS: list[str] = [
    "自动", "copy",
    "libx264", "libx265",
    "h264_nvenc", "hevc_nvenc",
    "h264_qsv", "hevc_qsv",
]
CODEC_OPTIONS: list[str] = ALL_CODEC_OPTIONS[:]


def set_available_codecs(available: list[str]) -> None:
    """根据硬件探测结果裁剪 :data:`CODEC_OPTIONS`。

    * 始终保留 ``"自动"`` 与 ``"copy"`` 两个非编码器选项。
    * 顺序按 :data:`ALL_CODEC_OPTIONS` 原始顺序保留，便于下拉框稳定。
    * 必须在创建 :class:`StreamCardView` 之前调用才能影响新建的卡片。
    """
    global CODEC_OPTIONS
    keep = set(available) | {"自动", "copy"}
    CODEC_OPTIONS = [c for c in ALL_CODEC_OPTIONS if c in keep]


class StreamCardView(QFrame):
    """单路推流通道卡片 UI 组件。

    Signals:
        source_type_changed(str):  用户切换视频源类型时发出（key 值）
        source_path_edited(str):   用户编辑路径/URL 输入框时发出
        device_selected(str):      用户在设备下拉框选择设备时发出（value）
        browse_clicked():          用户点击"浏览..."按钮
        refresh_clicked():         用户点击"刷新"按钮
        start_clicked():           用户点击"开始推流"按钮
        stop_clicked():            用户点击"停止"按钮
        remove_clicked():          用户点击"移除"按钮
        stream_name_edited(str):   用户编辑流名称时发出
        codec_changed(str):        用户切换编码器时发出
        width_edited(str):         用户编辑宽度时发出
        height_edited(str):        用户编辑高度时发出
        fps_edited(str):           用户编辑帧率时发出
        bitrate_edited(str):       用户编辑码率时发出
        loop_toggled(bool):        用户切换循环复选框时发出
        preview_toggled(bool):     用户切换预览复选框时发出
    """

    # ── 信号定义 ──
    source_type_changed = Signal(str)
    source_path_edited  = Signal(str)
    device_selected     = Signal(str)
    browse_clicked      = Signal()
    refresh_clicked     = Signal()
    start_clicked       = Signal()
    stop_clicked        = Signal()
    remove_clicked      = Signal()
    stream_name_edited  = Signal(str)
    codec_changed       = Signal(str)
    width_edited        = Signal(str)
    height_edited       = Signal(str)
    fps_edited          = Signal(str)
    bitrate_edited      = Signal(str)
    source_reconnect_interval_edited = Signal(str)
    source_reconnect_max_attempts_edited = Signal(str)
    loop_toggled        = Signal(bool)
    preview_clicked      = Signal()
    title_edited        = Signal(str)
    move_up_clicked     = Signal()
    move_down_clicked   = Signal()

    def __init__(self, channel_index: int, parent=None):
        """初始化推流通道卡片。

        Args:
            channel_index: 通道在列表中的索引（从 0 开始），用于标题显示。
            parent: 父控件。
        """
        super().__init__(parent)
        self._channel_index = channel_index
        self._position_index = channel_index  # 列表位置（由 Controller 在排序/增删后刷新）
        self._config_locked = False
        self._streaming = False
        self._can_move_up = False
        self._can_move_down = False
        self._source_paths_cache: dict[str, str] = {}  # 每种源类型保存的路径
        self._current_source_type: str = "video"  # 当前源类型

        # 应用卡片级样式
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            StreamCardView {{
                background-color: {Theme.MANTLE};
                border: 1px solid {Theme.SURFACE0};
                border-radius: {Theme.RADIUS_LARGE}px;
            }}
        """)

        self._build_ui()
        self._connect_signals()

    # ==================================================================
    #  UI 构建
    # ==================================================================

    def _build_ui(self):
        """构建三行布局 + 可折叠高级配置。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # ── 标题栏（可点击编辑）──
        self._title_text = f"推流通道 {self._channel_index + 1}"

        # 序号徽标（显示卡片在列表中的位置）
        self._position_badge = QLabel(f"#{self._position_index + 1}")
        badge_font = QFont()
        badge_font.setBold(True)
        self._position_badge.setFont(badge_font)
        self._position_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._position_badge.setFixedWidth(36)
        self._position_badge.setStyleSheet(f"""
            background-color: {Theme.MAUVE};
            color: {Theme.BASE};
            border-radius: {Theme.RADIUS_SMALL}px;
            padding: 4px;
        """)
        self._position_badge.setToolTip("当前卡片在列表中的位置")

        self._title_label = QLabel(self._title_text)
        title_font = QFont()
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        self._title_label.setStyleSheet(f"""
            background-color: {Theme.BLUE};
            color: {Theme.BASE};
            border-radius: {Theme.RADIUS_SMALL}px;
            padding: 4px;
        """)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_label.setToolTip("点击修改通道名称")
        self._title_label.mousePressEvent = self._on_title_clicked

        self._title_edit = QLineEdit()
        self._title_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_edit.setVisible(False)
        self._title_edit.returnPressed.connect(self._finish_title_edit)
        self._title_edit.editingFinished.connect(self._finish_title_edit)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self._position_badge)
        title_row.addWidget(self._title_label, 1)
        title_row.addWidget(self._title_edit, 1)
        root.addLayout(title_row)

        # ── 第 1 行：视频源选择 ──
        root.addLayout(self._build_row1())
        # ── 第 2 行：基本参数（流名称 + 配置模式切换 + 预览）──
        root.addLayout(self._build_row2())
        # ── 高级配置面板（默认隐藏）──
        self._advanced_panel = self._build_advanced_panel()
        self._advanced_panel.setVisible(False)
        root.addWidget(self._advanced_panel)
        # ── 第 3 行：控制 + 状态 ──
        root.addLayout(self._build_row3())

    def _build_row1(self) -> QHBoxLayout:
        """第 1 行：视频源类型 + 路径/设备 + 浏览/刷新 + 循环。"""
        row = QHBoxLayout()
        row.setSpacing(8)

        lbl = QLabel("视频源:")
        lbl.setFixedWidth(50)
        row.addWidget(lbl)

        # 源类型下拉框
        self._source_type_combo = QComboBox()
        self._source_type_combo.setFixedWidth(110)
        for key, label in SOURCE_TYPES:
            self._source_type_combo.addItem(label, key)
        row.addWidget(self._source_type_combo)

        # 路径输入框（本地视频 / RTSP 地址）
        self._source_input = QLineEdit()
        self._source_input.setPlaceholderText("视频文件路径")
        row.addWidget(self._source_input, 1)  # stretch=1 填满剩余空间

        # 设备下拉框（摄像头/屏幕/窗口）
        self._device_combo = QComboBox()
        self._device_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._device_combo.setMinimumWidth(150)
        self._device_combo.hide()
        row.addWidget(self._device_combo, 1)

        # 浏览文件按钮
        self._browse_btn = QPushButton("浏览...")
        self._browse_btn.setFixedWidth(70)
        row.addWidget(self._browse_btn)

        # 刷新设备按钮
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.setFixedWidth(55)
        self._refresh_btn.hide()
        row.addWidget(self._refresh_btn)

        # 循环播放复选框
        self._loop_check = QCheckBox("循环")
        row.addWidget(self._loop_check)

        return row

    def _build_row2(self) -> QHBoxLayout:
        """第 2 行：流名称 + 配置模式切换 + 预览。"""
        row = QHBoxLayout()
        row.setSpacing(8)

        lbl = QLabel("流名称:")
        lbl.setFixedWidth(50)
        row.addWidget(lbl)
        self._stream_name_input = QLineEdit()
        self._stream_name_input.setPlaceholderText("stream1")
        # 只允许 ASCII 字母、数字以及 . _ - 三种特殊符号
        self._stream_name_input.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[A-Za-z0-9._\-]*"))
        )
        row.addWidget(self._stream_name_input, 1)

        # 配置模式切换
        row.addWidget(QLabel("配置:"))
        self._settings_combo = QComboBox()
        self._settings_combo.setFixedWidth(100)
        self._settings_combo.addItems(["基本设置", "高级设置"])
        row.addWidget(self._settings_combo)

        return row

    @staticmethod
    def _make_separator() -> QFrame:
        """创建垂直分隔线，用于区分高级设置中的参数组。"""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {Theme.SURFACE2};")
        sep.setFixedWidth(1)
        return sep

    def _build_advanced_panel(self) -> QWidget:
        """构建高级配置面板（编码、分辨率、帧率、码率、重连）。"""
        panel = QWidget()
        row = QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        # ── 编码 ──
        lbl = QLabel("编码:")
        lbl.setFixedWidth(50)
        row.addWidget(lbl)
        self._codec_combo = QComboBox()
        self._codec_combo.setFixedWidth(110)
        self._codec_combo.addItems(CODEC_OPTIONS)
        row.addWidget(self._codec_combo)

        row.addWidget(self._make_separator())

        # ── 分辨率 ──
        row.addWidget(QLabel("分辨率:"))
        self._width_input = QLineEdit()
        self._width_input.setPlaceholderText("宽")
        self._width_input.setFixedWidth(55)
        self._width_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._width_input)
        row.addWidget(QLabel("x"))
        self._height_input = QLineEdit()
        self._height_input.setPlaceholderText("高")
        self._height_input.setFixedWidth(55)
        self._height_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._height_input)

        row.addWidget(self._make_separator())

        # ── 帧率 ──
        row.addWidget(QLabel("帧率:"))
        self._fps_input = QLineEdit()
        self._fps_input.setPlaceholderText("30")
        self._fps_input.setFixedWidth(50)
        self._fps_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._fps_input)

        row.addWidget(self._make_separator())

        # ── 码率 ──
        row.addWidget(QLabel("码率:"))
        self._bitrate_input = QLineEdit()
        self._bitrate_input.setPlaceholderText("")
        self._bitrate_input.setFixedWidth(50)
        self._bitrate_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bitrate_input.setToolTip("码率单位固定为 M，例如输入 2 表示 2M")
        row.addWidget(self._bitrate_input)
        row.addWidget(QLabel("M"))

        # ── 重连配置（仅对 RTSP 视频源有效，其他源类型时隐藏）──
        self._reconnect_container = QWidget()
        reconnect_layout = QHBoxLayout(self._reconnect_container)
        reconnect_layout.setContentsMargins(0, 0, 0, 0)
        reconnect_layout.setSpacing(8)

        reconnect_layout.addWidget(self._make_separator())

        reconnect_layout.addWidget(QLabel("重连间隔:"))
        self._source_reconnect_interval_input = QLineEdit()
        self._source_reconnect_interval_input.setPlaceholderText("5")
        self._source_reconnect_interval_input.setFixedWidth(45)
        self._source_reconnect_interval_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        reconnect_layout.addWidget(self._source_reconnect_interval_input)
        reconnect_layout.addWidget(QLabel("秒"))

        reconnect_layout.addWidget(QLabel("最大尝试:"))
        self._source_reconnect_max_attempts_input = QLineEdit()
        self._source_reconnect_max_attempts_input.setPlaceholderText("0=无限")
        self._source_reconnect_max_attempts_input.setFixedWidth(45)
        self._source_reconnect_max_attempts_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source_reconnect_max_attempts_input.setToolTip("设置为 0 表示无限重连")
        reconnect_layout.addWidget(self._source_reconnect_max_attempts_input)
        reconnect_layout.addWidget(QLabel("次"))

        row.addWidget(self._reconnect_container)
        # 默认隐藏重连配置（仅 RTSP 源时显示）
        self._reconnect_container.setVisible(False)

        row.addStretch()
        return panel

    def _build_row3(self) -> QHBoxLayout:
        """第 3 行：控制按钮 + 状态。"""
        row = QHBoxLayout()
        row.setSpacing(8)

        # 开始推流
        self._start_btn = QPushButton("\u25b6 开始推流")
        self._start_btn.setFixedWidth(100)
        self._start_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.GREEN};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.GREEN};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.TEAL}; }}
            QPushButton:disabled {{
                background-color: {Theme.SURFACE1};
                color: {Theme.OVERLAY0};
                border-color: {Theme.SURFACE1};
            }}
        """)
        row.addWidget(self._start_btn)

        # 预览按钮（仅推流中可用）
        self._preview_btn = QPushButton("\U0001f441 预览")
        self._preview_btn.setFixedWidth(90)
        self._preview_btn.setEnabled(False)
        self._preview_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BLUE};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.BLUE};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.SAPPHIRE}; }}
            QPushButton:disabled {{
                background-color: {Theme.SURFACE1};
                color: {Theme.OVERLAY0};
                border-color: {Theme.SURFACE1};
            }}
        """)
        row.addWidget(self._preview_btn)

        # 停止推流
        self._stop_btn = QPushButton("■ 停止推流")
        self._stop_btn.setFixedWidth(90)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.RED};
                color: {Theme.BASE};
                font-weight: bold;
                border: 1px solid {Theme.RED};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{ background-color: {Theme.MAROON}; }}
            QPushButton:disabled {{
                background-color: {Theme.SURFACE1};
                color: {Theme.OVERLAY0};
                border-color: {Theme.SURFACE1};
            }}
        """)
        row.addWidget(self._stop_btn)

        # 移除通道
        self._remove_btn = QPushButton("✕ 移除")
        self._remove_btn.setFixedWidth(70)
        self._remove_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {Theme.RED};
                border: 1px solid {Theme.RED};
                border-radius: {Theme.RADIUS_NORMAL}px;
            }}
            QPushButton:hover {{
                background-color: {Theme.RED};
                color: {Theme.BASE};
            }}
        """)
        row.addWidget(self._remove_btn)

        # 状态标签
        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet(f"color: {Theme.OVERLAY0};")
        row.addWidget(self._status_label)

        row.addStretch()

        # 上下移动按钮（用于卡片排序）
        move_btn_style = f"""
            QPushButton {{
                background-color: transparent;
                color: {Theme.OVERLAY2};
                border: 1px solid {Theme.SURFACE2};
                border-radius: {Theme.RADIUS_SMALL}px;
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover:enabled {{
                background-color: {Theme.SURFACE1};
                color: {Theme.TEXT};
            }}
            QPushButton:disabled {{
                color: {Theme.OVERLAY0};
                border-color: {Theme.SURFACE1};
            }}
        """
        self._move_up_btn = QPushButton("\u2191")
        self._move_up_btn.setFixedSize(28, 24)
        self._move_up_btn.setToolTip("上移卡片")
        self._move_up_btn.setStyleSheet(move_btn_style)
        self._move_up_btn.setEnabled(False)
        row.addWidget(self._move_up_btn)

        self._move_down_btn = QPushButton("\u2193")
        self._move_down_btn.setFixedSize(28, 24)
        self._move_down_btn.setToolTip("下移卡片")
        self._move_down_btn.setStyleSheet(move_btn_style)
        self._move_down_btn.setEnabled(False)
        row.addWidget(self._move_down_btn)

        return row

    # ==================================================================
    #  信号连接
    # ==================================================================

    def _connect_signals(self):
        """将控件内部信号转发为卡片级信号，供 Controller 监听。"""
        # 源类型切换
        self._source_type_combo.currentIndexChanged.connect(self._on_source_type_changed)
        # 路径编辑
        self._source_input.textChanged.connect(self.source_path_edited.emit)
        # 设备选择
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        # 按钮
        self._browse_btn.clicked.connect(self.browse_clicked.emit)
        self._refresh_btn.clicked.connect(self.refresh_clicked.emit)
        self._start_btn.clicked.connect(self.start_clicked.emit)
        self._stop_btn.clicked.connect(self.stop_clicked.emit)
        self._remove_btn.clicked.connect(self.remove_clicked.emit)
        self._move_up_btn.clicked.connect(self.move_up_clicked.emit)
        self._move_down_btn.clicked.connect(self.move_down_clicked.emit)
        # 参数编辑
        self._stream_name_input.textChanged.connect(self.stream_name_edited.emit)
        self._codec_combo.currentTextChanged.connect(self.codec_changed.emit)
        self._width_input.textChanged.connect(self.width_edited.emit)
        self._height_input.textChanged.connect(self.height_edited.emit)
        self._fps_input.textChanged.connect(self.fps_edited.emit)
        self._bitrate_input.textChanged.connect(self._emit_bitrate)
        self._source_reconnect_interval_input.textChanged.connect(
            self.source_reconnect_interval_edited.emit
        )
        self._source_reconnect_max_attempts_input.textChanged.connect(
            self.source_reconnect_max_attempts_edited.emit
        )
        # 复选框
        self._loop_check.toggled.connect(self.loop_toggled.emit)
        # 预览按钮
        self._preview_btn.clicked.connect(self.preview_clicked.emit)
        # 高级/基本设置切换
        self._settings_combo.currentIndexChanged.connect(self._on_settings_mode_changed)

    def _on_settings_mode_changed(self, idx: int):
        """切换基本/高级设置模式。"""
        self._advanced_panel.setVisible(idx == 1)

    def _on_source_type_changed(self, idx: int):
        """源类型切换时更新 UI 可见性并发出信号。"""
        key = self._source_type_combo.itemData(idx)
        is_text_input = key in ("video", "rtsp", "hikcamera")
        is_device = key in ("camera", "screen", "window")

        # 保存当前源类型的输入值（在切换前）
        prev_key = getattr(self, "_current_source_type", None)
        if prev_key and prev_key in ("video", "rtsp", "hikcamera"):
            self._source_paths_cache[prev_key] = self._source_input.text()

        # 切换输入 / 设备下拉框
        self._source_input.setVisible(is_text_input)
        self._device_combo.setVisible(is_device)
        self._browse_btn.setVisible(key == "video")
        self._refresh_btn.setVisible(is_device)
        self._loop_check.setVisible(key == "video")

        # 重连配置对 RTSP 源 和 海康相机 都有效（断线后会按相同机制重连）
        self._reconnect_container.setVisible(key in ("rtsp", "hikcamera"))

        # 恢复之前保存的源路径（而不是清空）
        self._source_input.blockSignals(True)
        if is_text_input:
            cached = self._source_paths_cache.get(key, "")
            self._source_input.setText(cached)
        else:
            self._source_input.clear()
        self._source_input.blockSignals(False)
        self._device_combo.blockSignals(True)
        self._device_combo.setCurrentIndex(-1)
        self._device_combo.blockSignals(False)

        # 更新 placeholder
        if key == "video":
            self._source_input.setPlaceholderText("视频文件路径")
        elif key == "rtsp":
            self._source_input.setPlaceholderText("RTSP 地址")
        elif key == "hikcamera":
            self._source_input.setPlaceholderText("海康相机 SN，例如：00DA1234567")

        self._current_source_type = key
        self.source_type_changed.emit(key)

    def _on_device_selected(self, idx: int):
        """设备下拉选择变更，发出 value。"""
        value = self._device_combo.itemData(idx)
        if value is not None:
            self.device_selected.emit(str(value))

    def _emit_bitrate(self):
        """组合码率数值和单位，发出 bitrate_edited 信号。"""
        self.bitrate_edited.emit(self.get_bitrate())

    # ── 标题编辑 ──

    def _on_title_clicked(self, _event):
        """点击标题标签，切换为编辑模式。"""
        if self._config_locked:
            return
        self._title_label.setVisible(False)
        self._title_edit.setText(self._title_text)
        self._title_edit.setVisible(True)
        self._title_edit.setFocus()
        self._title_edit.selectAll()

    def _finish_title_edit(self):
        """完成标题编辑，切换回标签模式。"""
        if not self._title_edit.isVisible():
            return
        new_text = self._title_edit.text().strip()
        if new_text and new_text != self._title_text:
            self._title_text = new_text
            self._title_label.setText(new_text)
            self.title_edited.emit(new_text)
        self._title_edit.setVisible(False)
        self._title_label.setVisible(True)

    # ==================================================================
    #  公共方法：供 Controller 调用读写 UI 状态
    # ==================================================================

    def get_source_type(self) -> str:
        """获取当前选中的视频源类型 key。"""
        return self._source_type_combo.currentData() or ""

    def get_title(self) -> str:
        """获取通道标题。"""
        return self._title_text

    def set_title(self, title: str):
        """设置通道标题（不触发 title_edited 信号）。"""
        if title:
            self._title_text = title
            self._title_label.setText(title)

    def set_source_type(self, key: str):
        """设置视频源类型。"""
        for i in range(self._source_type_combo.count()):
            if self._source_type_combo.itemData(i) == key:
                self._source_type_combo.setCurrentIndex(i)
                return

    def get_source_path(self) -> str:
        """获取路径输入框文本。"""
        return self._source_input.text()

    def set_source_path(self, path: str):
        """设置路径输入框文本（不触发 textChanged 信号）。"""
        self._source_input.blockSignals(True)
        self._source_input.setText(path)
        self._source_input.blockSignals(False)

    def get_stream_name(self) -> str:
        return self._stream_name_input.text()

    def set_stream_name(self, name: str):
        self._stream_name_input.blockSignals(True)
        self._stream_name_input.setText(name)
        self._stream_name_input.blockSignals(False)

    def set_stream_name_placeholder(self, placeholder: str):
        """设置流名称输入框的 placeholder 文本。"""
        self._stream_name_input.setPlaceholderText(placeholder)

    def get_codec(self) -> str:
        return self._codec_combo.currentText()

    def set_codec(self, codec: str):
        idx = self._codec_combo.findText(codec)
        if idx >= 0:
            self._codec_combo.setCurrentIndex(idx)

    def refresh_available_codecs(self):
        """把当前全局 ``CODEC_OPTIONS`` 同步到本卡片的编码器下拉框。

        主要用于启动后异步完成硬件探测时，刷新那些已经创建好的卡片。
        若当前选中的编码器已被裁剪，则回退到 ``"自动"``（若存在），
        否则回退到下拉框第一个选项。
        """
        current = self._codec_combo.currentText()
        items = [self._codec_combo.itemText(i) for i in range(self._codec_combo.count())]
        if items == CODEC_OPTIONS:
            return

        fallback = current
        if fallback not in CODEC_OPTIONS:
            fallback = "自动" if "自动" in CODEC_OPTIONS else (
                CODEC_OPTIONS[0] if CODEC_OPTIONS else ""
            )

        self._codec_combo.blockSignals(True)
        self._codec_combo.clear()
        self._codec_combo.addItems(CODEC_OPTIONS)
        if fallback:
            idx = self._codec_combo.findText(fallback)
            if idx >= 0:
                self._codec_combo.setCurrentIndex(idx)
        self._codec_combo.blockSignals(False)

    def get_width(self) -> str:
        return self._width_input.text()

    def set_width(self, w: str):
        self._width_input.blockSignals(True)
        self._width_input.setText(w)
        self._width_input.blockSignals(False)

    def get_height(self) -> str:
        return self._height_input.text()

    def set_height(self, h: str):
        self._height_input.blockSignals(True)
        self._height_input.setText(h)
        self._height_input.blockSignals(False)

    def get_fps(self) -> str:
        return self._fps_input.text()

    def set_fps(self, fps: str):
        self._fps_input.blockSignals(True)
        self._fps_input.setText(fps)
        self._fps_input.blockSignals(False)

    def get_bitrate(self) -> str:
        """返回标准化后的码率字符串，如 ``"2M"``，空则返回 ``""``。"""
        num = self._bitrate_input.text().strip()
        if not num:
            return ""
        return num if num.upper().endswith("M") else f"{num}M"

    def set_bitrate(self, br: str):
        """从 ``"2M"`` 格式字符串回填码率输入框，旧版 ``K`` 单位会换算为 ``M``。"""
        self._bitrate_input.blockSignals(True)
        if br and br[-1:].upper() == "K":
            try:
                self._bitrate_input.setText(f"{float(br[:-1]) / 1000:g}")
            except ValueError:
                self._bitrate_input.setText(br[:-1])
        elif br and br[-1:].upper() == "M":
            self._bitrate_input.setText(br[:-1])
        else:
            self._bitrate_input.setText(br)
        self._bitrate_input.blockSignals(False)

    def set_advanced_mode(self, advanced: bool):
        """设置高级模式（展开高级面板）。"""
        self._settings_combo.blockSignals(True)
        self._settings_combo.setCurrentIndex(1 if advanced else 0)
        self._settings_combo.blockSignals(False)
        self._advanced_panel.setVisible(advanced)

    def get_source_reconnect_interval(self) -> str:
        return self._source_reconnect_interval_input.text()

    def set_source_reconnect_interval(self, interval: int | str):
        self._source_reconnect_interval_input.blockSignals(True)
        self._source_reconnect_interval_input.setText(str(interval))
        self._source_reconnect_interval_input.blockSignals(False)

    def get_source_reconnect_max_attempts(self) -> str:
        return self._source_reconnect_max_attempts_input.text()

    def set_source_reconnect_max_attempts(self, attempts: int | str):
        self._source_reconnect_max_attempts_input.blockSignals(True)
        self._source_reconnect_max_attempts_input.setText(str(attempts))
        self._source_reconnect_max_attempts_input.blockSignals(False)

    def get_loop(self) -> bool:
        return self._loop_check.isChecked()

    def set_loop(self, val: bool):
        self._loop_check.blockSignals(True)
        self._loop_check.setChecked(val)
        self._loop_check.blockSignals(False)

    def set_preview_active(self, active: bool):
        """更新预览按钮文本以反映当前预览状态。"""
        self._preview_btn.setText("\u25a0 停止预览" if active else "\U0001f441 预览")

    # ── 设备列表管理 ──

    def set_device_items(self, items: list[tuple[str, str]]):
        """设置设备下拉框选项。

        设置完成后自动选中第一项并发出 ``device_selected`` 信号，
        确保控制器的 ``_source_path`` 与 UI 保持同步。

        Args:
            items: ``[(display_name, value), ...]`` 的列表。
        """
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        for display, value in items:
            self._device_combo.addItem(display, value)
        self._device_combo.blockSignals(False)

        # 自动选中第一项并通知 Controller
        if items:
            self._device_combo.setCurrentIndex(0)
            first_value = self._device_combo.itemData(0)
            if first_value is not None:
                self.device_selected.emit(str(first_value))

    # ── 状态更新 ──

    def set_status(self, text: str, state: str = "idle"):
        """更新状态标签文本和颜色。

        Args:
            text: 状态文本（如"就绪""推流中""错误"）。
            state: 状态标识，影响文本颜色。
                可选值: ``"idle"`` / ``"streaming"`` / ``"error"`` /
                ``"stopping"``。
        """
        color_map = {
            "idle": Theme.OVERLAY0,
            "starting": Theme.OVERLAY0,
            "streaming": Theme.GREEN,
            "reconnecting": Theme.YELLOW,
            "error": Theme.RED,
            "stopping": Theme.YELLOW,
        }
        color = color_map.get(state, Theme.OVERLAY0)
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def set_buttons_streaming(self, is_streaming: bool):
        """根据推流状态切换按钮启用/禁用。

        Args:
            is_streaming: ``True`` 表示正在推流。
        """
        self._streaming = is_streaming
        self._start_btn.setEnabled(not is_streaming)
        self._stop_btn.setEnabled(is_streaming)
        self._preview_btn.setEnabled(is_streaming)
        if not is_streaming:
            self._preview_btn.setText("\U0001f441 预览")
        self._remove_btn.setEnabled(not is_streaming)
        # 推流中禁止上下移动卡片
        self._apply_move_buttons_state()

    def set_position_index(self, index: int):
        """设置该卡片在列表中的位置索引（0 起始），并刷新序号徽标。

        Args:
            index: 卡片在列表中的位置（从 0 开始）。
        """
        if index < 0:
            return
        self._position_index = index
        self._position_badge.setText(f"#{index + 1}")

    def get_position_index(self) -> int:
        """获取该卡片在列表中的位置索引（0 起始）。"""
        return self._position_index

    def set_move_buttons_enabled(self, can_up: bool, can_down: bool):
        """设置上下移动按钮的启用状态（边界处禁用）。

        Args:
            can_up:   是否允许上移。
            can_down: 是否允许下移。
        """
        self._can_move_up = bool(can_up)
        self._can_move_down = bool(can_down)
        self._apply_move_buttons_state()

    def _apply_move_buttons_state(self):
        """根据当前推流状态与边界标志，统一刷新移动按钮的启用状态。"""
        allow = not self._streaming
        self._move_up_btn.setEnabled(allow and self._can_move_up)
        self._move_down_btn.setEnabled(allow and self._can_move_down)

    def set_can_start(self, can: bool):
        """设置"开始推流"按钮是否可用。"""
        self._start_btn.setEnabled(can)

    def show_error(self, message: str):
        """弹出错误提示对话框。"""
        QMessageBox.warning(self, "提示", message)

    def browse_file(self) -> str:
        """打开文件选择对话框，返回选中的文件路径（未选择返回空字符串）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.avi *.mkv *.flv *.mov *.ts *.wmv *.mpg);;所有文件 (*)"
        )
        return path

    def set_config_locked(self, locked: bool):
        """锁定或解锁所有配置输入控件。

        推流中时锁定，防止用户意外修改参数。

        Args:
            locked: ``True`` 表示锁定（不可编辑）。
        """
        read_only = locked
        self._source_type_combo.setEnabled(not locked)
        self._source_input.setReadOnly(read_only)
        self._device_combo.setEnabled(not locked)
        self._browse_btn.setEnabled(not locked)
        self._refresh_btn.setEnabled(not locked)
        self._loop_check.setEnabled(not locked)
        self._stream_name_input.setReadOnly(read_only)
        self._settings_combo.setEnabled(not locked)
        self._codec_combo.setEnabled(not locked)
        self._width_input.setReadOnly(read_only)
        self._height_input.setReadOnly(read_only)
        self._fps_input.setReadOnly(read_only)
        self._bitrate_input.setReadOnly(read_only)
        self._source_reconnect_interval_input.setReadOnly(read_only)
        self._source_reconnect_max_attempts_input.setReadOnly(read_only)
        self._config_locked = locked
        # 推流中时禁止编辑标题
        if locked:
            self._title_label.setCursor(Qt.CursorShape.ArrowCursor)
            self._title_label.setToolTip("推流中不可修改通道名称")
        else:
            self._title_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self._title_label.setToolTip("点击修改通道名称")
