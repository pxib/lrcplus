from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPolygon
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QSpinBox, QToolButton
from core.theme import build_theme_palette

class _SpinStepButton(QToolButton):
    def __init__(self, parent, direction):
        super().__init__(parent)
        self.direction = direction
        self.setObjectName(
            "spinStepUp" if direction > 0 else "spinStepDown"
        )
        self.setAutoRepeat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        super().paintEvent(event)
        center_x = self.width() // 2
        center_y = self.height() // 2
        if self.direction > 0:
            points = QPolygon((
                QPoint(center_x - 4, center_y + 2),
                QPoint(center_x + 4, center_y + 2),
                QPoint(center_x, center_y - 3),
            ))
        else:
            points = QPolygon((
                QPoint(center_x - 4, center_y - 2),
                QPoint(center_x + 4, center_y - 2),
                QPoint(center_x, center_y + 3),
            ))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#53665e"))
        painter.drawPolygon(points)


class ThemedSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._step_up = _SpinStepButton(self, 1)
        self._step_down = _SpinStepButton(self, -1)
        self._step_up.clicked.connect(self.stepUp)
        self._step_down.clicked.connect(self.stepDown)
        self.lineEdit().setTextMargins(0, 0, 24, 0)
        self._position_step_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_step_buttons()

    def _position_step_buttons(self):
        button_width = 22
        usable_height = max(0, self.height() - 2)
        upper_height = usable_height // 2
        x = max(1, self.width() - button_width - 1)
        self._step_up.setGeometry(x, 1, button_width, upper_height)
        self._step_down.setGeometry(
            x,
            1 + upper_height,
            button_width,
            usable_height - upper_height,
        )


_NativeComboBox = QComboBox


class _ComboDropButton(QToolButton):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("comboDropIndicator")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda _checked=False: parent.showPopup())

    def paintEvent(self, event):
        super().paintEvent(event)
        center_x = self.width() // 2
        center_y = self.height() // 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#53665e"))
        painter.drawLine(
            QPoint(center_x - 3, center_y - 1),
            QPoint(center_x, center_y + 2),
        )
        painter.drawLine(
            QPoint(center_x, center_y + 2),
            QPoint(center_x + 3, center_y - 1),
        )


class ThemedComboBox(_NativeComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_indicator = _ComboDropButton(self)
        self._position_drop_indicator()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_drop_indicator()

    def _position_drop_indicator(self):
        button_width = 24
        self._drop_indicator.setGeometry(
            max(1, self.width() - button_width - 1),
            1,
            button_width,
            max(0, self.height() - 2),
        )


# All settings combos use the painted dropdown indicator.
QComboBox = ThemedComboBox


SETTINGS_DIALOG_STYLE = """
    QDialog {
        background: @WINDOW@;
        color: @TEXT@;
    }
    QDialog QWidget {
        color: @TEXT@;
        background-color: @WINDOW@;
    }
    QDialog QLabel,
    QDialog QCheckBox {
        background-color: transparent;
    }
    QDialog QTabWidget::pane {
        background: @SURFACE@;
        border: 1px solid @BORDER@;
        border-radius: 5px;
    }
    QDialog QTabBar::tab {
        color: @MUTED@;
        background: @RAISED@;
        border: 1px solid @BORDER@;
        border-bottom: none;
        padding: 7px 11px;
    }
    QDialog QTabBar::tab:selected {
        color: @ACCENT_TEXT@;
        background: @ACCENT_SOFT@;
    }
    QDialog QPushButton#themeModeButton {
        color: @MUTED@;
        background: @RAISED@;
        border: 1px solid @BORDER@;
        border-radius: 5px;
        padding: 6px 14px;
        min-height: 20px;
    }
    QDialog QPushButton#themeModeButton:checked {
        color: @ACCENT_TEXT@;
        background: @ACCENT_SOFT@;
        border-color: @ACCENT_BORDER@;
    }
    QDialog QLineEdit,
    QDialog QSpinBox,
    QDialog QTextEdit,
    QDialog QListWidget {
        color: @TEXT@;
        background: @SURFACE@;
        border: 1px solid @FIELD_BORDER@;
        border-radius: 4px;
        padding: 4px 6px;
        selection-background-color: @ACCENT_SOFT@;
        selection-color: @TEXT@;
    }
    QDialog QComboBox {
        color: @TEXT@;
        background: @SURFACE@;
        border: 1px solid @FIELD_BORDER@;
        border-radius: 4px;
        padding: 4px 28px 4px 6px;
        selection-background-color: @ACCENT_SOFT@;
        selection-color: @TEXT@;
    }
    QDialog QComboBox:hover {
        border-color: @ACCENT_BORDER@;
    }
    QDialog QComboBox::drop-down {
        width: 24px;
        background: @RAISED@;
        border-left: 1px solid @BORDER@;
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
    }
    QDialog QComboBox::drop-down:hover {
        background: @RAISED_HOVER@;
    }
    QDialog QComboBox::down-arrow {
        image: none;
        width: 0;
        height: 0;
    }
    QDialog QToolButton#comboDropIndicator {
        background: @RAISED@;
        border: none;
        border-left: 1px solid @BORDER@;
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
        padding: 0;
    }
    QDialog QToolButton#comboDropIndicator:hover {
        background: @RAISED_HOVER@;
    }
    QDialog QComboBox QAbstractItemView {
        color: @TEXT@;
        background: @SURFACE@;
        border: 1px solid @FIELD_BORDER@;
        selection-background-color: @ACCENT_SOFT@;
        selection-color: @TEXT@;
        outline: none;
    }
    QDialog QToolButton#spinStepUp,
    QDialog QToolButton#spinStepDown {
        background: @RAISED@;
        border: none;
        border-left: 1px solid @BORDER@;
        padding: 0;
    }
    QDialog QToolButton#spinStepUp {
        border-bottom: 1px solid @BORDER@;
        border-top-right-radius: 4px;
    }
    QDialog QToolButton#spinStepDown {
        border-bottom-right-radius: 4px;
    }
    QDialog QToolButton#spinStepUp:hover,
    QDialog QToolButton#spinStepDown:hover {
        background: @RAISED_HOVER@;
    }
    QDialog QPushButton {
        color: @TEXT@;
        background: @SURFACE@;
        border: 1px solid @FIELD_BORDER@;
        border-radius: 5px;
        padding: 6px 11px;
        min-height: 20px;
    }
    QDialog QPushButton:hover {
        background: @RAISED_HOVER@;
        border-color: @ACCENT_BORDER@;
    }
    QDialog QPushButton:pressed {
        background: @PRESSED@;
    }
    QDialog QGroupBox {
        color: @TEXT@;
        background: @SURFACE_ALT@;
        border: 1px solid @BORDER@;
        border-radius: 5px;
        margin-top: 10px;
        padding: 8px;
    }
    QDialog QGroupBox::title {
        subcontrol-origin: margin;
        left: 8px;
        padding: 0 4px;
    }
    QDialog QSlider::groove:horizontal {
        height: 4px;
        background: @BORDER@;
        border-radius: 2px;
    }
    QDialog QSlider::sub-page:horizontal {
        background: @ACCENT@;
        border-radius: 2px;
    }
    QDialog QSlider::handle:horizontal {
        width: 12px;
        margin: -5px 0;
        background: @SURFACE@;
        border: 2px solid @ACCENT@;
        border-radius: 7px;
    }
    QDialog QScrollBar:vertical {
        width: 10px;
        margin: 3px 2px;
        background: transparent;
        border: none;
    }
    QDialog QScrollBar:horizontal {
        height: 10px;
        margin: 2px 3px;
        background: transparent;
        border: none;
    }
    QDialog QScrollBar::handle:vertical,
    QDialog QScrollBar::handle:horizontal {
        background: @SCROLL_HANDLE@;
        border-radius: 5px;
        min-height: 28px;
        min-width: 28px;
    }
    QDialog QScrollBar::handle:vertical:hover,
    QDialog QScrollBar::handle:horizontal:hover {
        background: @ACCENT_BORDER@;
    }
    QDialog QScrollBar::add-line:vertical,
    QDialog QScrollBar::sub-line:vertical,
    QDialog QScrollBar::add-line:horizontal,
    QDialog QScrollBar::sub-line:horizontal {
        width: 0;
        height: 0;
        background: transparent;
        border: none;
    }
    QDialog QScrollBar::add-page:vertical,
    QDialog QScrollBar::sub-page:vertical,
    QDialog QScrollBar::add-page:horizontal,
    QDialog QScrollBar::sub-page:horizontal {
        background: transparent;
    }
"""


def _apply_settings_dialog_style(dialog, theme_mode, theme_color):
    palette = build_theme_palette(theme_mode, theme_color)
    style = SETTINGS_DIALOG_STYLE
    for token, key in (
        ("@WINDOW@", "window"),
        ("@TEXT@", "text"),
        ("@SURFACE@", "surface"),
        ("@SURFACE_ALT@", "surface_alt"),
        ("@RAISED@", "raised"),
        ("@RAISED_HOVER@", "raised_hover"),
        ("@PRESSED@", "pressed"),
        ("@MUTED@", "muted"),
        ("@BORDER@", "border"),
        ("@FIELD_BORDER@", "field_border"),
        ("@ACCENT@", "accent"),
        ("@ACCENT_HOVER@", "accent_hover"),
        ("@ACCENT_BORDER@", "accent_border"),
        ("@ACCENT_SOFT@", "accent_soft"),
        ("@ACCENT_TEXT@", "accent_text"),
        ("@SCROLL_HANDLE@", "scroll_handle"),
    ):
        style = style.replace(token, palette[key])
    dialog.setStyleSheet(style)

