"""Grouped, independently scrolling controls for the lyric-card editor."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFontComboBox, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QStyle,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.fonts import library_font
from ui.lyrics_card_renderer import CARD_SIZES, CARD_THEMES, CARD_TEMPLATES, WATERMARK_POSITIONS, DETAILS_PLACEMENTS, theme_colors


def _field(label, control):
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    caption = QLabel(label)
    caption.setBuddy(control)
    layout.addWidget(caption)
    layout.addWidget(control)
    control.setMinimumHeight(28)
    control.setMinimumWidth(0)
    control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return container


def _row(*widgets):
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget, 1)
    return container


class _OptionalSection(QWidget):
    """Use a native checkbox header without constraining its indicator height."""

    toggled = Signal(bool)

    def __init__(self, title, checked):
        super().__init__()
        self.checkbox = QCheckBox(title, self)
        self.checkbox.setChecked(checked)
        self.checkbox.toggled.connect(self.toggled.emit)

    def isChecked(self):
        return self.checkbox.isChecked()

    def setChecked(self, checked):
        self.checkbox.setChecked(checked)


def _section(layout, title, optional=False, checked=False):
    group = _OptionalSection(title, checked) if optional else QGroupBox(title)
    group_layout = QVBoxLayout(group)
    group_layout.setContentsMargins(*(0, 0, 0, 0) if optional else (12, 12, 12, 12))
    if optional:
        group_layout.setSpacing(8)
        group_layout.addWidget(group.checkbox)
    body = QFrame() if optional else QWidget()
    content = QVBoxLayout(body)
    content.setContentsMargins(*(12, 12, 12, 12) if optional else (0, 0, 0, 0))
    content.setSpacing(10)
    group_layout.addWidget(body)
    if optional:
        body.setFrameShape(QFrame.Shape.StyledPanel)

        def reveal(enabled):
            body.setVisible(enabled)
            body.setEnabled(enabled)

        reveal(checked)
        group.toggled.connect(reveal)
    layout.addWidget(group)
    return group, content, body


class LyricsCardControlsMixin:
    def _control_page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self.options_tabs.addTab(scroll, title)
        self.control_scrolls[title] = scroll
        return layout

    def _build_card_controls(self):
        controls = QWidget()
        controls.setMinimumWidth(290)
        controls.setMaximumWidth(480)
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.preset_combo = QComboBox()
        self.preset_combo.setAccessibleName("Saved card styles")
        self.save_preset_button = QPushButton("Save…")
        self.save_preset_button.setToolTip("Save the current card style as a preset")
        self.delete_preset_button = QToolButton()
        self.delete_preset_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.delete_preset_button.setAccessibleName("Delete selected preset")
        self.delete_preset_button.setToolTip("Delete selected preset")
        presets = QHBoxLayout()
        presets.setSpacing(6)
        presets.addWidget(self.preset_combo, 1)
        presets.addWidget(self.save_preset_button)
        presets.addWidget(self.delete_preset_button)
        layout.addLayout(presets)
        self.options_tabs = QTabWidget()
        self.options_tabs.setDocumentMode(True)
        layout.addWidget(self.options_tabs, 1)
        self.control_scrolls = {}
        self._build_layout_controls()
        self._build_text_controls()
        self._build_artwork_controls()
        self._build_effect_controls()
        for combo in controls.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setMinimumWidth(0)
        self.preset_combo.setMinimumHeight(30)
        self.save_preset_button.setMinimumHeight(30)
        self.delete_preset_button.setFixedSize(30, 30)
        return controls

    def _build_layout_controls(self):
        layout = self._control_page("Layout")
        _, card, _ = _section(layout, "Card")
        self.template_combo = QComboBox()
        self.template_combo.addItems(CARD_TEMPLATES)
        self.size_combo = QComboBox()
        for label, size in CARD_SIZES.items():
            self.size_combo.addItem(label, size)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(CARD_THEMES)
        for label, control in (("Template", self.template_combo), ("Canvas size", self.size_combo), ("Theme", self.theme_combo)):
            card.addWidget(_field(label, control))
        self.custom_colors, colors, _ = _section(layout, "Custom colors", optional=True)
        self._colors = list(theme_colors(self.theme_combo.currentText(), self.template_combo.currentText()))
        self.color_buttons = []
        fields = []
        for index, label in enumerate(("Background", "Gradient end", "Lyrics", "Title", "Artist / album", "Accent")):
            button = QPushButton()
            button.clicked.connect(lambda _checked=False, i=index: self.choose_color(i))
            self.color_buttons.append(button)
            fields.append(_field(label, button))
        for index in range(0, len(fields), 2):
            colors.addWidget(_row(*fields[index:index + 2]))
        reset = QPushButton("Use theme colors")
        reset.clicked.connect(lambda: self.custom_colors.setChecked(False))
        colors.addWidget(reset)
        self.custom_colors.toggled.connect(self._sync_color_controls)
        self._sync_color_controls()
        _, spacing, _ = _section(layout, "Spacing")
        self.padding = self._spin("padding", 84, " px")
        self.line_spacing = self._spin("line_spacing", 100, "%")
        self.vertical_position = self._spin("vertical_position", 50, "%")
        self.vertical_position.setToolTip("Top: 0% · Middle: 50% · Bottom: 100%")
        spacing.addWidget(_row(_field("Padding", self.padding), _field("Line spacing", self.line_spacing)))
        spacing.addWidget(_field("Vertical lyric position", self.vertical_position))

    def _build_text_controls(self):
        layout = self._control_page("Text")
        _, typography, _ = _section(layout, "Typography")
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(library_font(QApplication.font()))
        typography.addWidget(_field("Font", self.font_combo))
        self.font_size = self._spin("font_size", 56, " px")
        self.font_size.setToolTip("Maximum lyric font size; long excerpts shrink to fit.")
        self.alignment_combo = QComboBox()
        for label, value in (("Left", "left"), ("Center", "center"), ("Right", "right")):
            self.alignment_combo.addItem(label, value)
        typography.addWidget(_row(_field("Size", self.font_size), _field("Alignment", self.alignment_combo)))
        emphasis = QWidget()
        emphasis_layout = QHBoxLayout(emphasis)
        emphasis_layout.setContentsMargins(0, 0, 0, 0)
        emphasis_layout.setSpacing(6)
        self.text_style_buttons = {}
        for key, label, description in (("bold", "B", "Bold"), ("italic", "I", "Italic"),
                                        ("underline", "U", "Underline"), ("strikeout", "S", "Strikethrough")):
            button = QToolButton()
            button.setText(label)
            button.setFixedSize(32, 30)
            button.setCheckable(True)
            button.setToolTip(description)
            button.setAccessibleName(description)
            font = QFont(button.font())
            {"bold": font.setBold, "italic": font.setItalic, "underline": font.setUnderline,
             "strikeout": font.setStrikeOut}[key](True)
            button.setFont(font)
            button.toggled.connect(self.schedule_refresh)
            emphasis_layout.addWidget(button)
            self.text_style_buttons[key] = button
        emphasis_layout.addStretch()
        typography.addWidget(emphasis)
        _, details, _ = _section(layout, "Song details")
        metadata = {}
        for entry in self.editor._document_metadata:
            key, separator, value = entry[1:-1].partition(":")
            if separator:
                metadata[key.casefold()] = value.strip()
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(160)
        self.title_edit.setText(metadata.get("ti") or self.editor.lrc_path.stem)
        self.credit_edit = QLineEdit()
        self.credit_edit.setMaxLength(200)
        self.credit_edit.setText(metadata.get("ar", ""))
        self.album_edit = QLineEdit()
        self.album_edit.setMaxLength(200)
        self.album_edit.setText(metadata.get("al", ""))
        for label, field, placeholder in (("Title", self.title_edit, "Optional song title"),
                                           ("Artist / credit", self.credit_edit, "Optional artist or credit"),
                                           ("Album", self.album_edit, "Optional album name")):
            field.setPlaceholderText(placeholder)
            field.setClearButtonEnabled(True)
            field.setCursorPosition(0)
            field.setToolTip(field.text())
            field.textChanged.connect(field.setToolTip)
            details.addWidget(_field(label, field))
        self.details_placement = QComboBox()
        for label, value in DETAILS_PLACEMENTS.items():
            self.details_placement.addItem(label, value)
        self.details_placement.setToolTip("Beside cover groups the artwork and song details. Without available artwork, details appear below the title.")
        self.details_placement.currentIndexChanged.connect(self.schedule_refresh)
        details.addWidget(_field("Details placement", self.details_placement))
        _, lyrics, _ = _section(layout, "Lyrics")
        self.preserve_lrcx_formatting = QCheckBox("Preserve LRCX formatting")
        self.preserve_lrcx_formatting.setChecked(True)
        self.preserve_lrcx_formatting.setToolTip("Keep inline emphasis, blockquotes, and smaller translation rows. Karaoke timing and highlighting are excluded.")
        self.preserve_lrcx_formatting.toggled.connect(self.schedule_refresh)
        lyrics.addWidget(self.preserve_lrcx_formatting)
        self.include_furigana = QCheckBox("Include furigana")
        self.include_furigana.setChecked(True)
        self.include_furigana.setToolTip("Use saved readings and readings already available in the editor.")
        self.furigana_scale = self._spin("furigana_scale", 50, "%")
        self.furigana_scale.setMaximumWidth(90)
        self.furigana_scale.setAccessibleName("Furigana size")
        self.furigana_scale.setToolTip("Reading font size relative to the lyric font.")
        furigana = QHBoxLayout()
        furigana.addWidget(self.include_furigana)
        furigana.addStretch()
        furigana.addWidget(self.furigana_scale)
        lyrics.addLayout(furigana)
        self.include_furigana.toggled.connect(self.furigana_scale.setVisible)
        self.include_furigana.toggled.connect(self.furigana_scale.setEnabled)
        self.include_furigana.toggled.connect(self.schedule_refresh)

    def _build_artwork_controls(self):
        layout = self._control_page("Artwork")
        self.show_album_cover, cover, self.cover_controls = _section(layout, "Album cover", optional=True)
        self.cover_size = self._spin("cover_size", 160, " px")
        self.cover_size.setEnabled(False)
        self.retry_cover_button = QPushButton("Refresh cover")
        self.retry_cover_button.clicked.connect(self.album_art.retry)
        self.retry_cover_button.setEnabled(False)
        cover.addWidget(_field("Cover size", self.cover_size))
        self.album_cover_status = QLabel(self.album_art.message)
        self.album_cover_status.setWordWrap(True)
        self.album_cover_status.setMinimumWidth(0)
        self.album_cover_status.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        cover.addWidget(self.album_cover_status)
        cover.addWidget(self.retry_cover_button)
        _, background, _ = _section(layout, "Background")
        self.background_mode = QComboBox()
        self.background_mode.addItems(["Gradient", "Solid", "Image"])
        background.addWidget(_field("Fill", self.background_mode))
        self.gradient_angle = self._spin("gradient_angle", 45, "°")
        self.gradient_controls = _field("Gradient angle", self.gradient_angle)
        background.addWidget(self.gradient_controls)
        self.image_controls = QWidget()
        image_layout = QVBoxLayout(self.image_controls)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(10)
        self.image_path_edit = QLineEdit()
        self.image_path_edit.setReadOnly(True)
        self.image_path_edit.setPlaceholderText("Choose a background image")
        image_layout.addWidget(self.image_path_edit)
        image_actions = QHBoxLayout()
        choose_image = QPushButton("Choose image…")
        choose_image.clicked.connect(self.choose_image)
        self.clear_image_button = QPushButton("Clear")
        self.clear_image_button.clicked.connect(self.clear_image)
        image_actions.addWidget(choose_image, 1)
        image_actions.addWidget(self.clear_image_button)
        image_layout.addLayout(image_actions)
        self.image_blur = self._spin("image_blur", 0, " px")
        self.image_dim = self._spin("image_dim", 35, "%")
        self.image_zoom = self._spin("image_zoom", 100, "%")
        self.crop_x = self._spin("crop_x", 50, "%")
        self.crop_y = self._spin("crop_y", 50, "%")
        image_layout.addWidget(_row(_field("Blur", self.image_blur), _field("Dim", self.image_dim)))
        image_layout.addWidget(_field("Zoom", self.image_zoom))
        image_layout.addWidget(_row(_field("Crop X", self.crop_x), _field("Crop Y", self.crop_y)))
        hint = QLabel("Images fill the card without stretching. Crop from left/top (0%) to right/bottom (100%).")
        hint.setWordWrap(True)
        image_layout.addWidget(hint)
        background.addWidget(self.image_controls)
        self.background_mode.currentIndexChanged.connect(self._sync_background_controls)
        self._sync_background_controls()

    def _build_effect_controls(self):
        layout = self._control_page("Effects")
        hint = QLabel("Stroke and shadow apply to lyrics, title, artist, and album. Effects scale with the smaller song details.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._effect_colors = {"stroke_color": "#000000", "shadow_color": "#000000"}
        self.effect_color_buttons = {}
        self.stroke_enabled, stroke, _ = _section(layout, "Stroke", optional=True)
        self.shadow_enabled, shadow, _ = _section(layout, "Shadow", optional=True)
        for key in self._effect_colors:
            button = QPushButton()
            button.setAccessibleName(key.replace("_", " "))
            button.clicked.connect(lambda _checked=False, name=key: self.choose_effect_color(name))
            self.effect_color_buttons[key] = button
        self.stroke_width = self._spin("stroke_width", 2, " px")
        stroke.addWidget(_row(_field("Color", self.effect_color_buttons["stroke_color"]), _field("Width", self.stroke_width)))
        self.shadow_opacity = self._spin("shadow_opacity", 60, "%")
        self.shadow_blur = self._spin("shadow_blur", 8, " px")
        self.shadow_offset_x = self._spin("shadow_offset_x", 4, " px")
        self.shadow_offset_y = self._spin("shadow_offset_y", 4, " px")
        shadow.addWidget(_field("Color", self.effect_color_buttons["shadow_color"]))
        shadow.addWidget(_row(_field("Opacity", self.shadow_opacity), _field("Blur", self.shadow_blur)))
        shadow.addWidget(_row(_field("Offset X", self.shadow_offset_x), _field("Offset Y", self.shadow_offset_y)))
        self.watermark_enabled, watermark, _ = _section(layout, "LRCPlus watermark", optional=True)
        self.watermark_width = self._spin("watermark_width", 180, " px")
        self.watermark_opacity = self._spin("watermark_opacity", 45, "%")
        self.watermark_position = QComboBox()
        for value in WATERMARK_POSITIONS:
            self.watermark_position.addItem(value.replace("_", " ").capitalize(), value)
        watermark.addWidget(_row(_field("Width", self.watermark_width), _field("Opacity", self.watermark_opacity)))
        watermark.addWidget(_field("Corner", self.watermark_position))
        for group in (self.stroke_enabled, self.shadow_enabled, self.watermark_enabled):
            group.toggled.connect(self.schedule_refresh)
        self.watermark_position.currentIndexChanged.connect(self.schedule_refresh)
        self._sync_effect_colors()
