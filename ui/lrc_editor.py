"""Structured lyrics editor, line editing and reading generation."""

from pathlib import Path
from bisect import bisect_right
import copy
import re
import threading

from PySide6.QtCore import Qt, QSignalBlocker, Signal, QTimer, QItemSelectionModel
from PySide6.QtGui import QKeySequence, QShortcut, QColor, QBrush
from PySide6.QtWidgets import (
    QDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QCheckBox,
    QApplication,
)

from lyrics.models import LyricLine, LyricSegment
from lyrics.line_breaks import (
    decode_line_breaks,
    encode_line_breaks,
    reading_source,
    original_reading_text,
)
from lyrics.formatting import format_emphasis, parse_emphasis, parse_quote_prefix
from lyrics.writer import format_mora_reading, format_timestamp
from lyrics.timing import parse_timestamp
from lyrics.karaoke_timing import next_lyric_start, segment_end
from lyrics.editor_operations import correct_line_text, replace_reading_span
from lyrics.editor_recovery import new_draft_recovery_path, recoverable_drafts
from lyrics.reading_groups import group_reading_tokens, remap_reading_groups
from lyrics.reading_warnings import missing_english_readings, unconverted_words_message
from lyrics.furigana import get_cached_furigana_segments
from core.debug import debug_print
from core.fonts import content_font
from lyrics.lrcx_parser import get_lrcx_offset
from lyrics.waveform import WaveformWidget
from core.settings import get_playback_settings

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


from ui.lrc_editor_ui import LrcEditorUiMixin
from ui.lyrics_timing_tools import LyricsTimingToolsMixin
from ui.lyrics_editor_search import LyricsEditorSearchMixin
from ui.karaoke_editor_tools import KaraokeEditorToolsMixin
from ui.lyrics_editor_document import LyricsEditorDocumentMixin
from ui.lyrics_editor_playback import LyricsEditorPlaybackMixin
from ui.lyrics_editor_karaoke import LyricsEditorKaraokeMixin
from ui.lyrics_editor_operations import LyricsEditorOperationsMixin
from ui.lyrics_editor_preview import LyricsEditorPreviewMixin
from ui.lyrics_editor_layout import LyricsEditorLayoutMixin


class LrcEditorDialog(
    LyricsEditorLayoutMixin,
    LyricsEditorDocumentMixin,
    LyricsEditorPreviewMixin,
    LyricsEditorPlaybackMixin,
    LyricsEditorKaraokeMixin,
    LyricsEditorOperationsMixin,
    KaraokeEditorToolsMixin,
    LyricsEditorSearchMixin,
    LyricsTimingToolsMixin,
    LrcEditorUiMixin,
    QDialog,
):
    # Emitted from background reading-generation workers. Qt delivers this
    # back to the dialog thread, so widget updates never happen off-thread.
    _reading_generation_finished = Signal(int, int, bool)
    _parse_time = staticmethod(parse_timestamp)

    """Lyrics Editor 2.0.

    The primary editor is structured: lyric lines are objects with a timestamp,
    text and optional LRCX-only segment/ruby/karaoke data. The raw source view
    is retained as an advanced escape hatch rather than being the whole editor.
    """

    def __init__(self, lrc_path=None, parent=None, *, new_document=False):
        super().__init__(parent)
        self.player = parent
        self.lrc_path = None
        self.result_path = None
        self.document_dirty = False
        self._closed = False
        self._choosing_document = lrc_path is None
        self._initialize_editor_layout()
        if self._choosing_document:
            self.setWindowTitle("Lyrics Editor 4.0")
            self._build_document_start_page()
            return
        self._initialize_document(lrc_path, new_document=new_document)

    def _initialize_document(self, lrc_path, *, new_document=False, draft_path=None):
        parent = self.player
        if self._choosing_document:
            self.layout().removeWidget(self.document_start_page)
            self.layout().setContentsMargins(11, 11, 11, 11)
            self.document_start_page.hide()
            self.document_start_page.deleteLater()
            self._choosing_document = False

        # Smooth display clock: QMediaPlayer positionChanged is coarse on some
        # backends, so interpolate only the precise time display at 30 Hz.
        self._display_position_anchor_ms = 0.0
        self._display_position_anchor_monotonic = None
        self._display_position_playing = False
        self._precise_display_timer = QTimer(self)
        self._precise_display_timer.setTimerType(Qt.TimerType.PreciseTimer)
        precise_timestamp_fps = get_playback_settings().get(
            "precise_timestamp_fps", 30
        )
        self._precise_display_timer.setInterval(
            max(1, round(1000 / max(1, int(precise_timestamp_fps))))
        )
        self._precise_display_timer.timeout.connect(
            self._update_precise_playback_display
        )

        self.lrc_path = Path(lrc_path)
        # A draft has a save target but only writes to its recovery copy until Save.
        self.is_new_document = new_document or not self.lrc_path.exists()
        self._confirm_draft_replace = draft_path is not None
        self.result_path = None if self.is_new_document else self.lrc_path
        self.attached_song_path = (
            self._read_attached_song_path(self.lrc_path)
            if not self.is_new_document
            else None
        )
        self.player = parent
        self._editing_song_path = self._normalise_song_path(
            getattr(parent, "current_audio_path", None)
        )
        self.karaoke_enabled = bool(
            parent is not None
            and getattr(parent, "plugin_manager", None) is not None
            and "karaoke" in parent.plugin_manager.plugins
        )
        self.lines = []
        self.loading = False
        self.karaoke_dirty = False
        self.document_dirty = False
        self._source_dirty = False
        self._syncing_source = False
        self._source_suffix = self.lrc_path.suffix.lower()
        if self._source_suffix not in {".lrc", ".lrcx", ".txt"}:
            self._source_suffix = ".lrcx"
        self._pending_recovered_source = None
        self._document_metadata = []
        self._closed = False
        self._saved_document_state = None
        self._generation_mode = get_reading_mode() or "auto"
        self.preview_wants_playing = False
        self.current_playback_line = -1
        self.sidebar_sort_mode = "source"
        self._view_line_indices = []
        self._share_checked_line_ids = set()
        self._share_selection_active = False
        self._playback_line_starts = []
        self._playback_line_rows = []
        self._preview_segments_line_index = None
        self._waveform_drag_row = -1
        self._segment_preview_range = None
        self._segment_preview_pause_ms = None
        self._segment_preview_timer = QTimer(self)
        self._segment_preview_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._segment_preview_timer.setSingleShot(True)
        self._segment_preview_timer.timeout.connect(self._check_segment_preview_end)
        # Cache expensive full-document language detection until lyric text changes.
        self._detected_language_cache_key = None
        self._detected_language_cache_value = None
        # Reading providers can be computationally expensive (notably
        # PyCantonese). Keep generation off the Qt UI thread.
        self._reading_generation_epoch = 0
        self._reading_generation_finished.connect(
            self._on_reading_generation_finished
        )
        self._line_reading_timer = QTimer(self)
        self._line_reading_timer.setSingleShot(True)
        self._line_reading_timer.setInterval(200)
        self._line_reading_timer.timeout.connect(
            lambda: self._refresh_auto_readings_for_line(self._current_line_index())
        )
        self.lyrics_offset_ms = (
            get_lrcx_offset(self.lrc_path)
            if not new_document and self.lrc_path.suffix.lower() == ".lrcx"
            else 0.0
        )

        # Model checkpoints support all editor tools; ordinary typing stores
        # only its changed line, while bulk operations keep a full snapshot.
        self._undo_stack = []
        self._redo_stack = []
        self._history_limit = 100
        self._history_restoring = False

        sidecar = self.lrc_path.with_name(f".{self.lrc_path.name}.autosave")
        self._autosave_path = new_draft_recovery_path(self.lrc_path) if self.is_new_document else sidecar
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.timeout.connect(self._autosave_if_needed)


        if self.is_new_document:
            recovery_path = draft_path
            if recovery_path is None:
                drafts = recoverable_drafts(self.lrc_path)
                if drafts:
                    recovery_path = drafts[0][1]
                elif not self.lrc_path.exists():
                    recovery_path = sidecar
        else:
            self._load_document()
            recovery_path = sidecar
        if recovery_path is not None:
            self._restore_autosave_if_available(recovery_path, confirmed=draft_path is not None)
        self._update_document_title()
        # Catch key events from transient QTableWidget/QSpinBox editors too.
        # The filter itself is tightly scoped to this dialog's editor controls.
        QApplication.instance().installEventFilter(self)

        self._build_ui()
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        if self._pending_recovered_source is not None:
            text, suffix = self._pending_recovered_source
            blocker = QSignalBlocker(self.source_editor)
            self.source_editor.setPlainText(text)
            del blocker
            self._source_suffix = suffix
            self.source_editor.set_source_format(suffix)
            self._source_dirty = True
            self.source_format_label.setText(f"{suffix[1:].upper()} source")
            self.tabs.setCurrentWidget(self.source_editor.parentWidget())
        # Reading providers are plugins. Defer automatic provider generation until the
        # event loop starts so the plugin manager has a chance to register
        # Koroman before we ask build_furigana_segments() for Korean readings.
        self._schedule_auto_reading_generation()
        self._waveform_lyric_preview_handle = None
        self._waveform_lyric_preview_ms = None
        self._populate_lines()
        self._autosave_timer.start()
        if self.lines:
            self.line_list.setCurrentRow(0)

        # Opening/selecting a line only populates widgets. Treat the resulting
        # model as the clean baseline unless crash recovery actually restored
        # unsaved edits.
        if not self.document_dirty:
            self.karaoke_dirty = any(
                getattr(line, "has_karaoke", False) for line in self.lines
            )
        self._reset_history_baseline()
        self._update_save_button_state()
        self._refresh_lyrics_waveform()

        if self.player is not None and hasattr(self.player, "media_player"):
            player = self.player.media_player
            player.positionChanged.connect(self.preview_position_changed)
            player.durationChanged.connect(self.preview_duration_changed)
            player.playbackStateChanged.connect(self.preview_playback_state_changed)
            player.playbackStateChanged.connect(
                self._update_precise_display_playback_state
            )
            player.playbackRateChanged.connect(self._segment_preview_rate_changed)
            self.preview_position_changed(player.position())
            self.preview_duration_changed(player.duration())
            self.preview_playback_state_changed(player.playbackState())
            self._update_precise_display_playback_state(player.playbackState())

        # QTableWidget starts editing a cell when Space is pressed. In the
        # karaoke timing grid Space is reserved for playback, so intercept it
        # before the item view can enter edit mode.
        if self.karaoke_enabled:
            self.segment_table.installEventFilter(self)
            self.segment_table.viewport().installEventFilter(self)
        self.shortcut_undo = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.shortcut_undo.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.shortcut_undo.activated.connect(self.undo)
        self.shortcut_redo = QShortcut(QKeySequence.StandardKey.Redo, self)
        self.shortcut_redo.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.shortcut_redo.activated.connect(self.redo)
        self.shortcut_save = QShortcut(QKeySequence.StandardKey.Save, self)
        self.shortcut_save.activated.connect(self.save_document)
        self._setup_lyrics_shortcuts()


    # ------------------------------------------------------------------
    # Reading generation and structured lyric views
    # ------------------------------------------------------------------


    def _current_generation_mode(self):
        if hasattr(self, "_generation_mode"):
            return self._generation_mode
        if hasattr(self, "reading_mode_combo"):
            return self.reading_mode_combo.currentData() or "auto"
        return get_reading_mode() or "auto"

    def _apply_generation_mode(self, mode):
        if not mode:
            return
        self._generation_mode = mode
        set_reading_mode(mode)
        if hasattr(self, "reading_mode_combo"):
            combo_index = self.reading_mode_combo.findData(mode)
            if combo_index >= 0:
                blocker = QSignalBlocker(self.reading_mode_combo)
                try:
                    self.reading_mode_combo.setCurrentIndex(combo_index)
                finally:
                    del blocker


    def _update_reading_mode_info(self):
        if not hasattr(self, "reading_mode_info"):
            return
        available = set()
        try:
            if available_reading_modes is not None:
                available = {str(mode).lower() for mode in available_reading_modes()}
        except Exception:
            available = set()

        lines = [
            "Auto: detect the appropriate generator.",
            "Furigana: Japanese readings.",
        ]
        if "pinyin" in available:
            lines.append("Pinyin: Mandarin pronunciation.")
        if "jyutping" in available:
            lines.append("Jyutping: Cantonese pronunciation.")
        if "yale" in available:
            lines.append("Yale: Cantonese Yale romanization.")
        if "english_katakana" in available:
            lines.append("English → Katakana: offline dictionary readings; select explicitly. Unknown words stay unannotated.")
        self.reading_mode_info.setText("\n".join(lines))

    def _tab_changed(self, index):
        # Source is intentionally a raw full-width workspace.
        source_index = self.tabs.indexOf(self.source_editor.parentWidget())
        if index != source_index and self._source_dirty:
            if not self._commit_pending_source():
                blocker = QSignalBlocker(self.tabs)
                self.tabs.setCurrentIndex(source_index)
                del blocker
                return
        elif index == source_index and not self._source_dirty:
            self.sync_source_from_model()
        if index != source_index and not self.source_editor.document().isEmpty():
            # Pending edits have been committed above. The model and document
            # history now own the content; release hidden Qt blocks/syntax runs.
            blocker = QSignalBlocker(self.source_editor)
            self.source_editor.clear()
            del blocker
        self._remember_editor_panel_widths()
        # parentWidget() is the source page in this layout.
        # Source and Translations have their own document navigation.
        full_width = index == source_index or (
            self.translation_page is not None and index == self.tabs.indexOf(self.translation_page)
        )
        panel = getattr(self, "line_list_panel", None)
        if panel is not None:
            panel.setVisible(not full_width)
        else:
            self.line_list.setVisible(not full_width)
        self._set_share_selection_active(
            index == self.tabs.indexOf(self.share_page)
        )
        on_translations = self.translation_page is not None and index == self.tabs.indexOf(self.translation_page)
        if hasattr(self, "quota_button"):
            self.quota_button.setVisible(on_translations)
        if on_translations:
            self._ensure_translation_panel()
        self._update_tab_specific_controls()
        preview = self.lyrics_renderer_preview
        if self.tabs.currentWidget() is self.lyrics_page:
            self._refresh_lyrics_preview()
        else:
            preview.stop_workers()
            if preview.lyric:
                preview.set_lyric("", generate=False)
            self._lyrics_preview_line_index = None
        self._update_history_actions()
        if hasattr(self, "preview_tools"):
            on_karaoke = self.karaoke_enabled and self.tabs.currentWidget() is self.karaoke_scroll
            self.preview_tools.setVisible(self.tabs.currentWidget() is self.lyrics_page or on_karaoke)
            self.preview_selection_button.setText("Preview segment" if on_karaoke else "Preview line")
        self._restore_editor_panel_widths()

    def _set_share_selection_active(self, active):
        active = bool(active)
        self._share_selection_active = active
        self.share_selection_controls.setVisible(active)
        blocker = QSignalBlocker(self.line_list)
        for row in range(self.line_list.count()):
            item = self.line_list.item(row)
            flags = item.flags()
            if active:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                line_index = item.data(Qt.ItemDataRole.UserRole)
                is_checked = (
                    isinstance(line_index, int)
                    and 0 <= line_index < len(self.lines)
                    and id(self.lines[line_index]) in self._share_checked_line_ids
                )
                item.setCheckState(
                    Qt.CheckState.Checked
                    if is_checked
                    else Qt.CheckState.Unchecked
                )
            else:
                line_index = item.data(Qt.ItemDataRole.UserRole)
                if (
                    item.checkState() == Qt.CheckState.Checked
                    and isinstance(line_index, int)
                    and 0 <= line_index < len(self.lines)
                ):
                    self._share_checked_line_ids.add(
                        id(self.lines[line_index])
                    )
                item.setData(Qt.ItemDataRole.CheckStateRole, None)
                flags &= ~Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
        del blocker
        if active:
            self._update_share_selection_status()

    def _update_tab_specific_controls(self):
        self._update_line_selection_controls()
        if not self.karaoke_enabled:
            return
        karaoke_index = self.tabs.indexOf(self.karaoke_scroll)
        on_karaoke_tab = self.tabs.currentIndex() == karaoke_index

        if self.karaoke_shortcuts_button is not None:
            self.karaoke_shortcuts_button.setVisible(on_karaoke_tab)
        if self.reset_karaoke_button is not None:
            self.reset_karaoke_button.setVisible(on_karaoke_tab)

        # The separate preview behaves like a right-side inspector: visible
        # only when it is useful, and hidden on Lyrics/Source for more room.
        if self.karaoke_preview_panel is not None:
            self.karaoke_preview_panel.setVisible(on_karaoke_tab)
            if on_karaoke_tab:
                self._refresh_karaoke_preview()
            else:
                self.karaoke_renderer_preview.stop_workers()
                if self.karaoke_renderer_preview.lyric:
                    self.karaoke_renderer_preview.set_lyric("", generate=False)
                self._preview_segments_line_index = None

    def _populate_lines(self, preserve_line_index=None, selected_line_indices=None):
        """Refresh the sidebar as a view of canonical document order.

        Sorting changes only the list presentation. self.lines is never sorted.
        """
        if selected_line_indices is None:
            selected_line_indices = self._selected_line_indices()
        # Every editable Karaoke row must refer to a canonical model segment,
        # including plain LRC/TXT lines that arrive without any segments.
        if self.karaoke_enabled:
            for line in self.lines:
                if not line.segments:
                    line.segments = [LyricSegment(line.start, line.text)]
        timeline = sorted(enumerate(self.lines), key=lambda item: (item[1].start, item[0]))
        self._playback_line_starts = [line.start for _, line in timeline]
        self._playback_line_rows = [row for row, _ in timeline]
        self._preview_segments_line_index = None

        if preserve_line_index is None:
            preserve_line_index = self._current_line_index()

        indexed = list(enumerate(self.lines))
        if self.sidebar_sort_mode == "timestamp_asc":
            indexed.sort(key=lambda item: (item[1].start, item[0]))
        elif self.sidebar_sort_mode == "timestamp_desc":
            indexed.sort(key=lambda item: (item[1].start, item[0]), reverse=True)

        self._view_line_indices = [index for index, _ in indexed]
        current_line_ids = {id(line) for line in self.lines}
        self._share_checked_line_ids.intersection_update(current_line_ids)

        blocker = QSignalBlocker(self.line_list)
        self.line_list.clear()
        for line_index, line in indexed:
            item = QListWidgetItem(f"{format_timestamp(line.start)}  {line.text}")
            item.setData(Qt.ItemDataRole.UserRole, line_index)
            if self._share_selection_active:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if id(line) in self._share_checked_line_ids
                    else Qt.CheckState.Unchecked
                )
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.line_list.addItem(item)
        del blocker

        if preserve_line_index is not None and len(selected_line_indices) <= 1:
            selected_line_indices = [preserve_line_index]
        self._set_line_selection(selected_line_indices or (
            [preserve_line_index] if preserve_line_index is not None else []), preserve_line_index)

        self._update_share_selection_status()
        self._refresh_lyrics_waveform_markers()
        self._update_lyric_search()
        self._refresh_inline_timing_issues()

    def _checked_share_line_indices(self):
        return {
            int(self.line_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.line_list.count())
            if self.line_list.item(row).checkState() == Qt.CheckState.Checked
        }

    @staticmethod
    def _lines_for_share(lines, selected_indices):
        selected_indices = set(selected_indices or ())
        return [
            line
            for index, line in enumerate(lines)
            if index in selected_indices
        ]

    def _update_share_selection_status(self):
        label = getattr(self, "share_selection_label", None)
        if label is None:
            return
        selected_count = len(self._checked_share_line_indices())
        if selected_count:
            label.setText(
                f"Sharing {selected_count} checked line(s) only."
            )
        else:
            label.setText("No lyrics selected. Check lines to share, or use Select All.")
        card = self._share_card
        if card is not None and self._share_selection_active:
            card.schedule_refresh()

    def _share_line_checks_changed(self, _item):
        self._share_checked_line_ids = {
            id(self.lines[line_index])
            for line_index in self._checked_share_line_indices()
            if 0 <= line_index < len(self.lines)
        }
        self._update_share_selection_status()

    def select_all_share_lines(self):
        for row in range(self.line_list.count()):
            self.line_list.item(row).setCheckState(Qt.CheckState.Checked)

    def clear_share_line_selection(self):
        for row in range(self.line_list.count()):
            self.line_list.item(row).setCheckState(Qt.CheckState.Unchecked)

    def sidebar_sort_changed(self, _index):
        current = self._current_line_index()
        self.sidebar_sort_mode = self.sidebar_sort_combo.currentData() or "source"
        self._populate_lines(preserve_line_index=current)
        if self.line_list.currentRow() >= 0:
            self.load_selected_line(self.line_list.currentRow())

    def _current_line_index(self):
        item = self.line_list.currentItem() if hasattr(self, "line_list") else None
        if item is None:
            return None
        try:
            index = int(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return None
        return index if 0 <= index < len(self.lines) else None

    def _select_line_index(self, line_index, preserve_selection=False):
        try:
            view_row = self._view_line_indices.index(int(line_index))
        except (ValueError, TypeError):
            return
        if preserve_selection:
            self.line_list.setCurrentRow(view_row, QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            self.line_list.setCurrentRow(view_row, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _update_playback_line_style(self, position_ms):
        """Bold only the playback row that actually changed."""
        # TXT lines receive synthetic starts only for editor ordering. They
        # are not real timestamps, so playback must not automatically make the
        # first line look active.
        if self.lrc_path.suffix.lower() == ".txt":
            previous = getattr(self, "current_playback_line", -1)
            if 0 <= previous < self.line_list.count():
                item = self.line_list.item(previous)
                if item is not None:
                    font = item.font()
                    if font.bold():
                        font.setBold(False)
                        item.setFont(font)
            self.current_playback_line = -1
            return

        if not self.lines:
            previous = getattr(self, "current_playback_line", -1)
            if 0 <= previous < self.line_list.count():
                item = self.line_list.item(previous)
                if item is not None:
                    font = item.font()
                    if font.bold():
                        font.setBold(False)
                        item.setFont(font)
            self.current_playback_line = -1
            return

        starts = self._playback_line_starts
        rows = self._playback_line_rows
        if len(starts) != len(self.lines) or len(rows) != len(self.lines):
            timeline = sorted(enumerate(self.lines), key=lambda item: (item[1].start, item[0]))
            starts = [line.start for _, line in timeline]
            rows = [row for row, _ in timeline]
            self._playback_line_starts = starts
            self._playback_line_rows = rows

        position = self._audio_to_lyric_seconds(position_ms)
        timeline_index = bisect_right(starts, position) - 1
        if timeline_index >= 0:
            occurrence_start = starts[timeline_index]
            while (
                timeline_index > 0
                and starts[timeline_index - 1] == occurrence_start
            ):
                timeline_index -= 1
        current = rows[timeline_index] if timeline_index >= 0 else -1
        previous = getattr(self, "current_playback_line", -1)

        if current == previous:
            return

        if 0 <= previous < self.line_list.count():
            item = self.line_list.item(previous)
            if item is not None:
                font = item.font()
                if font.bold():
                    font.setBold(False)
                    item.setFont(font)

        try:
            current_view_row = self._view_line_indices.index(current)
        except ValueError:
            current_view_row = -1

        if 0 <= current_view_row < self.line_list.count():
            item = self.line_list.item(current_view_row)
            if item is not None:
                font = item.font()
                if not font.bold():
                    font.setBold(True)
                    item.setFont(font)

        self.current_playback_line = current

    def go_to_current_lyric(self):
        """Jump the editor selection to the lyric currently reached by playback."""
        row = getattr(self, "current_playback_line", -1)

        # If the playback callback has not fired yet, calculate it directly.
        if row < 0 and self.player is not None and hasattr(self.player, "media_player"):
            position = self._audio_to_lyric_seconds(self.player.media_player.position())
            starts = self._playback_line_starts
            rows = self._playback_line_rows
            if len(starts) != len(self.lines) or len(rows) != len(self.lines):
                timeline = sorted(enumerate(self.lines), key=lambda item: (item[1].start, item[0]))
                starts = [line.start for _, line in timeline]
                rows = [row for row, _ in timeline]
                self._playback_line_starts = starts
                self._playback_line_rows = rows
            timeline_index = bisect_right(starts, position) - 1
            if timeline_index >= 0:
                occurrence_start = starts[timeline_index]
                while (
                    timeline_index > 0
                    and starts[timeline_index - 1] == occurrence_start
                ):
                    timeline_index -= 1
            row = rows[timeline_index] if timeline_index >= 0 else -1

        self.go_to_lyric_line(row)

    def go_to_lyric_line(self, line_index):
        """Select a source line and bring its sidebar entry into view."""
        if not 0 <= line_index < len(self.lines):
            return
        try:
            view_row = self._view_line_indices.index(line_index)
        except ValueError:
            return

        self.line_list.setCurrentRow(view_row)
        item = self.line_list.item(view_row)
        if item is not None:
            self.line_list.scrollToItem(
                item,
                QListWidget.ScrollHint.PositionAtCenter,
            )

    def _current_line(self):
        index = self._current_line_index()
        if index is not None:
            return index, self.lines[index]
        return None, None

    def _start_background_reading_generation(self, texts, line_index=-1):
        """Warm reading-provider caches without blocking the editor UI."""
        if self._closed or build_furigana_segments is None:
            return

        texts = [str(text or "") for text in texts if str(text or "")]
        if not texts:
            return

        # Requests for individual lines and whole songs may coexist.  Only a
        # generation-mode change invalidates an existing worker batch.
        token = self._reading_generation_epoch

        def worker():
            changed = False
            try:
                if line_index < 0:
                    # Whole-song prefetch can use a provider's batch generator.
                    # PyCantonese therefore tokenizes the collection once instead
                    # of starting its heavy word-segmentation machinery per line.
                    prefetch_furigana_batches(
                        texts,
                        should_continue=lambda: token == self._reading_generation_epoch,
                    )
                    if token != self._reading_generation_epoch:
                        return
                    changed = True
                else:
                    tokens = build_furigana_segments(texts[0])
                    changed = any(part.get("reading") for part in tokens)
            except Exception as error:
                debug_print(f"[Readings] Background generation failed: {error!r}")
            if self._closed or token != self._reading_generation_epoch:
                return
            try:
                self._reading_generation_finished.emit(token, line_index, changed)
            except RuntimeError:
                # The dialog can be deleted between the guard and this emit.
                pass

        threading.Thread(
            target=worker,
            name="LRCPlusReadingGeneration",
            daemon=True,
        ).start()

    def _on_reading_generation_finished(self, token, line_index, changed):
        """Apply background-generation results on the Qt main thread."""
        if self._closed or token != self._reading_generation_epoch:
            return
        if not changed:
            return

        if self.readings_editor.is_editing():
            # A provider finishing must not replace a field the user is typing
            # into. Retry against this selection after the edit has settled.
            selected = self._current_line_index()
            if line_index < 0 or selected == line_index:
                QTimer.singleShot(200, lambda: self._on_reading_generation_finished(token, selected, changed))
            return

        if line_index >= 0:
            # Only refresh if this is still the line the user is looking at.
            if self._current_line_index() != line_index:
                return
            line = self.lines[line_index]
            self._refresh_ruby_preview(line)
            self._populate_furigana_editor(line)
            return

        current = self._current_line_index()
        self._populate_lines(preserve_line_index=current)
        if current is not None:
            self._select_line_index(current)
            self.load_selected_line(self.line_list.currentRow())

    def _refresh_auto_readings_for_line(self, line_index):
        """Warm the selected line asynchronously instead of freezing the UI."""
        if self._closed:
            return
        if line_index is None or not (0 <= line_index < len(self.lines)):
            return
        if self._current_line_index() != line_index:
            return

        line = self.lines[line_index]
        text = str(line.text or "")
        if text and get_cached_furigana_segments(reading_source(text, line.translation_lines)) is None:
            self._start_background_reading_generation([reading_source(text, line.translation_lines)], line_index)

    def load_selected_line(self, row):
        self._karaoke_bounds_warning = None
        if 0 <= row < len(self._view_line_indices):
            row = self._view_line_indices[row]
        else:
            row = -1
        self.insert_newline_button.setEnabled(0 <= row < len(self.lines))
        self.insert_translation_button.setEnabled(0 <= row < len(self.lines))
        for button in self.lyric_style_buttons:
            button.setEnabled(0 <= row < len(self.lines))
        self.loading = True
        if self.karaoke_enabled:
            self.segment_table.setRowCount(0)
            self.full_line_label.setText("")
            self.separation_preview.clear()
            self._clear_separator_buttons()
        if not (0 <= row < len(self.lines)):
            self.quote_button.setChecked(False)
            self.line_time.setValue(0)
            self.line_text.clear()
            self.readings_editor.set_tokens([])
            self._refresh_lyrics_preview()
            self._update_furigana_editor_availability(None)
            self._update_text_timing_notice(None)
            if hasattr(self, "lyrics_waveform"):
                self.lyrics_waveform.clear_selected_lyric_timing()
            self.loading = False
            return
        line = self.lines[row]
        self._update_text_timing_notice(line)
        self._update_line_selection_controls()
        self._update_detected_language()
        self.line_time.setValue(line.start)
        markup = encode_line_breaks(format_emphasis(line.text, line.style_spans), line.translation_lines)
        self.line_text.setPlainText(("> " if line.is_blockquote else "") + markup)
        self.quote_button.setChecked(line.is_blockquote)
        if self.karaoke_enabled:
            self.full_line_label.setText(line.text)
            self._build_separator_buttons(line)
        self._refresh_ruby_preview(line)
        self._populate_furigana_editor(line)
        self._refresh_selected_lyric_waveform_boundary()
        if self.karaoke_enabled:
            self._populate_segments(line)
            self._update_overlap_warning(line)
            self._refresh_karaoke_waveform_editor()
        self.loading = False
        self._update_lyric_search()
        if self.karaoke_enabled:
            self.refresh_separation_preview()

        # Run reading providers once the selection has settled. This is important
        # for optional providers such as Korean Romanization: opening the song
        # may work, but switching away and back must also regenerate/restore
        # the selected line's readings.
        selected_line_index = row
        QTimer.singleShot(
            0,
            lambda index=selected_line_index: self._refresh_auto_readings_for_line(index),
        )

    def _populate_segments(self, line):
        def make_item(text):
            value = QTableWidgetItem(text)
            value.setFont(content_font(self.segment_table.font(), text))
            return value

        previous_loading = self.loading
        self.loading = True
        blocker = QSignalBlocker(self.segment_table)
        try:
            if not line.segments:
                line.segments = [LyricSegment(line.start, line.text)]
            segments = line.segments
            fallback_end = next_lyric_start(self.lines, line)
            self.segment_table.setRowCount(len(segments))
            for row, segment in enumerate(segments):
                self.segment_table.setItem(
                    row, 0, make_item(format_timestamp(segment.start))
                )
                end_time = segment_end(line, row, fallback_end)
                self.segment_table.setItem(
                    row, 1, make_item(format_timestamp(end_time) if end_time is not None else "")
                )
                self.segment_table.setItem(row, 2, make_item(segment.text))
                segment_spans = list(getattr(segment, "ruby_spans", []) or [])
                if segment_spans and len(segment_spans) == 1:
                    ruby_text = str(segment_spans[0][2] or "")
                else:
                    ruby_text = segment.ruby or ""
                ruby_text = format_mora_reading(ruby_text, segment.start, segment.mora_cues)
                ruby_item = make_item(ruby_text)
                ruby_item.setToolTip(
                    "Use Mora Timing… to edit on a waveform, or enter timestamps before morae. "
                    "For a pause, use an end and next start together, "
                    "e.g. <00:12.000>あ<00:12.350><00:12.650>な."
                )
                self.segment_table.setItem(row, 3, ruby_item)
                if segment.needs_timing_review:
                    for column in range(4):
                        item = self.segment_table.item(row, column)
                        item.setBackground(QBrush(QColor(160, 110, 20, 80)))
                        item.setToolTip("Text changed or this chunk was split. Review its timing.")
                instant = QCheckBox()
                instant.setChecked(bool(segment.instant))
                instant.toggled.connect(lambda checked, r=row: self.instant_changed(r, checked))
                self.segment_table.setCellWidget(row, 4, instant)

                # Whitespace can still exist as a real karaoke segment in the
                # underlying LRCX data, but it is visual clutter in the editor.
                # Keep the row/data intact for saving and timing calculations;
                # only hide whitespace-only rows in this table.
                self.segment_table.setRowHidden(row, bool(str(segment.text).strip() == ""))

            if segments:
                first_visible = next(
                    (i for i, segment in enumerate(segments)
                     if str(segment.text).strip()),
                    0,
                )
                self.segment_table.setCurrentCell(first_visible, 0)
        finally:
            del blocker
            self.loading = previous_loading
        self._update_text_timing_notice(line)
    def _update_detected_language(self):
        """Update language display, caching expensive provider detection."""
        if not hasattr(self, "detected_language_label"):
            return
        lines = tuple(reading_source(line.text, line.translation_lines) for line in self.lines if str(line.text or ""))
        mode = self._current_generation_mode() if hasattr(self, "_current_generation_mode") else None
        cache_key = (lines, mode)
        if cache_key == self._detected_language_cache_key:
            detected = self._detected_language_cache_value
        elif not lines or detect_lyrics_language is None:
            detected = "Unknown"
        else:
            builtin = str(detect_lyrics_collection_language(lines) or "unknown")
            display_names = {
                "japanese": "Japanese",
                "chinese": "Chinese",
                "korean": "Korean",
                "latin": "Latin-based language",
            }
            if builtin in display_names:
                detected = display_names[builtin]
            else:
                providers = {
                    str(provider_id)
                    for text in lines
                    if (provider_id := find_reading_provider(original_reading_text(text), language="unknown"))
                }
                if len(providers) == 1:
                    detected = next(iter(providers)).replace("_", " ").replace("-", " ").title()
                elif providers:
                    detected = "Mixed"
                else:
                    detected = "Other"
            self._detected_language_cache_key = cache_key
            self._detected_language_cache_value = detected
        self.detected_language_label.setText(f"Detected: {detected}")

    def reading_mode_changed(self, index):
        if self.loading or not hasattr(self, "reading_mode_combo"):
            return
        self._push_undo_state()
        mode = self.reading_mode_combo.itemData(index) or "auto"
        self._generation_mode = mode
        # Invalidate background workers started for the previous provider.
        self._reading_generation_epoch += 1
        set_reading_mode(mode)

        # Purge stale generated ruby left by older builds. Manual/imported ruby
        # remains untouched.
        for lyric_line in self.lines:
            for segment in getattr(lyric_line, "segments", []) or []:
                if getattr(segment, "ruby_source", None) == "generated":
                    segment.ruby = None
                    segment.ruby_source = None

        if mode == "auto":
            self._schedule_auto_reading_generation()
        # Generation mode is persisted as [g:...] and therefore changing it
        # is itself a document edit.
        self._mark_document_dirty()
        self._update_detected_language()
        _, line = self._current_line()
        if line is not None:
            self._refresh_ruby_preview(line)
            self._populate_furigana_editor(line)
            line_index = self._current_line_index()
            QTimer.singleShot(
                0, lambda: self._refresh_auto_readings_for_line(line_index)
            )

    def generate_readings_for_current_timestamp(self):
        """Generate automatic reading data for the selected lyric only."""
        if build_furigana_segments is None:
            QMessageBox.warning(
                self,
                "Generate Readings",
                "No automatic reading generator is available.",
            )
            return

        index, line = self._current_line()
        if index is None or line is None:
            QMessageBox.information(
                self,
                "Generate Readings",
                "Select a lyric timestamp first.",
            )
            return

        mode = self.reading_mode_combo.currentData() or "auto"
        set_reading_mode(mode)

        # Generated ruby is display/cache data, not authored document data.
        # Clear only generated data belonging to this line so manual/imported
        # readings remain untouched.
        for segment in getattr(line, "segments", []) or []:
            if getattr(segment, "ruby_source", None) == "generated":
                segment.ruby = None
                segment.ruby_source = None

        source_text = str(line.text or "")
        if not source_text:
            self.load_selected_line(self.line_list.currentRow())
            return

        try:
            tokens = build_furigana_segments(reading_source(source_text, line.translation_lines), force=True)
        except Exception as error:
            debug_print(f"[Generation] Failed for {source_text!r}: {error}")
            QMessageBox.warning(
                self,
                "Generate Readings",
                f"Could not generate readings for this lyric:\n{error}",
            )
            return

        generated = any(token.get("reading") for token in tokens)
        if generated:
            self.readings_editor.set_tokens(self._furigana_editor_tokens(line))
            if self.readings_editor.tokens():
                self.furigana_changed()
        else:
            self.load_selected_line(self.line_list.currentRow())

        missing = (
            missing_english_readings(self._furigana_editor_tokens(line), line.translation_lines)
            if mode == "english_katakana" else []
        )
        (QMessageBox.warning if missing else QMessageBox.information)(
            self,
            "Generate Readings",
            (
                f"Generated {self.reading_mode_combo.currentText()} reading(s) "
                "for this timestamp."
                if generated
                else "No reading could be generated for this timestamp."
            ) + unconverted_words_message(missing),
        )

    def generate_readings_for_all_lyrics(self):
        """Generate automatic readings without changing lyric structure.

        Reading-token boundaries are renderer metadata, not karaoke boundaries.
        ``build_furigana_segments`` stores successful results in the shared
        reading cache, so the editor/player can reuse them without rewriting
        ``LyricLine.segments``.
        """
        if build_furigana_segments is None:
            QMessageBox.warning(
                self,
                "Generate Readings",
                "No automatic reading generator is available.",
            )
            return

        mode = self.reading_mode_combo.currentData() or "auto"
        set_reading_mode(mode)

        # Force generation means "use this provider now". Do not let generated
        # Pinyin/Jyutping/furigana from another mode survive on model segments.
        # Explicit/manual readings are preserved.
        for lyric_line in self.lines:
            for segment in getattr(lyric_line, "segments", []) or []:
                if getattr(segment, "ruby_source", None) == "generated":
                    segment.ruby = None
                    segment.ruby_source = None

        texts = [reading_source(line.text, line.translation_lines) for line in self.lines if line.text]
        if mode in {"pinyin", "english_katakana"} or (mode == "auto" and detect_lyrics_collection_language(texts) == "chinese"):
            # Warm dictionary readings together so Generate All starts a
            # single disposable worker instead of one process per line.
            prefetch_furigana_batches(texts, force=True)

        generated_lines = 0
        errors = 0
        missing = []
        for line in self.lines:
            source_text = str(line.text or "")
            if not source_text:
                continue
            try:
                tokens = build_furigana_segments(reading_source(source_text, line.translation_lines), force=True)
            except Exception as error:
                debug_print(f"[Generation] Failed for {source_text!r}: {error}")
                errors += 1
                continue
            if any(token.get("reading") for token in tokens):
                generated_lines += 1
            if mode == "english_katakana":
                missing.extend(missing_english_readings(
                    self._furigana_editor_tokens(line), line.translation_lines,
                ))

        row = self.line_list.currentRow()
        if row >= 0:
            self.load_selected_line(row)

        if errors or missing:
            QMessageBox.warning(
                self,
                "Generate Readings",
                f"Generated readings for {generated_lines} lyric line(s)"
                + (f", with {errors} error(s)." if errors else ".")
                + unconverted_words_message(missing),
            )
        else:
            QMessageBox.information(
                self,
                "Generate Readings",
                f"Generated {self.reading_mode_combo.currentText()} readings "
                f"for {generated_lines} lyric line(s).",
            )

    def _schedule_auto_reading_generation(self):
        # Providers may load after the editor is constructed. Defer one event
        # loop turn, then warm generic automatic readings without mutating
        # lyric or karaoke structure.
        QTimer.singleShot(0, self._run_auto_reading_generation)

    def _run_auto_reading_generation(self):
        # This used to loop over every lyric synchronously via QTimer.singleShot,
        # which still runs on the UI thread. A long Cantonese song could therefore
        # freeze the entire editor while PyCantonese analysed every line.
        if self._closed or build_furigana_segments is None or self._current_generation_mode() != "auto":
            return
        self._start_background_reading_generation(
            [reading_source(line.text, line.translation_lines) for line in self.lines if line.text],
            -1,
        )

    def _furigana_editor_tokens(self, line, include_generated=False):
        """Build reading-editor rows from lyric text, not karaoke chunks.

        Karaoke timing and furigana tokenization are independent layers. The
        editor must therefore use the tokenizer's natural text boundaries
        (for example ``描き`` remains one row) and then overlay any manual ruby
        spans stored inside karaoke segments.
        """
        text = str(line.text or "")
        if not text:
            return []

        # Display paths must never wait for a provider or its request lock.
        # Selection/background workers fill this cache and refresh the widgets
        # when ready; authored ruby is available immediately even on a miss.
        tokens = get_cached_furigana_segments(reading_source(text, line.translation_lines))

        # Collect manual/generated ruby spans in line-text coordinates without
        # exposing karaoke boundaries to the editor.
        spans = []
        cursor = 0
        for segment in getattr(line, "segments", []) or []:
            segment_text = str(segment.text or "")
            segment_len = len(segment_text)
            segment_spans = list(getattr(segment, "ruby_spans", []) or [])
            if segment_spans:
                source = "manual"
                for start, end, reading in segment_spans:
                    # A timed chunk may own a leading/trailing space from the
                    # middle of a grouped reading. Preserve it when rejoining.
                    reading = str(reading or "")
                    if reading:
                        spans.append((
                            cursor + max(0, int(start)),
                            cursor + min(segment_len, int(end)),
                            reading,
                            source,
                        ))
            elif getattr(segment, "ruby", None):
                source = getattr(segment, "ruby_source", None) or "manual"
                if include_generated or source != "generated":
                    spans.append((cursor, cursor + segment_len, str(segment.ruby), source))
            cursor += segment_len

        if not tokens:
            boundaries = sorted({
                0, len(text),
                *(max(0, min(len(text), boundary))
                  for start, end, _, _ in spans for boundary in (start, end)),
            })
            tokens = [
                {"text": text[start:end], "reading": None}
                for start, end in zip(boundaries, boundaries[1:])
                if start < end
            ]

        # Overlay explicit ruby on the natural reading tokens. For a token such
        # as 描き, the manual span normally covers 描 only; the row still remains
        # "描き" and displays the manual reading "が".
        result = []
        word_boundaries = {boundary for group in line.reading_groups for boundary in group}
        position = 0
        for token in tokens:
            token_text = str(token.get("text", ""))
            if not token_text:
                continue
            token_start = position
            token_end = position + len(token_text)
            reading = str(token.get("reading") or "") or None
            source = token.get("source")

            # Reassemble annotations divided across karaoke chunks, keeping
            # their exact base range so an edit affects only that annotation.
            groups = []
            for span_start, span_end, span_reading, span_source in sorted(spans):
                if span_start < token_end and span_end > token_start:
                    if span_source != "generated" or include_generated:
                        first, last = max(span_start, token_start), min(span_end, token_end)
                        if first != span_start or last != span_end:
                            span_reading = split_reading_by_text_ranges(
                                text[span_start:span_end], span_reading,
                                [(first - span_start, last - span_start)],
                            )[0]
                        if not span_reading:
                            continue
                        if groups and groups[-1][1] == first and first not in word_boundaries:
                            a, _, value, kind = groups[-1]
                            groups[-1] = (a, last, value + span_reading, kind)
                        else:
                            groups.append((first, last, span_reading, span_source))

            if len(groups) > 1:
                # Several separate annotations within one provider token must
                # stay separately editable instead of showing only the first.
                cursor = token_start
                for a, b, value, kind in groups:
                    if cursor < a:
                        result.append({"text": text[cursor:a], "reading": None,
                                       "start": cursor, "end": a})
                    result.append({"text": text[a:b], "reading": value, "source": kind,
                                   "start": a, "end": b, "ruby_start": a, "ruby_end": b})
                    cursor = b
                if cursor < token_end:
                    result.append({"text": text[cursor:token_end], "reading": None,
                                   "start": cursor, "end": token_end})
                position = token_end
                continue

            annotation_range = {}
            if groups:
                a, b, reading, source = groups[0]
                annotation_range = {"ruby_start": a, "ruby_end": b}

            if reading and source == "generated" and not include_generated:
                reading = None
                source = None

            result.append({"text": token_text, "reading": reading, "source": source,
                           "start": token_start, "end": token_end, **annotation_range})
            position = token_end

        return group_reading_tokens(text, result, line.reading_groups)

    def _stored_reading_tokens(self, line, include_generated=False):
        """Return reading-editor tokens using text boundaries, never karaoke boundaries."""
        tokens = self._furigana_editor_tokens(line, include_generated=include_generated)
        if not tokens:
            return []
        return tokens if any(token.get("reading") for token in tokens) else []

    def _refresh_ruby_preview(self, line):
        stored = self._furigana_editor_tokens(line)
        if stored:
            self.readings_editor.setToolTip("  ".join(
                f"{part['text']}({part['reading']})"
                if part.get("reading") else part["text"]
                for part in stored
            ))
            return

        self.readings_editor.setToolTip(
            "No stored readings. Automatic generation follows the selected mode."
        )

    def offset_changed(self, value):
        if self.loading:
            return
        self._push_undo_state()
        self.lyrics_offset_ms = float(value)
        self._mark_document_dirty()
        self._refresh_lyrics_waveform_markers()
        self._refresh_karaoke_waveform_editor()
        self._refresh_karaoke_preview()
        self._refresh_lyrics_preview(rebuild_segments=False)
        media = getattr(self.player, "media_player", None)
        if media is not None:
            self._update_playback_line_style(media.position())

    def _audio_to_lyric_seconds(self, position_ms):
        return (float(position_ms) - self.lyrics_offset_ms) / 1000.0

    def _lyric_to_audio_ms(self, seconds):
        return round(float(seconds) * 1000.0 + self.lyrics_offset_ms)

    def _update_furigana_editor_availability(self, line):
        """Readings are editable independently of the line's karaoke timing."""
        enabled = line is not None
        if hasattr(self, "readings_editor"):
            self.readings_editor.setEnabled(enabled)
        if hasattr(self, "reset_furigana_button"):
            self.reset_furigana_button.setEnabled(enabled)

    def _populate_furigana_editor(self, line):
        if not hasattr(self, "readings_editor"):
            return

        self._update_furigana_editor_availability(line)

        # Preserve an outer loading operation. This method is called while a
        # selected line is being populated, so it must not accidentally turn
        # loading off and let the remaining UI setup mark the document dirty.
        previous_loading = self.loading
        self.loading = True
        blocker = QSignalBlocker(self.readings_editor)
        try:
            self.readings_editor.set_tokens(self._furigana_editor_tokens(line))
        finally:
            del blocker
            self.loading = previous_loading
        self._refresh_lyrics_preview()

    def furigana_changed(self, token_index=None):
        if self.loading:
            return
        _, line = self._current_line()
        if line is None:
            return


        editor_tokens = self.readings_editor.tokens()
        tokens = [(str(token["text"]), str(token.get("reading") or "").strip() or None)
                  for token in editor_tokens]

        if "".join(base for base, _ in tokens) != line.text:
            return
        if token_index is not None and not 0 <= token_index < len(tokens):
            return
        if line.has_karaoke and "".join(segment.text for segment in line.segments) != line.text:
            return

        self._push_undo_state()


        old_segments = list(line.segments) or [
            LyricSegment(line.start, line.text)
        ]

        # IMPORTANT: furigana tokens and karaoke chunks are different layers.
        # Rebuilding line.segments from ruby tokens used to overwrite the real
        # karaoke boundaries, so merely editing/readjusting a reading could turn
        # one timed chunk into many tiny timed chunks after save/reload.
        if getattr(line, "has_karaoke", False) or token_index is not None:
            if not line.segments:
                line.segments = old_segments
            indices = range(len(tokens)) if token_index is None else [token_index]
            for index in indices:
                token = editor_tokens[index]
                base, reading = tokens[index]
                if token_index is None and not reading:
                    continue
                start, end = token["start"], token["end"]
                if "ruby_start" in token:
                    start, end = token["ruby_start"], token["ruby_end"]
                else:
                    # Keep okurigana outside a new annotation. Scripts without
                    # kanji (e.g. Korean romanization) use the whole token.
                    match = re.search(r"[\u3400-\u9fff]+", base)
                    if match:
                        start, end = start + match.start(), start + match.end()
                replace_reading_span(line, start, end, reading)
        else:
            # No karaoke structure exists, so ruby tokens may safely define the
            # ordinary display segments.
            rebuilt = []
            position = 0
            ranges = []
            start_position = 0
            for segment in old_segments:
                end_position = start_position + len(segment.text)
                ranges.append((start_position, end_position, segment))
                start_position = end_position

            for base, reading in tokens:
                source = next(
                    (
                        seg
                        for a, b, seg in ranges
                        if a <= position < b
                    ),
                    old_segments[-1],
                )
                rebuilt.append(
                    LyricSegment(
                        source.start,
                        base,
                        reading,
                        source.instant,
                        source.end,
                        "manual" if reading else None,
                    )
                )
                position += len(base)

            line.segments = rebuilt
            # Keep line.start independent from the first karaoke segment.
            # A leading gap before the first sung chunk is valid.

        self.document_dirty = True
        self.karaoke_dirty = bool(
            getattr(line, "has_karaoke", False)
        )
        # Reading edits are document edits too. Without refreshing this state,
        # the Save button can remain disabled even though the ruby data changed.
        self._update_save_button_state()
        self.refresh_line_label(self.line_list.currentRow())
        self._refresh_ruby_preview(line)
        if self.karaoke_enabled:
            self.loading = True
            self._populate_segments(line)
            self.loading = False
            self._refresh_karaoke_preview()

    def combine_reading(self, start, end, reading):
        if self.loading:
            return
        _, line = self._current_line()
        if line is None or not 0 <= start < end <= len(line.text):
            return
        if any(char.isspace() for char in line.text[start:end]):
            return
        if line.segments and "".join(segment.text for segment in line.segments) != line.text:
            return
        self._push_undo_state()
        if not line.segments:
            line.segments = [LyricSegment(line.start, line.text)]
        replace_reading_span(line, start, end, reading or None)
        line.reading_groups = sorted([(a, b) for a, b in line.reading_groups if b <= start or a >= end]
                                     + [(start, end)])
        self._mark_document_dirty()
        self._refresh_ruby_preview(line)
        self._populate_furigana_editor(line)
        if self.karaoke_enabled:
            self._populate_segments(line)
            self._refresh_karaoke_preview()

    def split_reading(self, start, cut, end, left, right, ruby_start, ruby_end):
        if self.loading:
            return
        _, line = self._current_line()
        if line is None or not 0 <= start < cut < end <= len(line.text):
            return
        from ui.reading_split_dialog import reading_split_points

        base = line.text[start:end]
        if any(char.isspace() for char in base) or cut - start not in reading_split_points(base):
            return
        if not start <= ruby_start < ruby_end <= end:
            return
        if line.segments and "".join(segment.text for segment in line.segments) != line.text:
            return
        self._push_undo_state()
        if not line.segments:
            line.segments = [LyricSegment(line.start, line.text)]
        for a, b, reading in ((start, cut, left), (cut, end, right)):
            first, last = max(a, ruby_start), min(b, ruby_end)
            if first < last:
                replace_reading_span(line, first, last, reading or None)
            elif reading:
                replace_reading_span(line, a, b, reading)
        line.reading_groups = sorted([(a, b) for a, b in line.reading_groups if b <= start or a >= end]
                                     + [(start, cut), (cut, end)])
        self._mark_document_dirty()
        self._refresh_ruby_preview(line)
        self._populate_furigana_editor(line)
        if self.karaoke_enabled:
            self._populate_segments(line)
            self._refresh_karaoke_preview()

    def reset_all_readings_advanced(self):
        if not self.lines:
            return
        answer = QMessageBox.question(
            self,
            "Clear Furigana",
            "Remove all readings from every lyric line? Automatic readings can be generated again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_state()
        changed = False
        for line in self.lines:
            for segment in line.segments or []:
                if segment.ruby or segment.ruby_source or segment.ruby_spans:
                    segment.ruby = None
                    segment.ruby_source = None
                    segment.ruby_spans = []
                    segment.mora_cues = ()
                    changed = True
        if clear_reading_segment_cache is not None:
            clear_reading_segment_cache()
        if changed:
            self._mark_document_dirty()
        row = self.line_list.currentRow()
        if row >= 0:
            self.load_selected_line(row)
        if self._current_generation_mode() == "auto":
            self._schedule_auto_reading_generation()

    def clear_all_translations(self):
        if not any(line.translation_lines for line in self.lines):
            return
        answer = QMessageBox.question(
            self,
            "Clear Translations",
            "Remove translation rows from every lyric line? Original lyrics will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_state()
        for line in self.lines:
            if not line.translation_lines:
                continue
            # Project text offsets onto the retained rows. Apply the same map
            # to formatting and ruby so translations can appear between originals.
            keep = [False] * len(line.text)
            offset = 0
            retained = False
            for row, text in enumerate(line.text.split("\n")):
                if row not in line.translation_lines:
                    if retained:
                        keep[offset - 1] = True
                    keep[offset:offset + len(text)] = [True] * len(text)
                    retained = True
                offset += len(text) + 1
            positions = [0]
            for kept in keep:
                positions.append(positions[-1] + int(kept))
            new_text = "".join(char for char, kept in zip(line.text, keep) if kept)
            line.style_spans = [
                (positions[a], positions[b], *style)
                for a, b, *style in line.style_spans
                if positions[a] < positions[b]
            ]
            segments = []
            offset = 0
            for segment in line.segments:
                end = offset + len(segment.text)
                text = "".join(char for char, kept in zip(segment.text, keep[offset:end]) if kept)
                if text:
                    replacement = copy.deepcopy(segment)
                    replacement.text = text
                    replacement.visual_line = new_text[:positions[offset]].count("\n")
                    replacement.ruby_spans = [
                        (positions[offset + a] - positions[offset],
                         positions[offset + b] - positions[offset], reading)
                        for a, b, reading in segment.ruby_spans
                        if positions[offset + a] < positions[offset + b]
                    ]
                    if any(not keep[offset + i] and char != "\n"
                           for i, char in enumerate(segment.text)):
                        replacement.ruby = None
                        replacement.ruby_source = None
                        replacement.mora_cues = ()
                    segments.append(replacement)
                offset = end
            line.reading_groups = [(positions[a], positions[b]) for a, b in line.reading_groups
                                   if all(keep[a:b]) and positions[a] < positions[b]]
            line.text = new_text
            line.segments = segments
            line.translation_lines = []
        self._populate_lines(preserve_line_index=self._current_line()[0])
        self._mark_document_dirty()

    def clear_all_formatting(self):
        if not any(line.style_spans or line.is_blockquote for line in self.lines):
            return
        answer = QMessageBox.question(
            self,
            "Clear Formatting",
            "Remove text formatting and blockquotes from every lyric line?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_state()
        for line in self.lines:
            line.style_spans = []
            line.is_blockquote = False
        self._mark_document_dirty()
        row = self.line_list.currentRow()
        if row >= 0:
            self.load_selected_line(row)

    def reset_all_furigana(self):
        self._push_undo_state()
        changed = False
        for line in self.lines:
            for segment in line.segments:
                if segment.ruby_spans or (segment.ruby and segment.ruby_source != "generated"):
                    segment.ruby = None
                    segment.ruby_source = None
                    segment.ruby_spans = []
                    segment.mora_cues = ()
                    changed = True
        if changed:
            self._mark_document_dirty()
            # Reading generation may split internal segments so ruby can be
            # rendered precisely, but that does not create karaoke timing.
            # Keep karaoke state/dirty tracking completely independent.
            row = self.line_list.currentRow()
            if row >= 0:
                self.load_selected_line(row)
        else:
            QMessageBox.information(
                self,
                "Reset Readings",
                "There is no explicit furigana to reset. Automatic readings are already being used.",
            )

    @staticmethod
    def _shift_line_timing(line, delta):
        if abs(delta) <= 1e-9:
            return
        if line.end is not None:
            line.end += delta
        for segment in line.segments:
            segment.start += delta
            if segment.end is not None:
                segment.end += delta

    def commit_line_timestamp(self):
        """Commit the selected lyric timestamp, including provisional B timing.

        Commit is intentionally idempotent: pressing it again after a commit
        simply re-synchronizes the UI/model instead of requiring the marker to
        move first.
        """
        if self.loading:
            return
        index, line = self._current_line()
        if line is None:
            return

        preview_handle = getattr(self, "_waveform_lyric_preview_handle", None)
        preview_ms = getattr(self, "_waveform_lyric_preview_ms", None)
        if preview_handle and preview_ms is not None:
            position = float(preview_ms) / 1000.0
            if preview_handle == "start":
                self._push_undo_state()
                self._shift_line_timing(line, position - line.start)
                line.start = position
                self.loading = True
                self.line_time.setValue(position)
                self.loading = False
                self.refresh_line_label(index)
            else:
                self._push_undo_state()
                next_index = index + 1
                if next_index < len(self.lines):
                    next_line = self.lines[next_index]
                    value = max(float(line.start), position)
                    self._shift_line_timing(next_line, value - next_line.start)
                    next_line.start = value
                    self._populate_lines(preserve_line_index=index)
                    self._select_line_index(index)
                    self.load_selected_line(self.line_list.currentRow())
                else:
                    # Last line: B is a provisional next timestamp. Commit
                    # materializes it as a new empty lyric line, ready to edit.
                    value = max(float(line.start), position)
                    new_line = LyricLine(
                        start=value,
                        text="",
                        segments=[LyricSegment(value, "")],
                    )
                    self.lines.insert(index + 1, new_line)
                    self._populate_lines(preserve_line_index=index + 1)
                    self._select_line_index(index + 1)
                    self.load_selected_line(self.line_list.currentRow())
            self._waveform_lyric_preview_handle = None
            self._waveform_lyric_preview_ms = None
            self._mark_document_dirty()
        else:
            position = float(self.line_time.value())
            if abs(float(line.start) - position) > 1e-9:
                self._push_undo_state()
                self._shift_line_timing(line, position - line.start)
                line.start = position
                self._mark_document_dirty()
        # Every commit refreshes the same views, including repeated commits
        # and provisional waveform edits that bypass line_details_changed().
        timeline = sorted(enumerate(self.lines), key=lambda item: (item[1].start, item[0]))
        self._playback_line_starts = [line.start for _, line in timeline]
        self._playback_line_rows = [row for row, _ in timeline]
        self.refresh_line_label(index)
        self._refresh_lyrics_waveform_markers()
        self._refresh_karaoke_waveform_editor()
        self._refresh_karaoke_preview()
        self._refresh_lyrics_preview()
        self._update_save_button_state()

    def mark_current_line_playback_time(self):
        """Stamp the selected lyric with playback time and advance."""
        if self.player is None or not hasattr(self.player, "media_player"):
            return
        index, line = self._current_line()
        if line is None:
            return

        self._push_undo_state()
        position = max(0.0, self._audio_to_lyric_seconds(self.player.media_player.position()))
        self.loading = True
        self.line_time.setValue(position)
        self.loading = False
        self._shift_line_timing(line, position - line.start)
        line.start = position
        self.document_dirty = True
        self._update_save_button_state()
        timeline = sorted(enumerate(self.lines), key=lambda item: (item[1].start, item[0]))
        self._playback_line_starts = [line.start for _, line in timeline]
        self._playback_line_rows = [row for row, _ in timeline]
        self.refresh_line_label(index)
        # Keep the waveform's lyric-marker layer in sync immediately.
        # Mark Playback Time changes the model directly, so waiting for the
        # selection change below can leave the yellow timestamp markers at
        # their previous positions for a frame (or indefinitely if selection
        # does not change).
        self._refresh_lyrics_waveform_markers()
        self._refresh_karaoke_waveform_editor()

        next_index = index + 1
        if next_index < len(self.lines):
            self._select_line_index(next_index)
            item = self.line_list.currentItem()
            if item is not None:
                self.line_list.scrollToItem(
                    item, QListWidget.ScrollHint.PositionAtCenter
                )
        self._refresh_lyrics_preview()

    def insert_lyric_newline(self):
        self._insert_lyric_break(r"{\n}")

    def _translation_document(self):
        from core.translation_document import document_from_lines
        return document_from_lines(
            self.lines, self._editing_song_path, self.lrc_path,
            self.lyrics_offset_ms, self._generation_mode, self._document_metadata,
        )

    def _translation_document_for_panel(self):
        if not self._commit_pending_source():
            raise ValueError("Correct the source lyrics before translating.")
        return self._translation_document()

    def show_translation_quota(self):
        if self.translation_panel is not None:
            self.translation_panel.show_quota()

    def _ensure_translation_panel(self):
        if self.translation_panel is not None:
            return
        manager = getattr(self.player, "plugin_manager", None)
        if manager is None or not getattr(manager, "translation_providers", {}):
            return
        from ui.translation_workspace import TranslationWorkspace
        self.translation_panel = TranslationWorkspace(
            manager, self, self._translation_document_for_panel, self._apply_lyrics_translations)
        self.translation_page.layout().addWidget(self.translation_panel)

    def _apply_lyrics_translations(self, document, translations):
        from core.translation_document import render_translations
        from lyrics.lrcx_parser import parse_lrcx
        if self._closed or self._source_dirty or document != self._translation_document():
            raise ValueError("The lyrics changed. Reopen translation to use the current document.")
        lines = parse_lrcx(render_translations(document, translations), infer_ends=False)
        selected = self._current_line_index()
        self._push_undo_state()
        self._reading_generation_epoch += 1
        self.lines = lines
        self.karaoke_dirty = any(line.has_karaoke for line in self.lines)
        self._populate_lines(preserve_line_index=selected)
        self._mark_document_dirty()

    def insert_lyric_translation(self):
        self._insert_lyric_break(r"{\nt}")

    def _insert_lyric_break(self, marker):
        if self.loading or self._current_line()[1] is None:
            return
        self.line_text.insertPlainText(marker)
        self.line_text.setFocus()

    def insert_lyric_emphasis(self, marker, closing_marker=None):
        if self.loading or self._current_line()[1] is None:
            return
        cursor = self.line_text.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        selected = cursor.selectedText().replace("\u2029", "\n")
        cursor.insertText(marker + selected + (closing_marker or marker))
        cursor.setPosition(start + len(marker))
        if selected:
            cursor.setPosition(end + len(marker), cursor.MoveMode.KeepAnchor)
        self.line_text.setTextCursor(cursor)
        self.line_text.setFocus()

    def toggle_lyric_quote(self):
        if self.loading or self._current_line()[1] is None:
            return
        cursor = self.line_text.textCursor()
        anchor, position = cursor.anchor(), cursor.position()
        editing = self.line_text.textCursor()
        editing.setPosition(0)
        if self.line_text.toPlainText().startswith("> "):
            editing.setPosition(2, editing.MoveMode.KeepAnchor)
            editing.removeSelectedText()
            delta = -2
        else:
            editing.insertText("> ")
            delta = 2
        cursor.setPosition(max(0, anchor + delta))
        cursor.setPosition(max(0, position + delta), cursor.MoveMode.KeepAnchor)
        self.line_text.setTextCursor(cursor)
        self.line_text.setFocus()

    def line_details_changed(self):
        if self.loading:
            return
        index, line = self._current_line()
        if line is None:
            return
        self._push_undo_state(line_index=index)
        delta = self.line_time.value() - line.start
        self._shift_line_timing(line, delta)
        line.start = self.line_time.value()
        markup, line.is_blockquote = parse_quote_prefix(self.line_text.toPlainText())
        markup, line.translation_lines = decode_line_breaks(markup)
        text, line.style_spans, _offsets = parse_emphasis(markup)
        self.quote_button.setChecked(line.is_blockquote)
        if text != line.text:
            line.segments = correct_line_text(line, text, getattr(self, "_line_text_change", None))
            line.reading_groups = remap_reading_groups(
                line.text, text, line.reading_groups, getattr(self, "_line_text_change", None))
            line.text = text
            self._preview_segments_line_index = None
            if not text:
                line.has_karaoke = False
            self.loading = True
            if self.karaoke_enabled:
                self._populate_segments(line)
            self.loading = False
        self._update_text_timing_notice(line)
        self._line_text_change = None
        self._mark_document_dirty()
        self.refresh_line_label(index)
        if self.karaoke_enabled:
            self.full_line_label.setText(line.text)
            self._build_separator_buttons(line)
        self._refresh_ruby_preview(line)
        self._update_lyric_search()
        self._refresh_lyrics_preview()
        # Wait for typing to settle before requesting readings for new text.
        self._line_reading_timer.start()
        if self.karaoke_enabled:
            self._refresh_karaoke_preview()

        # Keep the waveform live with direct timestamp edits in the spin box.
        # A manual edit changes the model immediately, so the selected A/B
        # boundary and the yellow lyric markers must be repainted from that
        # new value right away.
        timeline = sorted(enumerate(self.lines), key=lambda item: (item[1].start, item[0]))
        self._playback_line_starts = [row_line.start for _, row_line in timeline]
        self._playback_line_rows = [row for row, _ in timeline]
        self._refresh_lyrics_waveform_markers()
        self._refresh_karaoke_waveform_editor()

    def add_line(self):
        if not self._commit_pending_source():
            return
        self._push_undo_state()
        index, current = self._current_line()
        start = (current.start + 1.0) if current else 0.0
        line = LyricLine(start=start, text="", segments=[LyricSegment(start, "")])
        insert_at = index + 1 if index is not None else len(self.lines)
        self.lines.insert(insert_at, line)
        self._populate_lines()
        self._select_line_index(insert_at)
        self._mark_document_dirty()

    def delete_line(self):
        if not self._commit_pending_source():
            return
        indices = self._selected_line_indices()
        if not indices:
            return
        if len(indices) > 1:
            self._confirm_bulk_delete(indices)
        else:
            self._delete_selected_lines(indices)

    def move_line(self, direction):
        if not self._commit_pending_source():
            return
        indices = self._selected_line_indices()
        if not indices:
            return
        selected = set(indices)
        order = list(range(len(self.lines)))
        for index in (indices if direction < 0 else reversed(indices)):
            target = index + direction
            if 0 <= target < len(self.lines) and target not in selected:
                order[index], order[target] = order[target], order[index]
                selected.remove(index)
                selected.add(target)
        if order == list(range(len(self.lines))):
            return
        current = self._current_line_index()
        self._push_undo_state()
        self.lines = [self.lines[index] for index in order]
        self._finish_line_operation(sorted(selected), order.index(current) if current is not None else None)

    # ------------------------------------------------------------------
    # Dialog lifecycle
    # ------------------------------------------------------------------
    def _confirm_close(self):
        if not self.document_dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved Lyric Changes", "Save your lyric edits before closing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return bool(self.save_document())
        return answer == QMessageBox.StandardButton.Discard

    def reject(self):
        if self._confirm_close():
            self.done(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        if not self._confirm_close():
            event.ignore()
            return
        if self.done(QDialog.DialogCode.Rejected) is False:
            event.ignore()
        else:
            event.accept()

    def done(self, result):
        if self._choosing_document:
            self._save_editor_layout()
            self._closed = True
            super().done(result)
            return True
        if self.translation_panel is not None and not self.translation_panel.confirm_close():
            return False
        self._save_editor_layout()
        self._closed = True
        if self._share_card is not None:
            self._share_card.shutdown()
        if self.translation_panel is not None:
            self.translation_panel.shutdown()
        if self._segment_preview_range is not None:
            self._cancel_segment_preview()
            media = getattr(self.player, "media_player", None)
            if media is not None:
                media.pause()
        self._discard_autosave()
        self._reading_generation_epoch += 1
        self._autosave_timer.stop()
        self._line_reading_timer.stop()
        self._precise_display_timer.stop()
        self._timing_validation_timer.stop()
        self.source_editor.shutdown()
        self.lyrics_renderer_preview.stop_workers()
        if hasattr(self, "karaoke_renderer_preview"):
            self.karaoke_renderer_preview.stop_workers()
        # The player deletes the closed dialog. Stop its decoder threads before
        # Qt destroys their parent widgets and QThread objects.
        for waveform in self.findChildren(WaveformWidget):
            waveform._stop_worker()
        QApplication.instance().removeEventFilter(self)
        media = getattr(self.player, "media_player", None)
        if media is not None:
            for signal_name, callback in (
                ("positionChanged", self.preview_position_changed),
                ("durationChanged", self.preview_duration_changed),
                ("playbackStateChanged", self.preview_playback_state_changed),
                ("playbackStateChanged", self._update_precise_display_playback_state),
                ("playbackRateChanged", self._segment_preview_rate_changed),
            ):
                try:
                    getattr(media, signal_name).disconnect(callback)
                except (AttributeError, RuntimeError, TypeError):
                    pass
        super().done(result)
        return True
