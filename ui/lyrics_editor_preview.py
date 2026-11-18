"""Read-only preview of the selected lyric using the player's renderer."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout,
)

from core.fonts import covering_font
from core.settings import apply_karaoke_settings, get_effective_lyrics_settings, get_karaoke_settings
from lyrics.furigana_widget import FuriganaWidget
from lyrics.karaoke_timing import next_lyric_start


class _LyricsPreviewRenderer(FuriganaWidget):
    def mouseDoubleClickEvent(self, event):
        # Reading edits belong to the dedicated controls below the preview.
        event.accept()


class LyricsEditorPreviewMixin:
    def _build_lyrics_live_preview(self):
        settings = get_effective_lyrics_settings(getattr(self.player, "current_audio_path", None))
        self._lyrics_preview_line_index = None
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        header = QHBoxLayout()
        title = QLabel("Live preview")
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        self.expand_lyric_text_button = QPushButton("Expand text")
        self.expand_lyric_text_button.setCheckable(True)
        self.expand_lyric_text_button.setToolTip("Give the lyric text box more room for longer passages.")
        self.expand_lyric_text_button.toggled.connect(self._expand_lyric_text)
        header.addWidget(self.expand_lyric_text_button)
        layout.addLayout(header)
        self.lyrics_preview_stack = QStackedWidget()
        self.lyrics_preview_stack.setMinimumHeight(104)
        self.lyrics_preview_empty = QLabel("Select a lyric to preview.")
        self.lyrics_preview_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lyrics_preview_empty.setWordWrap(True)
        self.lyrics_preview_stack.addWidget(self.lyrics_preview_empty)

        preview = _LyricsPreviewRenderer()
        self.lyrics_renderer_preview = preview
        preview.setAccessibleName("Live preview of selected lyric")
        preview.setToolTip("Your lyric display settings, scaled to fit the preview.")
        scale = min(1.0, 24 / settings["font_size"])
        preview.set_font(settings["font_family"], round(settings["font_size"] * scale),
                         max(8, round(settings["ruby_font_size"] * scale)))
        self._lyrics_preview_fonts = QFont(preview.base_font), QFont(preview.reading_font)
        preview.set_direction(settings["lyrics_direction"])
        preview.set_alignment(settings["lyrics_alignment"])
        preview.set_colors(settings["lyrics_color"], settings["highlight_color"])
        preview.set_highlight_current(settings["highlight_current"])
        preview.set_ruby_position(settings["ruby_position"])
        preview.set_ruby_padding(settings["ruby_padding"])
        preview.set_lyric_layers(settings["lyric_layer_order"], settings["show_lyrics"],
                                 settings["show_romaji"], settings["show_translation"], settings["show_ruby"])
        preview.set_text_wrapping(True, max_lines=-1)
        preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        apply_karaoke_settings(preview, get_karaoke_settings())
        self.lyrics_preview_scroll = QScrollArea()
        self.lyrics_preview_scroll.setWidgetResizable(True)
        self.lyrics_preview_scroll.setWidget(preview)
        # Keep the player's text colors readable under either editor theme.
        background = "#20242c" if preview.lyrics_color.lightness() > 128 else "#f1f3f5"
        self.lyrics_preview_scroll.setStyleSheet(f"QScrollArea {{ background: {background}; border: none; }}")
        self.lyrics_preview_scroll.viewport().setStyleSheet(f"background-color: {background};")
        preview.setStyleSheet(f"background-color: {background};")
        preview.setAutoFillBackground(True)
        self.lyrics_preview_stack.addWidget(self.lyrics_preview_scroll)
        layout.addWidget(self.lyrics_preview_stack, 1)
        self._expand_lyric_text(False)
        return panel

    def _expand_lyric_text(self, expanded):
        lines = 8 if expanded else 2
        self.line_text.setFixedHeight(max(52, self.line_text.fontMetrics().lineSpacing() * lines + 12))
        self.expand_lyric_text_button.setText("Compact text" if expanded else "Expand text")

    def _refresh_lyrics_preview(self, position_ms=None, rebuild_segments=True):
        preview = getattr(self, "lyrics_renderer_preview", None)
        if preview is None:
            return
        if self.tabs.currentWidget() is not self.lyrics_page:
            self._lyrics_preview_line_index = None
            return
        index, line = self._current_line()
        if line is None or not line.text:
            if preview.lyric:
                preview.set_lyric("", generate=False)
            self._lyrics_preview_line_index = None
            self.lyrics_preview_empty.setText("This lyric is empty." if line is not None else "Select a lyric to preview.")
            self.lyrics_preview_stack.setCurrentWidget(self.lyrics_preview_empty)
            return
        self.lyrics_preview_stack.setCurrentWidget(self.lyrics_preview_scroll)
        if rebuild_segments or self._lyrics_preview_line_index != index:
            base_font, reading_font = self._lyrics_preview_fonts
            preview.base_font = covering_font(base_font, line.text)
            readings = "".join(token.get("reading") or "" for token in self._furigana_editor_tokens(line))
            preview.reading_font = covering_font(reading_font, readings)
            line_end = line.end if line.end is not None else next_lyric_start(self.lines, line)
            media = getattr(self.player, "media_player", None)
            duration = getattr(media, "duration", lambda: 0)()
            if line_end is None and duration > 0:
                line_end = self._audio_to_lyric_seconds(duration)
            if line_end is None:
                line_end = max([line.start + 5] + [segment.end or segment.start for segment in line.segments])
            segments = line.segments or [{"text": line.text, "start": line.start, "end": line_end}]
            preview.set_segments(line.text, segments, line_end, style_spans=line.style_spans,
                                 is_blockquote=line.is_blockquote, translation_lines=line.translation_lines,
                                 generate=False)
            preview.set_current_line(True)
            self._lyrics_preview_line_index = index
        if position_ms is None:
            media = getattr(self.player, "media_player", None)
            position_ms = media.position() if media is not None else self._lyric_to_audio_ms(line.start)
        preview.set_karaoke_position(self._audio_to_lyric_seconds(position_ms))
