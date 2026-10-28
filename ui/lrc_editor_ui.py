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
from PySide6.QtGui import QKeySequence, QShortcut, QPainter, QFontMetrics, QColor, QPen, QDesktopServices, QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QDoubleSpinBox, QSplitter, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget, QTableWidget, QTableWidgetItem, QHeaderView, QSlider, QComboBox,
    QCheckBox, QScrollArea, QSizePolicy, QApplication, QMenu,
    QDialogButtonBox, QStackedWidget,
)

from lyrics.loader import load_lyrics, get_lyrics_generation_mode
from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import format_timestamp, serialize_lrc, serialize_lrcx
from core.debug import debug_print
from lyrics.lrcx_parser import get_lrcx_offset
from lyrics.widgets import FuriganaWidget
from lyrics.ass_parser import parse_ass
from lyrics.waveform import WaveformWidget, find_ffmpeg
from core.settings import get_playback_settings, get_karaoke_settings, save_karaoke_settings, get_lyrics_attachment, set_lyrics_attachment

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


from ui.lrc_editor_controls import ClickableSlider, KaraokeSeparationWidget


class LrcEditorUiMixin:
    def _build_ui(self):
        # The line list is intentionally outside the tabs. Selecting a lyric
        # must work the same way in Lyrics, Karaoke and Source modes.
        self.line_list = QListWidget()
        self.line_list.setMinimumWidth(250)
        self.line_list.currentRowChanged.connect(self.load_selected_line)
        self.line_list.itemChanged.connect(self._share_line_checks_changed)
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
        lyrics_layout = QVBoxLayout(lyrics_page)
        hint = QLabel("Select a lyric line, edit it here, or use the shared seekbar to align its timestamp.")
        hint.setWordWrap(True)
        lyrics_layout.addWidget(hint)

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
        offset_row.addStretch()
        lyrics_layout.addLayout(offset_row)

        timing_tools = QHBoxLayout()
        self.shift_timing_button = QPushButton("Shift Timing...")
        self.shift_timing_button.setToolTip("Shift all lines or a source-order range, including karaoke timing.")
        self.shift_timing_button.clicked.connect(self.show_timing_shift_dialog)
        timing_tools.addWidget(self.shift_timing_button)
        self.validate_timing_button = QPushButton("Validate Timing")
        self.validate_timing_button.clicked.connect(self.show_timing_validation)
        timing_tools.addWidget(self.validate_timing_button)
        timing_tools.addStretch()
        lyrics_layout.addLayout(timing_tools)

        # Precision transport: keep the familiar seekbar, but let the editor
        # switch to a waveform when timing lyrics.
        transport_mode_row = QHBoxLayout()
        transport_mode_row.addWidget(QLabel("Timeline:"))
        self.lyrics_transport_toggle = QPushButton("Waveform")
        self.lyrics_transport_toggle.setCheckable(True)
        self.lyrics_transport_toggle.setChecked(False)
        self.lyrics_transport_toggle.setToolTip(
            "Toggle between the normal seekbar and the audio waveform."
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

        self.lyrics_waveform = WaveformWidget()
        self.lyrics_waveform.seekRequested.connect(self.seek_preview)
        self.lyrics_waveform.segmentPlayRequested.connect(self.play_waveform_segment)
        self.lyrics_waveform.karaokeTimingRequested.connect(self._waveform_karaoke_timing_changed)
        self.lyrics_waveform.karaokeTimingFinished.connect(self._waveform_karaoke_timing_finished)
        self.lyrics_waveform.lyricTimingRequested.connect(self._waveform_lyric_timing_changed)
        self.lyrics_transport_stack.addWidget(self.lyrics_waveform)
        self.lyrics_transport_stack.setFixedHeight(
            self.lyrics_seek.sizeHint().height()
        )
        lyrics_layout.addWidget(self.lyrics_transport_stack)

        lyrics_transport = QHBoxLayout()
        self.lyrics_position_label = QLabel("00:00.000")
        self.lyrics_duration_label = QLabel("00:00.000")
        self.lyrics_back_button = QPushButton("<< 5s")
        self.lyrics_play_button = QPushButton("Play")
        self.lyrics_forward_button = QPushButton("5s >>")
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
            "Use the current playback position for this lyric, then advance to the next line."
        )
        self.mark_line_playback_button.clicked.connect(self.mark_current_line_playback_time)
        timestamp_row.addWidget(self.mark_line_playback_button)
        self.commit_line_timestamp_button = QPushButton("Commit")
        self.commit_line_timestamp_button.setToolTip(
            "Commit the selected lyric's timestamp, like Aegisub's Commit button."
        )
        self.commit_line_timestamp_button.clicked.connect(self.commit_line_timestamp)
        timestamp_row.addWidget(self.commit_line_timestamp_button)
        self.commit_line_timestamp_button.setVisible(self.lyrics_transport_toggle.isChecked())
        timestamp_widget = QWidget()
        timestamp_widget.setLayout(timestamp_row)
        form.addRow("Timestamp:", timestamp_widget)
        self.line_text = QTextEdit()
        self.line_text.setAcceptRichText(False)
        self.line_text.setPlaceholderText("Lyric text")
        self.line_text.textChanged.connect(self.line_details_changed)
        form.addRow("Lyric:", self.line_text)
        detail_layout.addLayout(form)

        ruby_box = QFrame()
        ruby_box.setFrameShape(QFrame.Shape.StyledPanel)
        ruby_layout = QVBoxLayout(ruby_box)
        ruby_layout.addWidget(QLabel("Reading preview"))
        self.ruby_preview = QLabel("Choose Auto, Pinyin, Jyutping, or Yale. Explicit LRCX ruby is preserved.")
        self.ruby_preview.setWordWrap(True)
        self.ruby_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ruby_layout.addWidget(self.ruby_preview)
        detail_layout.addWidget(ruby_box)

        furigana_box = QFrame()
        self.furigana_box = furigana_box
        furigana_box.setFrameShape(QFrame.Shape.StyledPanel)
        furigana_layout = QVBoxLayout(furigana_box)
        furigana_layout.addWidget(QLabel("Reading editor"))
        self.furigana_karaoke_notice = QLabel(
            "Furigana for karaoke lines is edited in the Karaoke tab. "
            "Existing readings are preserved when karaoke is created or changed."
        )
        self.furigana_karaoke_notice.setWordWrap(True)
        self.furigana_karaoke_notice.setStyleSheet("color: #ffcf70;")
        self.furigana_karaoke_notice.setVisible(False)
        furigana_layout.addWidget(self.furigana_karaoke_notice)
        self.furigana_table = QTableWidget(0, 2)
        self.furigana_table.setHorizontalHeaderLabels(["Text", "Reading"])
        furigana_header = self.furigana_table.horizontalHeader()
        furigana_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        furigana_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.furigana_table.itemChanged.connect(self.furigana_changed)
        furigana_layout.addWidget(self.furigana_table)
        furigana_buttons = QHBoxLayout()
        reset_furigana = QPushButton("Reset Readings")
        self.reset_furigana_button = reset_furigana
        reset_furigana.setToolTip("Remove all explicit readings from every lyric and return to automatic generation.")
        reset_furigana.clicked.connect(self.reset_all_furigana)
        furigana_buttons.addWidget(reset_furigana)
        furigana_buttons.addStretch()
        furigana_layout.addLayout(furigana_buttons)
        detail_layout.addWidget(furigana_box)

        line_buttons = QHBoxLayout()
        add_button = QPushButton("+ Add Line")
        add_button.clicked.connect(self.add_line)
        delete_button = QPushButton("Delete Line")
        delete_button.clicked.connect(self.delete_line)
        up_button = QPushButton("Move Up")
        up_button.clicked.connect(lambda: self.move_line(-1))
        down_button = QPushButton("Move Down")
        down_button.clicked.connect(lambda: self.move_line(1))
        for button in (add_button, delete_button, up_button, down_button):
            line_buttons.addWidget(button)
        line_buttons.addStretch()
        detail_layout.addLayout(line_buttons)
        lyrics_layout.addWidget(detail, 1)
        self.tabs.addTab(lyrics_page, "Lyrics")

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

            sweep_controls = QHBoxLayout()
            self.karaoke_sweep_style_button = QPushButton("Properties")
            self.karaoke_sweep_style_button.setToolTip(
                "Configure karaoke rendering properties."
            )
            self.karaoke_sweep_style_button.clicked.connect(
                self._show_karaoke_properties
            )
            sweep_controls.addWidget(self.karaoke_sweep_style_button)
            sweep_controls.addStretch()
            karaoke_layout.addLayout(sweep_controls)

            # Karaoke gets the same transport choice as the Lyrics tab: a
            # compact seekbar for ordinary playback, or a waveform for visual
            # timing work.
            karaoke_transport_controls = QHBoxLayout()
            karaoke_transport_controls.addStretch()
            self.karaoke_transport_toggle = QPushButton("Waveform")
            self.karaoke_transport_toggle.setCheckable(True)
            self.karaoke_transport_toggle.setToolTip(
                "Toggle between the normal seekbar and the audio waveform."
            )
            self.karaoke_transport_toggle.setEnabled(ffmpeg_available)
            if not ffmpeg_available:
                self.karaoke_transport_toggle.setToolTip(
                    "Waveform is unavailable because ffmpeg was not found."
                )
            self.karaoke_transport_toggle.toggled.connect(self._toggle_karaoke_transport)
            karaoke_transport_controls.addWidget(self.karaoke_transport_toggle)
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

            self.karaoke_waveform = WaveformWidget()
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

            preview_controls = QHBoxLayout()
            self.preview_position_label = QLabel("00:00.000")
            self.preview_duration_label = QLabel("00:00.000")
            self.preview_back_button = QPushButton("<< 5s")
            self.preview_play_button = QPushButton("Play")
            self.preview_forward_button = QPushButton("5s >>")
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
            self.full_line_label = QLabel("")
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
            self.separation_preview = QLabel("")
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
        source_layout.addWidget(QLabel("Edit raw syntax here. Source edits are applied when leaving this tab or saving."))
        self.source_format_label = QLabel()
        source_layout.addWidget(self.source_format_label)
        self.source_editor = QTextEdit()
        self.source_editor.setAcceptRichText(False)
        self.source_editor.textChanged.connect(self._source_text_changed)
        source_layout.addWidget(self.source_editor, 1)
        source_buttons = QHBoxLayout()
        sync_source = QPushButton("Refresh Source")
        sync_source.clicked.connect(self.sync_source_from_model)
        reload_source = QPushButton("Reload from Source")
        reload_source.clicked.connect(self.reload_model_from_source)
        open_source_file = QPushButton("Open File")
        open_source_file.setToolTip("Open this lyrics file with your system's default application.")
        open_source_file.clicked.connect(self.open_lyrics_file)
        source_buttons.addWidget(sync_source)
        source_buttons.addWidget(reload_source)
        source_buttons.addWidget(open_source_file)
        source_buttons.addStretch()
        source_layout.addLayout(source_buttons)
        source_tools = QHBoxLayout()
        import_ass = QPushButton("Import ASS…")
        import_ass.setToolTip(
            "Import ASS/SSA Dialogue lines and convert \\k, \\K, \\kf and \\ko karaoke timing into LRCX."
        )
        import_ass.clicked.connect(self.import_ass)
        source_tools.addWidget(import_ass)
        source_tools.addStretch()
        source_layout.addLayout(source_tools)
        self.tabs.addTab(source_page, "Source")

        # ----------------------------- Share --------------------------
        self.share_page = QWidget()
        share_layout = QVBoxLayout(self.share_page)
        share_layout.addWidget(QLabel("Copy or export the current lyrics as text."))

        share_format_row = QHBoxLayout()
        share_format_row.addWidget(QLabel("Format:"))
        self.share_format_combo = QComboBox()
        self.share_format_combo.addItem("Plain text", "text")
        self.share_format_combo.addItem("With line timestamps", "lrc")
        share_format_row.addWidget(self.share_format_combo)
        share_format_row.addStretch()
        share_layout.addLayout(share_format_row)

        share_actions = QHBoxLayout()
        copy_share_button = QPushButton("Copy")
        copy_share_button.clicked.connect(self.copy_share_text)
        export_share_button = QPushButton("Export…")
        export_share_button.clicked.connect(self.export_share_text)
        share_actions.addWidget(copy_share_button)
        share_actions.addWidget(export_share_button)
        share_actions.addStretch()
        share_layout.addLayout(share_actions)

        self.share_status_label = QLabel("")
        share_layout.addWidget(self.share_status_label)
        self.share_selection_label = QLabel("")
        share_layout.addWidget(self.share_selection_label)
        share_layout.addStretch()
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

        self.song_association_status = QLabel()
        self.song_association_status.setWordWrap(True)
        association_layout.addWidget(self.song_association_status)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Associated Song:"))
        self.song_association_label = QLabel()
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

        readings_frame = QFrame()
        readings_frame.setFrameShape(QFrame.Shape.StyledPanel)
        readings_layout = QVBoxLayout(readings_frame)
        readings_title = QLabel("Readings")
        readings_font = readings_title.font()
        readings_font.setBold(True)
        readings_title.setFont(readings_font)
        readings_layout.addWidget(readings_title)
        reset_all_readings = QPushButton("Reset All Readings")
        reset_all_readings.setToolTip("Remove every saved/manual/generated reading and return to automatic generation.")
        reset_all_readings.clicked.connect(self.reset_all_readings_advanced)
        readings_layout.addWidget(reset_all_readings)
        advanced_layout.addWidget(readings_frame)
        advanced_layout.addStretch()
        self.tabs.addTab(advanced_page, "Advanced")
        self._update_song_association_ui()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.sync_source_from_model()

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

        workspace = QSplitter(Qt.Orientation.Horizontal)
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
            self.karaoke_renderer_preview.set_karaoke_sweep_style(
                self.karaoke_sweep_style
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

        # Keep Reset Karaoke available only while actually editing karaoke.
        self.reset_karaoke_button = None
        self.karaoke_shortcuts_button = None
        if self.karaoke_enabled:
            self.karaoke_shortcuts_button = QPushButton("Shortcuts")
            self.karaoke_shortcuts_button.setToolTip(
                "Show karaoke keyboard shortcuts."
            )
            self.karaoke_shortcuts_button.clicked.connect(
                self.show_karaoke_shortcuts
            )

            self.reset_karaoke_button = QPushButton("Reset Karaoke")
            self.reset_karaoke_button.setToolTip(
                "Clear all karaoke timing and instant-fill flags while keeping lyrics and ruby."
            )
            self.reset_karaoke_button.clicked.connect(self.clear_all_karaoke)

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
        if self.karaoke_shortcuts_button is not None:
            buttons.addWidget(self.karaoke_shortcuts_button)
        if self.reset_karaoke_button is not None:
            buttons.addWidget(self.reset_karaoke_button)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.redo_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.save_close_button)
        buttons.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(workspace, 1)
        layout.addLayout(buttons)

        workspace.setSizes([250, 900, 360])
        self._tab_changed(self.tabs.currentIndex())

