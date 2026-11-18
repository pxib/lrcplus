"""A compact, themed start screen for the standalone lyrics editor."""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.fonts import library_font
from core.settings import get_appearance_settings
from core.theme import build_theme_palette


class _LyricIllustration(QWidget):
    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.setFixedHeight(100)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.setBrush(QColor(self.colors["surface"]))
        painter.setPen(QPen(QColor(self.colors["border"]), 1))
        painter.drawRoundedRect(QRectF(-29, -37, 58, 74), 8, 8)
        painter.setPen(QColor(self.colors["accent"]))
        font = QFont(self.font())
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(-19, -24, 40, 14), ".LRCX")
        painter.setPen(Qt.PenStyle.NoPen)
        for index, width in enumerate((30, 38, 24)):
            color = "accent" if index == 0 else "raised"
            painter.setBrush(QColor(self.colors[color]))
            painter.drawRoundedRect(QRectF(-19, -1 + index * 10, width, 3), 1.5, 1.5)


class EditorStartPage(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.setFont(library_font(self.font()))
        appearance = getattr(editor.player, "appearance_settings", None)
        if appearance is None:
            appearance = get_appearance_settings()
        self.colors = build_theme_palette(
            appearance.get("theme_mode", "light"),
            appearance.get("theme_color", "#3a9879"),
            appearance.get("theme_surface", "classic"),
            appearance.get("theme_overrides", {}),
        )
        colors = self.colors
        self.setObjectName("editorStartPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(f"""
            QWidget#editorStartPage {{ background: {colors['window']}; }}
            QWidget#editorStartPage QLabel {{ background: transparent; }}
            QWidget#editorStartPage QPushButton {{
                color: {colors['text']}; background: {colors['surface']};
                border: 2px solid {colors['border']}; border-radius: 8px;
                padding: 10px 18px; font-size: 13px; font-weight: 500;
            }}
            QWidget#editorStartPage QPushButton:hover {{
                background: {colors['raised_hover']}; border-color: {colors['accent']};
            }}
            QWidget#editorStartPage QPushButton:pressed {{ background: {colors['pressed']}; }}
            QWidget#editorStartPage QPushButton:focus {{ border-color: {colors['accent']}; }}
            QWidget#editorStartPage QPushButton#newLrcx {{
                color: {colors['accent_text']}; background: {colors['accent']};
                border-color: {colors['accent']};
            }}
            QWidget#editorStartPage QPushButton#newLrcx:hover {{
                background: {colors['accent_hover']}; border-color: {colors['accent_hover']};
            }}
            QWidget#editorStartPage QPushButton#newLrcx:pressed {{
                background: {colors['accent_hover']};
            }}
            QWidget#editorStartPage QPushButton#newLrcx:focus {{
                border-color: {colors['text']};
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch()
        content = QWidget()
        content.setMaximumWidth(480)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(_LyricIllustration(colors))
        heading = QLabel("No songs found")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet(f"color: {colors['text']}; font-size: 26px; font-weight: 600;")
        body.addWidget(heading)
        body.addSpacing(10)
        subtitle = QLabel("Create or open an LRCX. No audio required.")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {colors['muted']}; font-size: 13px;")
        body.addWidget(subtitle)
        body.addSpacing(24)
        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch()
        self.create_button = QPushButton("Create New LRCX")
        self.create_button.setObjectName("newLrcx")
        self.create_button.clicked.connect(editor.create_new_lrcx)
        actions.addWidget(self.create_button)
        self.open_button = QPushButton("Open LRCX")
        self.open_button.clicked.connect(editor.open_lrcx_document)
        actions.addWidget(self.open_button)
        actions.addStretch()
        for button in (self.create_button, self.open_button):
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        body.addLayout(actions)
        center = QHBoxLayout()
        center.addStretch()
        center.addWidget(content, 1)
        center.addStretch()
        root.addLayout(center)
        root.addStretch()
