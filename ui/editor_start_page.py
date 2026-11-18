"""A lightweight, themed welcome screen for the standalone lyrics editor."""

import math

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.settings import get_appearance_settings
from core.theme import build_theme_palette
from core.fonts import library_font


class _LyricIllustration(QWidget):
    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.setFixedHeight(146)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        accent = QColor(self.colors["accent"])
        painter.setPen(Qt.PenStyle.NoPen)
        # A quiet waveform frames the lyric sheet without loading audio.
        for index in range(41):
            height = 8 + 34 * abs(math.sin(index * 0.73)) * (1 - abs(index - 20) / 26)
            tint = QColor(accent)
            tint.setAlpha(35 if abs(index - 20) < 9 else 85)
            painter.setBrush(tint)
            painter.drawRoundedRect(QRectF((index - 20) * 9 - 2, -height / 2, 4, height), 2, 2)
        painter.rotate(-6)
        shadow = QColor(self.colors["text"])
        shadow.setAlpha(10)
        painter.setBrush(shadow)
        painter.drawRoundedRect(QRectF(-66, -59, 140, 124), 14, 14)
        painter.setBrush(QColor(self.colors["surface"]))
        painter.setPen(QPen(QColor(self.colors["border"]), 1))
        painter.drawRoundedRect(QRectF(-70, -64, 140, 124), 14, 14)
        painter.setPen(accent)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(-51, -47, 100, 16), ".LRCX")
        painter.setPen(Qt.PenStyle.NoPen)
        for index, width in enumerate((74, 94, 62)):
            y = -14 + index * 20
            painter.setBrush(accent if index == 0 else QColor(self.colors["raised"]))
            painter.drawRoundedRect(QRectF(-51, y, width, 5), 2.5, 2.5)
        painter.end()


class _DocumentAction(QPushButton):
    def __init__(self, title, description, symbol, colors, *, primary=False, parent=None):
        super().__init__(title, parent)
        self.colors = colors
        self.primary = primary
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)
        self.setAutoDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(220)
        self.setMinimumHeight(136)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 17, 22, 19)
        layout.setSpacing(8)
        top = QHBoxLayout()
        icon = QLabel(symbol)
        icon.setStyleSheet(f"color: {colors['accent']}; font-size: 25px; font-weight: 600;")
        top.addWidget(icon)
        top.addStretch()
        arrow = QLabel("→")
        arrow.setStyleSheet(f"color: {colors['muted']}; font-size: 18px;")
        top.addWidget(arrow)
        layout.addLayout(top)
        heading = QLabel(title)
        heading.setStyleSheet(f"color: {colors['text']}; font-size: 16px; font-weight: 600;")
        layout.addWidget(heading)
        caption = QLabel(description)
        caption.setWordWrap(True)
        caption.setStyleSheet(f"color: {colors['muted']}; font-size: 12px;")
        layout.addWidget(caption)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()
        fill = "accent_soft" if self.primary else "surface"
        if self.isDown():
            fill = "pressed"
        elif hovered:
            fill = "accent_soft"
        border = "accent" if hovered or self.hasFocus() or self.primary else "border"
        painter.setBrush(QColor(self.colors[fill]))
        painter.setPen(QPen(QColor(self.colors[border]), 2 if self.hasFocus() else 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 14, 14)
        if self.hasFocus():
            painter.setPen(QPen(QColor(self.colors["accent"]), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(6, 6, -6, -6), 10, 10)
        painter.end()


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
        self.setStyleSheet("QWidget#editorStartPage QLabel { background: transparent; }")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(0)
        header = QHBoxLayout()
        brand = QLabel("LRCPlus  /  LYRICS EDITOR")
        brand.setStyleSheet(f"color: {colors['muted']}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        header.addWidget(brand)
        header.addStretch()
        version = QLabel("4.0")
        version.setStyleSheet(f"color: {colors['muted']}; font-size: 11px;")
        header.addWidget(version)
        root.addLayout(header)
        root.addStretch(1)

        content = QWidget()
        content.setMaximumWidth(720)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 12, 0, 12)
        body.setSpacing(0)
        body.addWidget(_LyricIllustration(colors))
        heading = QLabel("No songs found")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet(f"color: {colors['text']}; font-size: 32px; font-weight: 700;")
        body.addWidget(heading)
        body.addSpacing(12)
        subtitle = QLabel("Every song starts with a line.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {colors['muted']}; font-size: 15px;")
        body.addWidget(subtitle)
        body.addSpacing(30)
        actions = QHBoxLayout()
        actions.setSpacing(16)
        self.create_button = _DocumentAction(
            "Create New LRCX", "Start with a blank lyric document.", "＋", colors,
            primary=True,
        )
        self.create_button.clicked.connect(editor.create_new_lrcx)
        actions.addWidget(self.create_button, 1)
        self.open_button = _DocumentAction(
            "Open LRCX", "Pick up where you left off.", "≡", colors,
        )
        self.open_button.clicked.connect(editor.open_lrcx_document)
        actions.addWidget(self.open_button, 1)
        body.addLayout(actions)
        body.addSpacing(24)
        note = QLabel("No audio needed. Write now, add your song later.")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setStyleSheet(f"color: {colors['muted']}; font-size: 12px;")
        body.addWidget(note)
        center = QHBoxLayout()
        center.addStretch()
        center.addWidget(content, 1)
        center.addStretch()
        root.addLayout(center)
        root.addStretch(1)
        footer = QLabel("LYRICS  ·  TIMING  ·  READINGS")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(f"color: {colors['muted']}; font-size: 10px; letter-spacing: 1px;")
        root.addWidget(footer)

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(self.colors["window"]))
        gradient.setColorAt(1, QColor(self.colors["surface_alt"]))
        painter.fillRect(self.rect(), gradient)
        painter.end()
