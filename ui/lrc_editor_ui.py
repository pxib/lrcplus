from pathlib import Path
from bisect import bisect_right
import copy
import os
import tempfile
import time
import re
import threading

from PySide6.QtCore import Qt, QSignalBlocker, Signal, QSize, QTimer, QUrl, QEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtGui import QKeySequence, QShortcut, QPainter, QFont, QFontMetrics, QColor, QPen, QDesktopServices, QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QDoubleSpinBox, QSplitter, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget, QTableWidget, QTableWidgetItem, QHeaderView, QSlider, QComboBox,
    QCheckBox, QScrollArea, QSizePolicy, QApplication, QMenu,
    QDialogButtonBox, QStackedWidget, QToolButton, QWidgetAction,
)

from lyrics.loader import load_lyrics, get_lyrics_generation_mode
from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import format_timestamp, serialize_lrc, serialize_lrcx
from core.debug import debug_print
from lyrics.lrcx_parser import get_lrcx_offset
from lyrics.widgets import FuriganaWidget
from lyrics.ass_parser import parse_ass
from lyrics.waveform import WaveformWidget, find_ffmpeg
from core.settings import get_playback_settings, get_karaoke_settings, apply_karaoke_settings, get_effective_lyrics_settings, get_lyrics_attachment, set_lyrics_attachment

try:
    from lyrics.furigana import build_furigana_segments, prefetch_furigana_batches, clear_reading_segment_cache, yomi_word_surfaces, set_reading_mode, get_reading_mode, available_reading_modes, detect_lyrics_language, detect_lyrics_collection_language, split_reading_by_text_ranges, token_to_furigana_segments
    from lyrics.reading_providers import find_reading_provider
    from lyrics.reading_providers import (
        available_reading_providers,
        generate_reading_segments,
    )
except Exception:
    build_furigana_segments = None
    token_to_furigana_segments = None
    clear_reading_segment_cache = None
    yomi_word_surfaces = None
    available_reading_providers = lambda: {}
    generate_reading_segments = None
    find_reading_provider = lambda *args, **kwargs: None


from ui.lrc_editor_controls import ClickableSlider, KaraokeSeparationWidget, LyricLabel, WaveformControls
from ui.inline_readings import InlineReadingsEditor
from ui.content_list import configure_content_list
from ui.content_fonts import ContentLabel
from ui.lyrics_preview import LyricsTextEdit


class LrcEditorUiMixin:
    def _build_document_start_page(self):
        from ui.editor_start_page import EditorStartPage

        self.document_start_page = EditorStartPage(self)
        self.create_lrcx_button = self.document_start_page.create_button
        self.open_lrcx_button = self.document_start_page.open_button
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.document_start_page)

    @property
    def share_card(self):
        """Allocate the card controls and fonts only when they are needed."""
        if self._share_card is None:
            from ui.lyrics_share_card import LyricsShareCardWidget

            card = LyricsShareCardWidget(self)
            self._share_card = card
            index = self.share_tabs.indexOf(self._share_card_page)
            current = self.share_tabs.currentIndex()
            blocker = QSignalBlocker(self.share_tabs)
            self.share_tabs.removeTab(index)
            self.share_tabs.insertTab(index, card, "Image card")
            self.share_tabs.setCurrentIndex(current)
            del blocker
            self._share_card_page.deleteLater()
        return self._share_card

    def _share_tab_changed(self, index):
        if self.share_tabs.widget(index) is self._share_card_page:
            self.share_tabs.setCurrentWidget(self.share_card)

    def _build_ui(self):
        # The line list is intentionally outside the tabs. Selecting a lyric
        # must work the same way in Lyrics, Karaoke and Source modes.
        self.line_list = QListWidget()
        configure_content_list(self.line_list)
        self.line_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.line_list.setMinimumWidth(250)
        self.line_list.currentRowChanged.connect(self.load_selected_line)
        self.line_list.itemChanged.connect(self._share_line_checks_changed)
        self.line_list.itemSelectionChanged.connect(self._update_line_selection_controls)
        # Delete removes the selected lyric/timestamp from the document.
        # Install this on the list rather than using a dialog-wide shortcut so
        # Delete keeps its normal meaning while editing lyric/timestamp fields.
        self.line_list.installEventFilter(self)
        self.line_list.viewport().installEventFilter(self)

        self.sidebar_sort_combo = QComboBox()
        self.sidebar_sort_combo.addItem("Source Order", "source")
        self.sidebar_sort_combo.addItem("Timestamp ↑", "timestamp_asc")
        self.sidebar_sort_combo.addItem("Timestamp ↓", "timestamp_desc")
        self.sidebar_sort_combo.currentIndexChanged.connect(self.sidebar_sort_changed)

        self.tabs = QTabWidget(self)

        # ---------------------------- Lyrics ---------------------------
        lyrics_page = QWidget()
        self.lyrics_page = lyrics_page
        lyrics_layout = QVBoxLayout(lyrics_page)
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("Lyrics offset:"))
        self.lyrics_offset = QDoubleSpinBox()
        self.lyrics_offset.setDecimals(0)
        self.lyrics_offset.setRange(-600000, 600000)
        self.lyrics_offset.setSingleStep(50)
        self.lyrics_offset.setSuffix(" ms")
        self.lyrics_offset.setValue(self.lyrics_offset_ms)
        self.lyrics_offset.setToolTip(
            "Positive values delay the lyrics. Negative values make them appear earlier."
        )
        self.lyrics_offset.valueChanged.connect(self.offset_changed)
        offset_row.addWidget(self.lyrics_offset)
        reset_offset = QPushButton("Reset Offset")
        reset_offset.clicked.connect(lambda: self.lyrics_offset.setValue(0))
        offset_row.addWidget(reset_offset)

        timing_tools = QPushButton("Timing tools")
        timing_menu = QMenu(timing_tools)
        timing_menu.addAction("Shift Timing…", self.show_timing_shift_dialog)
        timing_menu.addAction("Validate Timing…", self.show_timing_validation)
        timing_tools.setMenu(timing_menu)
        offset_row.addWidget(timing_tools)
        offset_row.addStretch()

        # Precision transport: keep the familiar seekbar, but let the editor
        # switch to a waveform when timing lyrics.
        transport_mode_row = offset_row
        self.lyrics_transport_toggle = QPushButton("Waveform")
        self.lyrics_transport_toggle.setCheckable(True)
        self.lyrics_transport_toggle.setChecked(False)
        self.lyrics_transport_toggle.setToolTip(
            "Show waveform timing controls. Turn off to use the seekbar."
        )
        ffmpeg_available = find_ffmpeg() is not None
        self.lyrics_transport_toggle.setEnabled(ffmpeg_available)
        if not ffmpeg_available:
            self.lyrics_transport_toggle.setToolTip(
                "Waveform is unavailable because ffmpeg was not found."
            )
        self.lyrics_transport_toggle.toggled.connect(self._toggle_lyrics_transport)
        transport_mode_row.addWidget(self.lyrics_transport_toggle)
        transport_mode_row.addStretch()
        lyrics_layout.addLayout(transport_mode_row)

        self.lyrics_transport_stack = QStackedWidget()
        self.lyrics_transport_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        self.lyrics_seek = ClickableSlider(Qt.Orientation.Horizontal)
        self.lyrics_seek.setRange(0, 0)
        self.lyrics_seek.sliderMoved.connect(self.seek_preview)
        self.lyrics_transport_stack.addWidget(self.lyrics_seek)

        self.lyrics_waveform = WaveformWidget(defer_hidden_load=True, shift_click_playback=True)
        self.lyrics_waveform.seekRequested.connect(self.seek_preview)
        self.lyrics_waveform.playFromRequested.connect(self.play_preview_from)
        self.lyrics_waveform.segmentPlayRequested.connect(self.play_waveform_segment)
        self.lyrics_waveform.karaokeTimingRequested.connect(self._waveform_karaoke_timing_changed)
        self.lyrics_waveform.karaokeTimingFinished.connect(self._waveform_karaoke_timing_finished)
        self.lyrics_waveform.lyricTimingRequested.connect(self._waveform_lyric_timing_changed)
        self.lyrics_transport_stack.addWidget(self.lyrics_waveform)
        self.lyrics_transport_stack.setFixedHeight(
            self.lyrics_seek.sizeHint().height()
        )
        lyrics_layout.addWidget(self.lyrics_transport_stack)
        self.lyrics_waveform_controls = WaveformControls(self.lyrics_waveform)
        self.lyrics_waveform_controls.hide()
        lyrics_layout.addWidget(self.lyrics_waveform_controls)

        lyrics_transport = QHBoxLayout()
        self.lyrics_position_label = QLabel("00:00.000")
        self.lyrics_duration_label = QLabel("00:00.000")
        self.lyrics_back_button = QPushButton("−5 s")
        self.lyrics_play_button = QPushButton("Play")
        self.lyrics_forward_button = QPushButton("+5 s")
        self.lyrics_play_button.setToolTip("Play or pause (Space, outside text fields).")
        self.lyrics_back_button.setToolTip("Seek back 5 seconds. Use A for 1 second or Q for 100 ms.")
        self.lyrics_forward_button.setToolTip("Seek forward 5 seconds. Use D for 1 second or E for 100 ms.")
        self.lyrics_back_button.clicked.connect(lambda: self.skip_preview(-5.0))
        self.lyrics_play_button.clicked.connect(self.toggle_preview)
        self.lyrics_forward_button.clicked.connect(lambda: self.skip_preview(5.0))
        lyrics_transport.addWidget(self.lyrics_position_label)
        lyrics_transport.addStretch()
        lyrics_transport.addWidget(self.lyrics_back_button)
        lyrics_transport.addWidget(self.lyrics_play_button)
        lyrics_transport.addWidget(self.lyrics_forward_button)
        lyrics_transport.addStretch()
        lyrics_transport.addWidget(self.lyrics_duration_label)
        lyrics_layout.addLayout(lyrics_transport)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(4, 4, 4, 4)
        detail_layout.setSpacing(4)
        form = QFormLayout()
        self.line_time = QDoubleSpinBox()
        self.line_time.setDecimals(3)
        self.line_time.setRange(0, 9999 * 60)
        self.line_time.setSingleStep(0.050)
        self.line_time.valueChanged.connect(self.line_details_changed)
        timestamp_row = QHBoxLayout()
        timestamp_row.addWidget(self.line_time)
        self.mark_line_playback_button = QPushButton("Mark Playback Time")
        self.mark_line_playback_button.setToolTip(
            "Use the current playback position for this lyric, then advance to the next line "
            "(F outside text fields, or Ctrl+Enter)."
        )
        self.mark_line_playback_button.clicked.connect(self.mark_current_line_playback_time)
        timestamp_row.addWidget(self.mark_line_playback_button)
        self.commit_line_timestamp_button = QPushButton("Commit")
        self.commit_line_timestamp_button.setToolTip(
            "Commit the selected lyric's timestamp, like Aegisub's Commit button (Ctrl+Shift+Enter)."
        )
        self.commit_line_timestamp_button.clicked.connect(self.commit_line_timestamp)
        timestamp_row.addWidget(self.commit_line_timestamp_button)
        self.commit_line_timestamp_button.setVisible(self.lyrics_transport_toggle.isChecked())
        timestamp_widget = QWidget()
        timestamp_widget.setLayout(timestamp_row)
        form.addRow("Timestamp:", timestamp_widget)
        self.line_text = LyricsTextEdit()
        self.line_text.setAcceptRichText(False)
        self.line_text.setPlaceholderText("Lyric text")
        self.line_text.setToolTip(
            r"Use **bold**, *italic*, ~~strikethrough~~, <u>underline</u>, > at the start for a quote, {\n} for a line break, and {\nt} for a translation row."
        )
        self.line_text.document().contentsChange.connect(self._record_line_text_change)
        self.line_text.textChanged.connect(self.line_details_changed)
        self.text_timing_notice = QLabel()
        self.text_timing_notice.setWordWrap(True)
        self.text_timing_notice.setStyleSheet("color: #d89b35;")
        self.text_timing_notice.hide()
        self.insert_newline_button = QPushButton("Newline")
        self.insert_newline_button.setAutoDefault(False)
        self.insert_newline_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.insert_newline_button.setToolTip(
            "Insert a line break at the lyric cursor, keeping the same lyric timestamp."
        )
        self.insert_newline_button.clicked.connect(self.insert_lyric_newline)
        self.insert_translation_button = QPushButton("Translation")
        self.insert_translation_button.setAutoDefault(False)
        self.insert_translation_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.insert_translation_button.setToolTip(
            "Start a smaller translation row at the lyric cursor, keeping the same timestamp."
        )
        self.insert_translation_button.clicked.connect(self.insert_lyric_translation)
        lyric_tools = QHBoxLayout()
        lyric_tools.addWidget(self.insert_newline_button)
        lyric_tools.addWidget(self.insert_translation_button)
        self.lyric_style_buttons = []
        self.lyric_style_shortcuts = []
        for label, marker, closing, key in (
            ("Bold", "**", "**", "Ctrl+B"),
            ("Italic", "*", "*", "Ctrl+I"),
            ("Strikethrough", "~~", "~~", "Ctrl+Shift+X"),
            ("Underline", "<u>", "</u>", "Ctrl+U"),
        ):
            button = QPushButton(label)
            button.setAccessibleName(label)
            button.setText({"Bold": "B", "Italic": "I", "Strikethrough": "S", "Underline": "U"}[label])
            button.setFixedWidth(30)
            button.setAutoDefault(False)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(f"{label}: wrap the selected lyric text in {marker} ({key}).")
            font = button.font()
            font.setBold(marker == "**")
            font.setItalic(marker == "*")
            font.setStrikeOut(marker == "~~")
            font.setUnderline(marker == "<u>")
            button.setFont(font)
            button.clicked.connect(lambda checked=False, token=marker, end=closing: self.insert_lyric_emphasis(token, end))
            shortcut = QShortcut(QKeySequence(key), self.line_text)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda token=marker, end=closing: self.insert_lyric_emphasis(token, end))
            self.lyric_style_buttons.append(button)
            self.lyric_style_shortcuts.append(shortcut)
            lyric_tools.addWidget(button)
        self.quote_button = QPushButton("Quote")
        self.quote_button.setCheckable(True)
        self.quote_button.setAutoDefault(False)
        self.quote_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.quote_button.setToolTip("Toggle a blockquote for this whole lyric timestamp using > at the start.")
        self.quote_button.clicked.connect(self.toggle_lyric_quote)
        self.lyric_style_buttons.append(self.quote_button)
        lyric_tools.addWidget(self.quote_button)
        lyric_tools.addStretch()
        lyric_field = QWidget()
        lyric_layout = QVBoxLayout(lyric_field)
        lyric_layout.setContentsMargins(0, 0, 0, 0)
        lyric_layout.addLayout(lyric_tools)
        lyric_layout.addWidget(self.line_text)
        form.addRow("Lyric:", lyric_field)
        detail_layout.addLayout(form)
        self.lyrics_live_preview_panel = self._build_lyrics_live_preview()
        detail_layout.addWidget(self.lyrics_live_preview_panel, 1)

        furigana_box = QFrame()
        self.furigana_box = furigana_box
        furigana_box.setFrameShape(QFrame.Shape.StyledPanel)
        furigana_layout = QVBoxLayout(furigana_box)
        self.readings_toggle = QToolButton()
        self.readings_toggle.setText("Readings")
        self.readings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.readings_toggle.setCheckable(True)
        self.readings_toggle.setChecked(True)
        self.readings_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.readings_toggle.setToolTip("Show or hide reading controls.")
        furigana_layout.addWidget(self.readings_toggle)
        self.readings_body = QWidget()
        readings_body_layout = QVBoxLayout(self.readings_body)
        readings_body_layout.setContentsMargins(0, 0, 0, 0)
        readings_hint = QLabel("Click a reading or + to edit; right-click words to combine or split.")
        readings_hint.setWordWrap(True)
        readings_body_layout.addWidget(readings_hint)
        self.readings_editor = InlineReadingsEditor()
        self.readings_editor.readingChanged.connect(self.furigana_changed)
        self.readings_editor.combineRequested.connect(self.combine_reading)
        self.readings_editor.splitRequested.connect(self.split_reading)
        readings_body_layout.addWidget(self.readings_editor)
        furigana_buttons = QHBoxLayout()
        reset_furigana = QPushButton("Reset Readings")
        self.reset_furigana_button = reset_furigana
        reset_furigana.setToolTip("Remove all explicit readings from every lyric and return to automatic generation.")
        reset_furigana.clicked.connect(self.reset_all_furigana)
        furigana_buttons.addWidget(reset_furigana)
        furigana_buttons.addStretch()
        readings_body_layout.addLayout(furigana_buttons)
        furigana_layout.addWidget(self.readings_body)
        self.readings_toggle.toggled.connect(self.readings_body.setVisible)
        self.readings_toggle.toggled.connect(lambda expanded: self.readings_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        detail_layout.addWidget(furigana_box)

        line_buttons = QHBoxLayout()
        add_button = QPushButton("+ Add Line")
        add_button.setToolTip("Add a lyric after the selected line (Ctrl+N).")
        add_button.clicked.connect(self.add_line)
        delete_button = QPushButton("Delete Line")
        self.delete_line_button = delete_button
        delete_button.setToolTip("Delete the selected lyric (Delete while the lyric list has focus).")
        delete_button.clicked.connect(self.delete_line)
        for button in (add_button, delete_button):
            line_buttons.addWidget(button)
        surgery_buttons = line_buttons
        self.split_line_button = QPushButton("Split at Cursor…")
        self.split_line_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.split_line_button.setToolTip("Split the current lyric at the text cursor, keeping word timing and readings.")
        self.split_line_button.clicked.connect(self.split_current_line)
        self.merge_lines_button = QPushButton("Merge Lines…")
        self.merge_lines_button.setToolTip("Merge selected adjacent lines, or the current line with its next source line.")
        self.merge_lines_button.clicked.connect(self.merge_selected_lines)
        surgery_buttons.addWidget(self.split_line_button)
        surgery_buttons.addWidget(self.merge_lines_button)
        move_button = QPushButton("Move")
        move_menu = QMenu(move_button)
        move_menu.addAction("Move Up", lambda: self.move_line(-1))
        move_menu.addAction("Move Down", lambda: self.move_line(1))
        move_button.setMenu(move_menu)
        surgery_buttons.addWidget(move_button)
        surgery_buttons.addStretch()
        detail_layout.addLayout(surgery_buttons)
        detail_layout.addWidget(self.text_timing_notice)
        lyrics_layout.addWidget(detail, 1)
        self.tabs.addTab(lyrics_page, "Lyrics")

        self.translation_page = None
        self.translation_panel = None
        manager = getattr(self.player, "plugin_manager", None)
        if manager is not None and getattr(manager, "translation_providers", {}):
            self.translation_page = QWidget()
            QVBoxLayout(self.translation_page).setContentsMargins(0, 0, 0, 0)
            self.tabs.addTab(self.translation_page, "Translations")

        # -------------------------- Generation -------------------------
        # Reading generation is global for the editor, so it belongs here
        # rather than forcing the user to configure it per lyric line.
        generation_page = QWidget()
        generation_layout = QVBoxLayout(generation_page)

        generation_title = QLabel("Reading generation")
        generation_layout.addWidget(generation_title)

        generation_hint = QLabel(
            "Choose which automatic reading system to use for the lyrics. "
            "This applies globally while editing; explicit readings already "
            "stored in the lyrics are preserved."
        )
        generation_hint.setWordWrap(True)
        generation_layout.addWidget(generation_hint)

        generation_form = QFormLayout()
        self.reading_mode_combo = QComboBox()
        for mode_id, mode_name in available_reading_modes().items():
            self.reading_mode_combo.addItem(mode_name, mode_id)

        current_mode = get_reading_mode()
        index = self.reading_mode_combo.findData(current_mode)
        if index >= 0:
            self.reading_mode_combo.setCurrentIndex(index)

        self.reading_mode_combo.currentIndexChanged.connect(self.reading_mode_changed)
        self._update_reading_mode_info()
        generation_form.addRow("Generate as:", self.reading_mode_combo)
        generation_layout.addLayout(generation_form)

        self.detected_language_label = QLabel("Detected: Other")
        generation_layout.addWidget(self.detected_language_label)

        self.generate_all_readings_button = QPushButton("Generate readings for all lyrics")
        self.generate_all_readings_button.setToolTip(
            "Regenerate automatic readings for every lyric line using the selected system."
        )
        self.generate_all_readings_button.clicked.connect(self.generate_readings_for_all_lyrics)
        generation_layout.addWidget(self.generate_all_readings_button)

        self.generate_timestamp_button = QPushButton("Generate For This Timestamp")
        self.generate_timestamp_button.setToolTip(
            "Generate automatic reading data for the currently selected lyric/timestamp only."
        )
        self.generate_timestamp_button.clicked.connect(
            self.generate_readings_for_current_timestamp
        )
        generation_layout.addWidget(self.generate_timestamp_button)

        self.reading_mode_info = QLabel()
        self.reading_mode_info.setWordWrap(True)
        self._update_reading_mode_info()
        generation_layout.addWidget(self.reading_mode_info)

        generation_layout.addStretch()
        self.tabs.addTab(generation_page, "Generation")

        if self.karaoke_enabled:
            # --------------------------- Karaoke ---------------------------
            self.karaoke_page = QWidget()
            karaoke_layout = QVBoxLayout(self.karaoke_page)
            self.karaoke_hint = QLabel("Select a lyric line from the shared list. Click the separators between characters to create karaoke chunks.")
            self.karaoke_hint.setWordWrap(True)
            karaoke_layout.addWidget(self.karaoke_hint)

            # Karaoke gets the same transport choice as the Lyrics tab: a
            # compact seekbar for ordinary playback, or a waveform for visual
            # timing work.
            karaoke_transport_controls = QHBoxLayout()
            karaoke_transport_controls.addWidget(QLabel("Timeline:"))
            self.karaoke_transport_toggle = QPushButton("Waveform")
            self.karaoke_transport_toggle.setCheckable(True)
            self.karaoke_transport_toggle.setToolTip(
                "Show waveform timing controls. Turn off to use the seekbar."
            )
            self.karaoke_transport_toggle.setEnabled(ffmpeg_available)
            if not ffmpeg_available:
                self.karaoke_transport_toggle.setToolTip(
                    "Waveform is unavailable because ffmpeg was not found."
                )
            self.karaoke_transport_toggle.toggled.connect(self._toggle_karaoke_transport)
            karaoke_transport_controls.addWidget(self.karaoke_transport_toggle)
            karaoke_transport_controls.addStretch()
            karaoke_layout.addLayout(karaoke_transport_controls)

            self.karaoke_transport_stack = QStackedWidget()
            self.karaoke_transport_stack.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Fixed,
            )
            self.karaoke_seek = ClickableSlider(Qt.Orientation.Horizontal)
            self.karaoke_seek.setRange(0, 0)
            self.karaoke_seek.sliderMoved.connect(self.seek_preview)
            self.karaoke_transport_stack.addWidget(self.karaoke_seek)

            self.karaoke_waveform = WaveformWidget(defer_hidden_load=True)
            self.karaoke_waveform.seekRequested.connect(self.seek_preview)
            self.karaoke_waveform.segmentPlayRequested.connect(self.play_waveform_segment)
            self.karaoke_waveform.karaokeTimingRequested.connect(self._waveform_karaoke_timing_changed)
            self.karaoke_waveform.karaokeTimingFinished.connect(self._waveform_karaoke_timing_finished)
            self.karaoke_waveform.lyricTimingRequested.connect(self._waveform_karaoke_line_boundary_changed)
            self.karaoke_transport_stack.addWidget(self.karaoke_waveform)
            self.karaoke_transport_stack.setFixedHeight(
                self.karaoke_seek.sizeHint().height()
            )
            karaoke_layout.addWidget(self.karaoke_transport_stack)
            self.karaoke_waveform_controls = WaveformControls(self.karaoke_waveform, karaoke=True)
            self.karaoke_waveform_controls.hide()
            karaoke_layout.addWidget(self.karaoke_waveform_controls)

            preview_controls = QHBoxLayout()
            self.preview_position_label = QLabel("00:00.000")
            self.preview_duration_label = QLabel("00:00.000")
            self.preview_back_button = QPushButton("−5 s")
            self.preview_play_button = QPushButton("Play")
            self.preview_forward_button = QPushButton("+5 s")
            self.preview_play_button.setToolTip("Play or pause (Space, outside text fields).")
            self.preview_back_button.setToolTip("Seek back 5 seconds. Use A for 1 second or Q for 100 ms.")
            self.preview_forward_button.setToolTip("Seek forward 5 seconds. Use D for 1 second or E for 100 ms.")
            self.preview_back_button.clicked.connect(lambda: self.skip_preview(-5.0))
            self.preview_play_button.clicked.connect(self.toggle_preview)
            self.preview_forward_button.clicked.connect(lambda: self.skip_preview(5.0))
            preview_controls.addWidget(self.preview_position_label)
            preview_controls.addStretch()
            preview_controls.addWidget(self.preview_back_button)
            preview_controls.addWidget(self.preview_play_button)
            preview_controls.addWidget(self.preview_forward_button)
            preview_controls.addStretch()
            preview_controls.addWidget(self.preview_duration_label)
            karaoke_layout.addLayout(preview_controls)

            separation_box = QFrame()
            separation_box.setFrameShape(QFrame.Shape.StyledPanel)
            separation_layout = QVBoxLayout(separation_box)
            separation_layout.addWidget(QLabel("Separation Center"))
            self.full_line_label = LyricLabel("")
            self.full_line_label.setWordWrap(True)
            separation_layout.addWidget(self.full_line_label)

            self.separator_scroll = QScrollArea()
            self.separator_scroll.setWidgetResizable(False)
            self.separator_widget = KaraokeSeparationWidget()
            self.separator_widget.boundariesChanged.connect(self.refresh_separation_preview)
            self.separator_scroll.setWidget(self.separator_widget)
            self.separator_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.separator_scroll.setMinimumHeight(
                self.separator_widget.sizeHint().height()
                + self.separator_scroll.horizontalScrollBar().sizeHint().height()
                + 2 * self.separator_scroll.frameWidth()
            )
            separation_layout.addWidget(self.separator_scroll)

            separation_buttons = QHBoxLayout()
            auto = QPushButton("Automatic Separation")
            auto.clicked.connect(self.auto_separate_current_line)
            apply = QPushButton("Apply Separation")
            apply.clicked.connect(self.apply_visual_separation)
            reset = QPushButton("Reset Separation")
            reset.clicked.connect(self.reset_current_separation)
            for button in (auto, apply, reset):
                separation_buttons.addWidget(button)
            separation_buttons.addStretch()
            separation_layout.addLayout(separation_buttons)
            self.separation_preview = LyricLabel("")
            self.separation_preview.setWordWrap(True)
            separation_layout.addWidget(self.separation_preview)
            karaoke_layout.addWidget(separation_box)

            self.segment_table = QTableWidget(0, 5)

            self.segment_table.setEditTriggers(

                QAbstractItemView.EditTrigger.DoubleClicked

            )
            self.segment_table.setHorizontalHeaderLabels(["Start", "End", "Text", "Ruby", "Instant"])
            header = self.segment_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self.segment_table.itemChanged.connect(self.segment_changed)
            self.segment_table.currentCellChanged.connect(lambda *_: self._refresh_karaoke_waveform_editor())
            # Table editors receive key events before the dialog does. Install an
            # event filter so karaoke shortcuts cannot both run AND type into a
            # Start/End editor.
            self.segment_table.installEventFilter(self)
            self.segment_table.viewport().installEventFilter(self)
            karaoke_layout.addWidget(self.segment_table, 1)
            self._build_karaoke_timing_tools(karaoke_layout)

            self.overlap_warning = QLabel("")
            self.overlap_warning.setWordWrap(True)
            self.overlap_warning.setStyleSheet("color: #ff9b73;")
            self.overlap_warning.setVisible(False)
            karaoke_layout.addWidget(self.overlap_warning)
            self.karaoke_text_timing_notice = QLabel()
            self.karaoke_text_timing_notice.setWordWrap(True)
            self.karaoke_text_timing_notice.setStyleSheet("color: #d89b35;")
            self.karaoke_text_timing_notice.hide()
            karaoke_layout.addWidget(self.karaoke_text_timing_notice)

            timing = QHBoxLayout()
            timing.addWidget(QLabel("Current:"))
            self.current_time = QDoubleSpinBox()
            self.current_time.setReadOnly(True)
            self.current_time.installEventFilter(self)
            self.current_time.setDecimals(3)
            self.current_time.setRange(0, 9999 * 60)
            self.current_time.setSingleStep(0.050)
            timing.addWidget(self.current_time)
            mark = QPushButton("Mark Playback Time [F]")
            mark.setToolTip(
                "F = Mark current Start/End\n"
                "Q/E = -/+ 100 ms\n"
                "A/D = -/+ 1 second"
            )
            mark.clicked.connect(self.use_playback_time)
            timing.addWidget(mark)

            timing.addStretch()
            karaoke_layout.addLayout(timing)

            self.karaoke_scroll = QScrollArea()
            self.karaoke_scroll.setWidgetResizable(True)
            self.karaoke_scroll.setWidget(self.karaoke_page)
            self.tabs.addTab(self.karaoke_scroll, "Karaoke")

        # ----------------------------- Source --------------------------
        source_page = QWidget()
        source_layout = QVBoxLayout(source_page)
        source_hint = QLabel("Edit LRC/LRCX source. Changes apply when you leave this tab or save.")
        source_hint.setWordWrap(True)
        source_layout.addWidget(source_hint)
        source_header = QHBoxLayout()
        self.source_format_label = QLabel()
        source_header.addWidget(self.source_format_label)
        self.source_diagnostics_label = QLabel("No source issues")
        source_header.addWidget(self.source_diagnostics_label)
        source_header.addStretch()
        self.source_position_label = QLabel("Ln 1, Col 1")
        source_header.addWidget(self.source_position_label)
        source_layout.addLayout(source_header)
        from ui.source_editor import SourceTextEdit
        self.source_editor = SourceTextEdit(suffix=self._source_suffix)
        self.source_editor.sourceTextChanged.connect(self._source_text_changed)
        self.source_editor.undoAvailable.connect(self._update_history_actions)
        self.source_editor.redoAvailable.connect(self._update_history_actions)
        self.source_editor.cursorPositionChanged.connect(self._source_cursor_moved)
        self.source_editor.diagnosticsChanged.connect(self._update_source_diagnostics)
        source_layout.addWidget(self.source_editor, 1)
        self.source_issues_list = QListWidget()
        self.source_issues_list.setAccessibleName("Source issues; select an issue to jump to its location")
        self.source_issues_list.setWordWrap(True)
        self.source_issues_list.itemClicked.connect(
            lambda item: self.source_editor.go_to_issue(item.data(Qt.ItemDataRole.UserRole)))
        self.source_issues_list.itemActivated.connect(
            lambda item: self.source_editor.go_to_issue(item.data(Qt.ItemDataRole.UserRole)))
        self.source_issues_list.hide()
        source_layout.addWidget(self.source_issues_list)
        source_buttons = QHBoxLayout()
        sync_source = QPushButton("Refresh from lyrics")
        sync_source.setToolTip("Regenerate source from the structured lyrics.")
        sync_source.clicked.connect(self.sync_source_from_model)
        reload_source = QPushButton("Apply source")
        reload_source.setToolTip("Validate and apply source edits to the structured lyrics.")
        reload_source.clicked.connect(self.reload_model_from_source)
        open_source_file = QPushButton("Open File")
        open_source_file.setToolTip("Open this lyrics file with your system's default application.")
        open_source_file.clicked.connect(self.open_lyrics_file)
        open_source_folder = QPushButton("Open Folder")
        open_source_folder.setToolTip("Open the folder containing this lyrics file.")
        open_source_folder.clicked.connect(self.open_lyrics_folder)
        source_buttons.addWidget(sync_source)
        source_buttons.addWidget(reload_source)
        source_buttons.addWidget(open_source_file)
        source_buttons.addWidget(open_source_folder)
        import_ass = QPushButton("Import ASS…")
        import_ass.setToolTip(
            "Import ASS/SSA Dialogue lines and convert \\k, \\K, \\kf and \\ko karaoke timing into LRCX."
        )
        import_ass.clicked.connect(self.import_ass)
        source_buttons.addStretch()
        source_buttons.addWidget(import_ass)
        source_layout.addLayout(source_buttons)
        self.tabs.addTab(source_page, "Source")

        # ----------------------------- Share --------------------------
        self.share_page = QWidget()
        share_layout = QVBoxLayout(self.share_page)
        self.share_tabs = QTabWidget()
        share_layout.addWidget(self.share_tabs, 1)
        share_text_page = QWidget()
        share_text_layout = QVBoxLayout(share_text_page)
        share_text_layout.addWidget(QLabel("Copy or export the current lyrics as text."))

        share_format_row = QHBoxLayout()
        share_format_row.addWidget(QLabel("Format:"))
        self.share_format_combo = QComboBox()
        self.share_format_combo.addItem("Plain text", "text")
        self.share_format_combo.addItem("With line timestamps", "lrc")
        share_format_row.addWidget(self.share_format_combo)
        share_format_row.addStretch()
        share_text_layout.addLayout(share_format_row)

        share_actions = QHBoxLayout()
        copy_share_button = QPushButton("Copy")
        copy_share_button.clicked.connect(self.copy_share_text)
        export_share_button = QPushButton("Export…")
        export_share_button.clicked.connect(self.export_share_text)
        share_actions.addWidget(copy_share_button)
        share_actions.addWidget(export_share_button)
        share_actions.addStretch()
        share_text_layout.addLayout(share_actions)

        self.share_status_label = QLabel("")
        share_text_layout.addWidget(self.share_status_label)
        share_text_layout.addStretch()
        self.share_tabs.addTab(share_text_page, "Text")
        self._share_card = None
        self._share_card_page = QWidget()
        self.share_tabs.addTab(self._share_card_page, "Image card")
        self.share_tabs.currentChanged.connect(self._share_tab_changed)
        self.share_selection_label = QLabel("")
        share_layout.addWidget(self.share_selection_label)
        self.tabs.addTab(self.share_page, "Share")

        # ---------------------------- Advanced --------------------------
        advanced_page = QWidget()
        advanced_layout = QVBoxLayout(advanced_page)

        association_frame = QFrame()
        association_frame.setFrameShape(QFrame.Shape.StyledPanel)
        association_layout = QVBoxLayout(association_frame)

        title = QLabel("Song Association")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        association_layout.addWidget(title)

        self.song_association_status = ContentLabel()
        self.song_association_status.setWordWrap(True)
        association_layout.addWidget(self.song_association_status)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Associated Song:"))
        self.song_association_label = ContentLabel()
        self.song_association_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.song_association_label.setWordWrap(False)
        path_row.addWidget(self.song_association_label, 1)
        association_layout.addLayout(path_row)

        self.force_song_button = QPushButton()
        self.force_song_button.clicked.connect(self.toggle_song_association)
        association_layout.addWidget(self.force_song_button)

        self.open_song_file_button = QPushButton("Choose Lyrics File for This Song…")
        self.open_song_file_button.setToolTip("Choose any .lrc or .lrcx file to force for the currently loaded song.")
        self.open_song_file_button.clicked.connect(self.choose_lyrics_file_for_current_song)
        association_layout.addWidget(self.open_song_file_button)

        metadata_hint = QLabel(
            'The selected song is mapped directly to the chosen lyrics file.'
        )
        metadata_hint.setWordWrap(True)
        association_layout.addWidget(metadata_hint)

        advanced_layout.addWidget(association_frame)

        self.danger_frame = QFrame()
        self.danger_frame.setFrameShape(QFrame.Shape.StyledPanel)
        danger_layout = QVBoxLayout(self.danger_frame)
        danger_title = QLabel("Danger")
        danger_font = danger_title.font()
        danger_font.setBold(True)
        danger_title.setFont(danger_font)
        danger_layout.addWidget(danger_title)
        danger_hint = QLabel("These actions affect every lyric line. Confirm before clearing; use Undo to restore.")
        danger_hint.setWordWrap(True)
        danger_layout.addWidget(danger_hint)
        for label, description, callback in (
            ("Clear Karaoke Tags", "Remove all karaoke timing and instant-fill flags. Keep lyric text, line timestamps, and furigana.", self.clear_all_karaoke),
            ("Clear Furigana", "Remove all saved furigana, including inline readings. Automatic readings can be generated again.", self.reset_all_readings_advanced),
            ("Clear Translations", "Remove all translation rows while keeping original lyrics, timing, furigana, and formatting.", self.clear_all_translations),
            ("Clear Formatting", "Remove bold, italic, strikethrough, underline, and blockquotes from every line.", self.clear_all_formatting),
        ):
            button = QPushButton(label)
            button.setToolTip(description)
            button.clicked.connect(callback)
            danger_layout.addWidget(button)
            hint = QLabel(description)
            hint.setWordWrap(True)
            danger_layout.addWidget(hint)
        advanced_layout.addWidget(self.danger_frame)
        advanced_layout.addStretch()
        self.tabs.addTab(advanced_page, "Advanced")
        self._update_song_association_ui()
        self.tabs.currentChanged.connect(self._tab_changed)
        # Source is serialized when opened. A hidden QTextDocument and its
        # syntax runs otherwise duplicate the complete structured document.
        self.source_format_label.setText(f"{self._source_suffix[1:].upper()} source")

        # Main editor workspace. The karaoke renderer lives in its own
        # inspector panel on the right instead of consuming vertical space in
        # the Karaoke tab.
        # Left column: lyric list plus a quick jump back to the line
        # currently reached by playback.
        self.line_list_panel = QWidget()
        line_list_panel = self.line_list_panel
        line_list_layout = QVBoxLayout(line_list_panel)
        line_list_layout.setContentsMargins(0, 0, 0, 0)
        self._build_lyric_search(line_list_layout)
        self._build_line_selection_controls(line_list_layout)
        line_list_layout.addWidget(self.sidebar_sort_combo)

        self.share_selection_controls = QWidget()
        share_selection_layout = QHBoxLayout(self.share_selection_controls)
        share_selection_layout.setContentsMargins(0, 0, 0, 0)
        select_all_share_lines = QPushButton("Select All")
        select_all_share_lines.clicked.connect(self.select_all_share_lines)
        clear_share_line_selection = QPushButton("Clear")
        clear_share_line_selection.clicked.connect(self.clear_share_line_selection)
        share_selection_layout.addWidget(select_all_share_lines)
        share_selection_layout.addWidget(clear_share_line_selection)
        self.share_selection_controls.setVisible(False)
        line_list_layout.addWidget(self.share_selection_controls)
        line_list_layout.addWidget(self.line_list, 1)

        self.go_to_current_lyric_button = QPushButton("Go to Current Lyric")
        self.go_to_current_lyric_button.setToolTip(
            "Select and scroll to the lyric currently reached by playback."
        )
        self.go_to_current_lyric_button.clicked.connect(self.go_to_current_lyric)
        line_list_layout.addWidget(self.go_to_current_lyric_button)
        self._build_inline_timing_issues(line_list_layout)

        workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter = workspace
        workspace.splitterMoved.connect(self._remember_editor_panel_widths)
        workspace.addWidget(line_list_panel)
        workspace.addWidget(self.tabs)
        workspace.setStretchFactor(1, 1)

        self.karaoke_preview_panel = None
        if self.karaoke_enabled:
            self.karaoke_preview_panel = QFrame()
            self.karaoke_preview_panel.setFrameShape(QFrame.Shape.StyledPanel)
            self.karaoke_preview_panel.setMinimumWidth(320)
            self.karaoke_preview_panel.setMaximumWidth(480)
            preview_layout = QVBoxLayout(self.karaoke_preview_panel)

            preview_title = QLabel("Karaoke Preview")
            preview_title.setStyleSheet("font-weight: 600;")
            preview_layout.addWidget(preview_title)

            preview_hint = QLabel(
                "Live preview using the same renderer as the player."
            )
            preview_hint.setWordWrap(True)
            preview_layout.addWidget(preview_hint)

            self.karaoke_renderer_preview = FuriganaWidget()
            self.karaoke_renderer_preview.set_focus_transition(
                get_effective_lyrics_settings(getattr(self.player, "current_audio_path", None))["focus_transition"]
            )
            apply_karaoke_settings(self.karaoke_renderer_preview, get_karaoke_settings())
            self._karaoke_preview_fonts = (
                QFont(self.karaoke_renderer_preview.base_font),
                QFont(self.karaoke_renderer_preview.reading_font),
            )
            self.karaoke_renderer_preview.setMinimumHeight(180)
            self.karaoke_renderer_preview.alignment = "center"
            self.karaoke_renderer_preview.highlight_animation = "none"
            # Let the renderer derive the line count from the actual preview
            # height instead of imposing a strict three-row ceiling.
            self.karaoke_renderer_preview.set_text_wrapping(True, max_lines=0)
            preview_layout.addWidget(self.karaoke_renderer_preview, 1)

            workspace.addWidget(self.karaoke_preview_panel)
            workspace.setStretchFactor(2, 0)

        # Document-wide cleanup actions live in Advanced > Danger.
        self.reset_karaoke_button = None
        self.lyrics_shortcuts_button = QPushButton("Shortcuts")
        self.lyrics_shortcuts_button.setToolTip("Search keyboard shortcuts and waveform controls (F1).")
        self.lyrics_shortcuts_button.clicked.connect(self.show_lyrics_shortcuts)
        self.karaoke_shortcuts_button = None

        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)

        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(bool(self.document_dirty))
        self.save_button.setToolTip("Save and keep editing (Ctrl+S).")
        self.save_button.clicked.connect(self.save_document)
        self.save_close_button = QPushButton("Save && Close")
        self.save_close_button.clicked.connect(self.save_and_close)
        cancel_button = QPushButton("Close")
        cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.quota_button = QPushButton("Quota")
        self.quota_button.setToolTip("Show quota estimates for the selected translation provider.")
        self.quota_button.clicked.connect(self.show_translation_quota)
        self.quota_button.hide()
        buttons.addWidget(self.quota_button)
        buttons.addWidget(self.lyrics_shortcuts_button)
        if self.karaoke_shortcuts_button is not None:
            buttons.addWidget(self.karaoke_shortcuts_button)
        if self.reset_karaoke_button is not None:
            buttons.addWidget(self.reset_karaoke_button)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.redo_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.save_close_button)
        buttons.addWidget(cancel_button)

        layout = self.layout() or QVBoxLayout(self)
        layout.addWidget(workspace, 1)
        self.preview_tools = self._build_preview_tools()
        layout.addWidget(self.preview_tools)
        layout.addLayout(buttons)

        workspace.setSizes([250, 900, 360])
        self._tab_changed(self.tabs.currentIndex())

    def _source_cursor_moved(self):
        cursor = self.source_editor.textCursor()
        prefix = cursor.block().text().encode("utf-16-le")[:cursor.positionInBlock() * 2]
        column = len(prefix.decode("utf-16-le", errors="ignore")) + 1
        self.source_position_label.setText(f"Ln {cursor.blockNumber() + 1}, Col {column}")

    def _update_source_diagnostics(self, issues):
        if not hasattr(self, "source_issues_list"):
            return
        self.source_issues_list.clear()
        for issue in issues:
            item = QListWidgetItem(f"Line {issue.line_number}: {issue.message}")
            item.setData(Qt.ItemDataRole.UserRole, issue)
            self.source_issues_list.addItem(item)
        self.source_issues_list.setFixedHeight(min(120, max(48, len(issues) * self.fontMetrics().lineSpacing() + 16)))
        self.source_issues_list.setVisible(bool(issues))
        self.source_diagnostics_label.setText(f"{len(issues)} source issue(s)" if issues else "No source issues")
        self.source_diagnostics_label.setStyleSheet(
            f"color: {self.source_editor._error_color().name()};" if issues else "")

    def _build_preview_tools(self):
        tools = QWidget()
        row = QHBoxLayout(tools)
        row.setContentsMargins(0, 0, 0, 0)
        self.preview_selection_button = QPushButton("Preview line")
        self.preview_selection_button.setToolTip("Play the selected lyric or karaoke segment with the preview options below.")
        self.preview_selection_button.clicked.connect(self.preview_selected_timing)
        row.addWidget(self.preview_selection_button)
        self.preview_loop = QCheckBox("Loop")
        self.preview_loop.setToolTip("Repeat a line, karaoke segment, or clicked waveform segment until you pause or seek.")
        row.addWidget(self.preview_loop)
        row.addWidget(QLabel("Speed:"))
        self.preview_speed = QComboBox()
        self.preview_speed.setAccessibleName("Preview playback speed")
        for rate in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            self.preview_speed.addItem(f"{rate:g}×", rate)
        media = getattr(self.player, "media_player", None)
        self._sync_preview_speed(getattr(media, "playbackRate", lambda: 1.0)())
        self.preview_speed.currentIndexChanged.connect(self.change_preview_speed)
        self.preview_speed.setEnabled(callable(getattr(self.player, "handle_speed_change", None)))
        row.addWidget(self.preview_speed)
        options = QPushButton("Preview options")
        menu = QMenu(options)
        padding = QWidget()
        form = QFormLayout(padding)
        for name, label in (("preview_lead_in", "Lead-in:"), ("preview_lead_out", "Lead-out:")):
            field = QDoubleSpinBox()
            field.setRange(0, 5)
            field.setDecimals(1)
            field.setSingleStep(0.1)
            field.setSuffix(" s")
            field.setAccessibleName(label.rstrip(":"))
            setattr(self, name, field)
            form.addRow(label, field)
        action = QWidgetAction(menu)
        action.setDefaultWidget(padding)
        menu.addAction(action)
        options.setMenu(menu)
        row.addWidget(options)
        self.preview_status = QLabel()
        self.preview_status.setWordWrap(True)
        row.addWidget(self.preview_status, 1)
        return tools

