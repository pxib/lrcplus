"""Deterministic, full-resolution lyric cards and their Share-tab controls."""

from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QSaveFile, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QImage, QImageReader, QPainter, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QInputDialog, QSizePolicy, QSpinBox, QSplitter,
    QVBoxLayout, QWidget,
)

from core.fonts import library_font
from ui.lyrics_card_controls import LyricsCardControlsMixin


# Re-export the renderer API used by the editor and share integrations.
from ui.lyrics_card_renderer import (
    CARD_SIZES, CARD_THEMES, CARD_TEMPLATES, WATERMARK_POSITIONS, CardStyle, render_lyric_card, theme_colors,
)
from ui.lyrics_card_presets import (
    STYLE_RANGES, delete_card_preset, load_card_presets, save_card_preset, style_from_preset,
)
from ui.lyrics_card_artwork import CardAlbumArt
from ui.lyrics_card_text import snapshot_card_lyrics


def _color_swatch(button, value):
    color = QColor(value)
    ink = "#111111" if color.lightness() > 140 else "#ffffff"
    button.setText(color.name())
    button.setStyleSheet(f"QPushButton {{ background-color: {color.name()}; color: {ink}; padding: 4px; }}")


def save_card_png(image, path):
    """Use an atomic replacement so a failed encode/write preserves the target."""
    data = QBuffer()
    data.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(data, "PNG"):
        raise OSError("Could not encode the image as PNG.")
    output = QSaveFile(str(path))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    payload = data.data()
    if output.write(payload) != payload.size():
        output.cancelWriting()
        raise OSError(output.errorString())
    if not output.commit():
        raise OSError(output.errorString())


class _CardPreview(QWidget):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to open the image viewer")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Card preview. Open image viewer")

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())
                and not self.image.isNull()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and not self.image.isNull():
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = self.rect()
            target.setSize(size)
            target.moveCenter(self.rect().center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self.image)
        painter.end()


class LyricsShareCardWidget(LyricsCardControlsMixin, QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self._image = QImage()
        self._render_key = None
        self._updating_style = False
        self.album_art = CardAlbumArt(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self.refresh)
        layout = QVBoxLayout(self)
        hint = QLabel("Check the lyric lines you want to share. Use Select All to share the full song.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        controls = self._build_card_controls()
        self._sync_ruby_positions()
        self._sync_ruby_color()
        preview_panel = QFrame()
        preview_panel.setFrameShape(QFrame.Shape.StyledPanel)
        preview_panel.setMinimumWidth(240)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(14, 14, 14, 14)
        preview_layout.setSpacing(10)
        preview_header = QHBoxLayout()
        preview_heading = QLabel("Preview")
        heading_font = QFont(preview_heading.font())
        heading_font.setBold(True)
        preview_heading.setFont(heading_font)
        self.preview_details = QLabel()
        preview_header.addWidget(preview_heading)
        preview_header.addStretch()
        preview_header.addWidget(self.preview_details)
        preview_layout.addLayout(preview_header)
        self.preview = _CardPreview()
        self.preview.clicked.connect(self.open_image_viewer)
        preview_layout.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setWordWrap(True)
        preview_layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.copy_button = QPushButton("Copy Image")
        self.copy_button.clicked.connect(self.copy_image)
        self.save_button = QPushButton("Save PNG…")
        self.save_button.clicked.connect(self.export_image)
        actions.addStretch()
        for button in (self.copy_button, self.save_button):
            button.setMinimumHeight(32)
            actions.addWidget(button)
        preview_layout.addLayout(actions)
        self.controls_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.controls_splitter.setChildrenCollapsible(False)
        self.controls_splitter.addWidget(controls)
        self.controls_splitter.addWidget(preview_panel)
        self.controls_splitter.setStretchFactor(0, 0)
        self.controls_splitter.setStretchFactor(1, 1)
        self.controls_splitter.setSizes([360, 600])
        layout.addWidget(self.controls_splitter, 1)
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        for combo in (self.template_combo, self.size_combo, self.theme_combo, self.alignment_combo):
            combo.currentIndexChanged.connect(self.schedule_refresh)
        self.font_combo.currentFontChanged.connect(self.schedule_refresh)
        self.font_size.valueChanged.connect(self.schedule_refresh)
        self.title_edit.textChanged.connect(self.schedule_refresh)
        self.credit_edit.textChanged.connect(self.schedule_refresh)
        self.album_edit.textChanged.connect(self.schedule_refresh)
        self.theme_combo.currentIndexChanged.connect(self._sync_color_controls)
        self.template_combo.currentIndexChanged.connect(self._template_changed)
        self.preset_combo.currentIndexChanged.connect(self.apply_preset)
        self.save_preset_button.clicked.connect(self.save_preset)
        self.delete_preset_button.clicked.connect(self.delete_preset)
        self._reload_presets()
        self.show_album_cover.toggled.connect(self._album_cover_toggled)
        self._sync_details_placement()
        self.album_art.changed.connect(self._album_cover_changed)

    def _album_cover_toggled(self, enabled):
        self.cover_size.setEnabled(enabled)
        self._sync_details_placement()
        self._configure_album_art()
        self.schedule_refresh()

    def _sync_details_placement(self):
        item = self.details_placement.model().item(self.details_placement.findData("beside_cover"))
        item.setEnabled(self.show_album_cover.isChecked())

    def _template_changed(self, *_args):
        if not self._updating_style and self.template_combo.currentText() in ("Polaroid", "Editorial"):
            self.theme_combo.setCurrentText("Paper")
        self._sync_color_controls()

    def _album_song_path(self):
        path = self.editor.attached_song_path or self.editor._editing_song_path
        if path:
            path = Path(path)
            return path if path.is_absolute() else self.editor.lrc_path.parent / path
        for suffix in (".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"):
            candidate = self.editor.lrc_path.with_suffix(suffix)
            if candidate.is_file():
                return candidate
        return None

    def _configure_album_art(self):
        manager = getattr(self.editor.player, "plugin_manager", None)
        get_service = getattr(manager, "get_service", None)
        provider = get_service("discord_rpc.album_artwork") if callable(get_service) else None
        self.album_art.configure(self._album_song_path(), provider, self.show_album_cover.isChecked())

    def _album_cover_changed(self):
        self.album_cover_status.setText(self.album_art.message)
        self.retry_cover_button.setEnabled(self.show_album_cover.isChecked() and not self.album_art.loading)
        self.schedule_refresh()

    def shutdown(self):
        self._refresh_timer.stop()
        self.album_art.shutdown()
        self._image = QImage()
        self.preview.image = self._image
        self._render_key = None

    def _spin(self, name, value, suffix=""):
        widget = QSpinBox()
        widget.setRange(*STYLE_RANGES[name])
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.valueChanged.connect(self.schedule_refresh)
        return widget

    def _sync_effect_colors(self):
        for key, button in self.effect_color_buttons.items():
            _color_swatch(button, self._effect_colors[key])

    def choose_effect_color(self, key):
        color = QColorDialog.getColor(QColor(self._effect_colors[key]), self, "Choose text effect color")
        if color.isValid():
            self._effect_colors[key] = color.name()
            self._sync_effect_colors()
            self.schedule_refresh()

    def _sync_color_controls(self, *_args):
        if not self.custom_colors.isChecked():
            self._colors = list(theme_colors(self.theme_combo.currentText(), self.template_combo.currentText()))
        for button, value in zip(self.color_buttons, self._colors):
            _color_swatch(button, value)
            button.setEnabled(self.custom_colors.isChecked())
        if hasattr(self, "ruby_color_button"):
            self._sync_ruby_color()
        self.schedule_refresh()

    def _sync_ruby_positions(self, *_args):
        previous = self.ruby_position.currentData()
        vertical = self.writing_mode.currentData() in ("vertical_rl", "vertical_lr")
        options = (("Right", "right"), ("Left", "left")) if vertical else (("Above", "above"), ("Below", "below"))
        blocker = QSignalBlocker(self.ruby_position)
        self.ruby_position.clear()
        self.ruby_position.addItem("Auto", "auto")
        for label, value in options:
            self.ruby_position.addItem(label, value)
        self.ruby_position.setCurrentIndex(max(0, self.ruby_position.findData(previous)))
        del blocker
        self.schedule_refresh()

    def _sync_ruby_color(self, *_args):
        custom = self.ruby_custom_color.isChecked()
        if not custom:
            self._ruby_color = self._colors[2]
        _color_swatch(self.ruby_color_button, self._ruby_color)
        self.ruby_color_button.setEnabled(custom)
        self.schedule_refresh()

    def choose_ruby_color(self):
        color = QColorDialog.getColor(QColor(self._ruby_color), self, "Choose Furigana Color")
        if color.isValid():
            self._ruby_color = color.name()
            self._sync_ruby_color()

    def choose_color(self, index):
        color = QColorDialog.getColor(QColor(self._colors[index]), self, "Choose card color")
        if color.isValid():
            self._colors[index] = color.name()
            self._sync_color_controls()

    def _sync_background_controls(self, *_args):
        mode = self.background_mode.currentText()
        self.gradient_controls.setVisible(mode == "Gradient")
        self.image_controls.setVisible(mode == "Image")
        self.gradient_angle.setEnabled(mode == "Gradient")
        for control in (self.image_blur, self.image_dim, self.image_zoom, self.crop_x, self.crop_y):
            control.setEnabled(mode == "Image")
        self.clear_image_button.setEnabled(bool(self.image_path_edit.text()))
        self.schedule_refresh()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Card Background", self.image_path_edit.text() or str(self.editor.lrc_path.parent),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)",
        )
        if not path:
            return
        reader = QImageReader(path)
        if not reader.canRead():
            QMessageBox.warning(self, "Card Background", "Could not read this image. Choose another file.")
            return
        self.image_path_edit.setText(path)
        self.image_path_edit.setToolTip(path)
        self.background_mode.setCurrentText("Image")
        self._render_key = None
        self._sync_background_controls()

    def clear_image(self):
        self.image_path_edit.clear()
        self.image_path_edit.setToolTip("")
        if self.background_mode.currentText() == "Image":
            self.background_mode.setCurrentText("Gradient")
        self._sync_background_controls()

    def _reload_presets(self, selected=None):
        self._presets = load_card_presets()
        blocker = QSignalBlocker(self.preset_combo)
        self.preset_combo.clear()
        self.preset_combo.addItem("Choose a saved style…", None)
        for name in sorted(self._presets, key=str.casefold):
            self.preset_combo.addItem(name, name)
        index = self.preset_combo.findData(selected)
        self.preset_combo.setCurrentIndex(max(0, index))
        del blocker
        self.delete_preset_button.setEnabled(self.preset_combo.currentData() is not None)

    def save_preset(self):
        name, accepted = QInputDialog.getText(self, "Save Card Preset", "Preset name:",
                                             text=self.preset_combo.currentData() or "")
        if not accepted:
            return
        name = name.strip()
        if name in load_card_presets():
            answer = QMessageBox.question(self, "Replace Card Preset", f'Replace the saved style "{name}"?',
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            save_card_preset(name, self.current_style())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Save Card Preset", str(error))
            return
        self._reload_presets(name)
        self.status.setText(f'Saved preset "{name}". Image presets remember the image location.')

    def apply_preset(self, *_args):
        name = self.preset_combo.currentData()
        self.delete_preset_button.setEnabled(name is not None)
        if name not in self._presets:
            return
        style = style_from_preset(self._presets[name])
        self._updating_style = True
        try:
            self.template_combo.setCurrentText(style.template)
            self.theme_combo.setCurrentText(style.theme)
            for index in range(self.size_combo.count()):
                if tuple(self.size_combo.itemData(index)) == (style.width, style.height):
                    self.size_combo.setCurrentIndex(index)
                    break
            self.font_combo.setCurrentFont(QFont(style.font_family) if style.font_family else library_font(QApplication.font()))
            self.font_size.setValue(style.font_size)
            alignment = style.alignment or ("center" if style.centered else "left")
            self.alignment_combo.setCurrentIndex(self.alignment_combo.findData(alignment))
            self.writing_mode.setCurrentIndex(self.writing_mode.findData(style.writing_mode))
            for key, button in self.text_style_buttons.items():
                button.setChecked(getattr(style, key))
            self.stroke_enabled.setChecked(style.stroke_enabled)
            self.shadow_enabled.setChecked(style.shadow_enabled)
            self.watermark_enabled.setChecked(style.watermark_enabled)
            self.watermark_position.setCurrentIndex(self.watermark_position.findData(style.watermark_position))
            for key in self._effect_colors:
                self._effect_colors[key] = getattr(style, key)
            self._sync_effect_colors()
            self.show_album_cover.setChecked(style.show_album_cover)
            self.details_placement.setCurrentIndex(self.details_placement.findData(style.details_placement))
            self.preserve_lrcx_formatting.setChecked(style.preserve_lrcx_formatting)
            self.include_furigana.setChecked(style.include_furigana)
            self.ruby_position.setCurrentIndex(self.ruby_position.findData(style.ruby_position))
            self._ruby_color = style.ruby_color or self._colors[2]
            self.ruby_custom_color.setChecked(bool(style.ruby_color))
            self.ruby_inherit_stroke.setChecked(style.ruby_inherit_stroke)
            self.ruby_inherit_shadow.setChecked(style.ruby_inherit_shadow)
            self._sync_ruby_color()
            for key in STYLE_RANGES:
                getattr(self, key).setValue(getattr(style, key))
            self.custom_colors.setChecked(bool(style.colors))
            if style.colors:
                self._colors = list(style.colors)
            self._sync_color_controls()
            self.image_path_edit.setText(style.image_path)
            self.image_path_edit.setToolTip(style.image_path)
            self.background_mode.setCurrentText(style.background_mode)
            self._sync_background_controls()
        finally:
            self._updating_style = False
        self._render_key = None
        self.schedule_refresh()

    def delete_preset(self):
        name = self.preset_combo.currentData()
        if name is None:
            return
        try:
            delete_card_preset(name)
        except OSError as error:
            QMessageBox.warning(self, "Delete Card Preset", str(error))
            return
        self._reload_presets()
        self.status.setText(f'Deleted preset "{name}".')

    def current_style(self):
        width, height = self.size_combo.currentData()
        return CardStyle(
            width=width, height=height, theme=self.theme_combo.currentText(),
            font_family=self.font_combo.currentFont().family(), font_size=self.font_size.value(),
            centered=self.alignment_combo.currentIndex() == 1,
            alignment=self.alignment_combo.currentData(),
            writing_mode=self.writing_mode.currentData(),
            **{key: button.isChecked() for key, button in self.text_style_buttons.items()},
            stroke_enabled=self.stroke_enabled.isChecked(), shadow_enabled=self.shadow_enabled.isChecked(),
            watermark_enabled=self.watermark_enabled.isChecked(), watermark_position=self.watermark_position.currentData(),
            **self._effect_colors,
            title=self.title_edit.text(), credit=self.credit_edit.text(), album=self.album_edit.text(),
            details_placement=self.details_placement.currentData(), template=self.template_combo.currentText(),
            show_album_cover=self.show_album_cover.isChecked(),
            preserve_lrcx_formatting=self.preserve_lrcx_formatting.isChecked(),
            include_furigana=self.include_furigana.isChecked(),
            ruby_position=self.ruby_position.currentData(),
            ruby_color=self._ruby_color if self.ruby_custom_color.isChecked() else "",
            ruby_inherit_stroke=self.ruby_inherit_stroke.isChecked(),
            ruby_inherit_shadow=self.ruby_inherit_shadow.isChecked(),
            colors=tuple(self._colors) if self.custom_colors.isChecked() else (),
            background_mode=self.background_mode.currentText(), image_path=self.image_path_edit.text(),
            **{key: getattr(self, key).value() for key in STYLE_RANGES if key != "font_size"},
        )

    def schedule_refresh(self, *_args):
        if self.isVisible() and not self._updating_style:
            self._refresh_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.album_art.resume()
        self.schedule_refresh()

    def hideEvent(self, event):
        self._refresh_timer.stop()
        self.album_art.pause()
        super().hideEvent(event)

    def refresh(self):
        self._refresh_timer.stop()
        if self.editor._source_dirty:
            self._set_error("Apply the source edits before creating an image card.")
            return
        self._configure_album_art()
        lines = self.editor._lines_for_share(self.editor.lines, self.editor._checked_share_line_indices())
        if not lines:
            self._set_error("Select lyric lines to preview and share an image card.")
            return
        text = "\n".join(line.text for line in lines)
        style = self.current_style()
        source = snapshot_card_lyrics(lines, include_readings=style.include_furigana) if (style.preserve_lrcx_formatting or style.include_furigana) else None
        contents = source if source is not None and source.has_formatting else text
        image_stamp = None
        if style.background_mode == "Image":
            try:
                stat = Path(style.image_path).stat()
                image_stamp = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                pass
        key = (text, style, image_stamp, self.album_art.image.cacheKey(), self.album_art.loading, source)
        if key == self._render_key:
            return
        try:
            if style.show_album_cover and not self.album_art.image.isNull():
                self._image = render_lyric_card(contents, style, self.album_art.image)
            else:
                self._image = render_lyric_card(contents, style)
        except ValueError as error:
            self._set_error(str(error))
            return
        self._render_key = key
        self.preview.image = self._image
        self.preview.update()
        self.copy_button.setEnabled(not self.album_art.loading)
        self.save_button.setEnabled(not self.album_art.loading)
        count = len(lines)
        self.preview_details.setText(f"{style.width} × {style.height} · {count} {'line' if count == 1 else 'lines'}")
        self.status.setText(self.album_art.message if self.album_art.loading else
                            "Ready to share. Long excerpts shrink to fit.")

    def _set_error(self, message):
        self.preview_details.clear()
        self._render_key = None
        self._image = QImage()
        self.preview.image = self._image
        self.preview.update()
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status.setText(message)

    def _prepare_image(self):
        if not self.editor._commit_pending_source():
            self._set_error("Apply the source edits before creating an image card.")
            return False
        self.refresh()
        return not self._image.isNull() and not self.album_art.loading

    def copy_image(self):
        if self._prepare_image():
            QApplication.clipboard().setImage(self._image)
            self.status.setText("Copied image to clipboard.")

    def open_image_viewer(self):
        if not self._prepare_image():
            return
        from ui.player_dialogs import CoverPreviewDialog

        viewer = CoverPreviewDialog(QPixmap.fromImage(self._image), self)
        viewer.setWindowTitle("Lyrics Card")
        try:
            viewer.exec()
        finally:
            viewer.deleteLater()

    def export_image(self):
        if not self._prepare_image():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Lyrics Image", str(self.editor.lrc_path.with_name(f"{self.editor.lrc_path.stem}-card.png")),
            "PNG Images (*.png)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        try:
            save_card_png(self._image, target)
        except OSError as error:
            QMessageBox.warning(self, "Save Lyrics Image", f"Could not save image:\n{error}")
            return
        self.status.setText(f"Saved image to {target}.")
