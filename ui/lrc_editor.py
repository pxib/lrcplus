"""Structured lyrics editor, line editing and reading generation."""

from pathlib import Path
from bisect import bisect_right
import copy
import re
import threading

from PySide6.QtCore import Qt, QSignalBlocker, Signal, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
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
from lyrics.writer import format_timestamp
from lyrics.timing import parse_timestamp
from lyrics.karaoke_timing import next_lyric_start, segment_end
from lyrics.furigana import get_cached_furigana_segments
from core.debug import debug_print
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


class LrcEditorDialog(
    LyricsEditorDocumentMixin,
    LyricsEditorPlaybackMixin,
    LyricsEditorKaraokeMixin,
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

    def __init__(self, lrc_path, parent=None):
        super().__init__(parent)

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
        # This path is only the default save target when no file exists yet.
        # Keep that draft entirely in memory until the user explicitly saves.
        self.is_new_document = not self.lrc_path.exists()
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
        self.lyrics_offset_ms = get_lrcx_offset(self.lrc_path) if self.lrc_path.suffix.lower() == ".lrcx" else 0.0

        # Model checkpoints support all editor tools; ordinary typing stores
        # only its changed line, while bulk operations keep a full snapshot.
        self._undo_stack = []
        self._redo_stack = []
        self._history_limit = 100
        self._history_restoring = False

        # Crash recovery lives beside the lyric file but uses a hidden,
        # separate filename so the real lyric is never touched until Save.
        self._autosave_path = self.lrc_path.with_name(
            f".{self.lrc_path.name}.autosave"
        )
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.timeout.connect(self._autosave_if_needed)


        self._update_document_title()
        self.resize(1180, 820)

        self._load_document()
        self._restore_autosave_if_available()
        # Catch key events from transient QTableWidget/QSpinBox editors too.
        # The filter itself is tightly scoped to this dialog's Karaoke controls.
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
        self._reset_history_baseline()
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

        self._saved_document_state = None if self.document_dirty else self._document_state()
        self._refresh_lyrics_waveform()

        if self.player is not None and hasattr(self.player, "media_player"):
            player = self.player.media_player
            player.positionChanged.connect(self.preview_position_changed)
            player.durationChanged.connect(self.preview_duration_changed)
            player.playbackStateChanged.connect(self.preview_playback_state_changed)
            player.playbackStateChanged.connect(
                self._update_precise_display_playback_state
            )
            self.preview_position_changed(player.position())
            self.preview_duration_changed(player.duration())
            self.preview_playback_state_changed(player.playbackState())

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

    def _populate_lines(self, preserve_line_index=None):
        """Refresh the sidebar as a view of canonical document order.

        Sorting changes only the list presentation. self.lines is never sorted.
        """
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

        if preserve_line_index is not None:
            self._select_line_index(preserve_line_index)

        self._update_share_selection_status()
        self._refresh_lyrics_waveform_markers()
        self._update_lyric_search()

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

    def _select_line_index(self, line_index):
        try:
            view_row = self._view_line_indices.index(int(line_index))
        except (ValueError, TypeError):
            return
        self.line_list.setCurrentRow(view_row)

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

        if row < 0 or row >= len(self.lines):
            return
        try:
            view_row = self._view_line_indices.index(row)
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
            if hasattr(self, "lyrics_waveform"):
                self.lyrics_waveform.clear_selected_lyric_timing()
            self.loading = False
            return
        line = self.lines[row]
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
                    row, 0, QTableWidgetItem(format_timestamp(segment.start))
                )
                end_time = segment_end(line, row, fallback_end)
                self.segment_table.setItem(
                    row, 1, QTableWidgetItem(format_timestamp(end_time) if end_time is not None else "")
                )
                self.segment_table.setItem(row, 2, QTableWidgetItem(segment.text))
                segment_spans = list(getattr(segment, "ruby_spans", []) or [])
                if segment_spans and len(segment_spans) == 1:
                    ruby_text = str(segment_spans[0][2] or "")
                else:
                    ruby_text = segment.ruby or ""
                self.segment_table.setItem(row, 3, QTableWidgetItem(ruby_text))
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
            editor_tokens = self._furigana_editor_tokens(line)
            previous_loading = self.loading
            self.loading = True
            blocker = QSignalBlocker(self.furigana_table)
            try:
                self.furigana_table.setRowCount(0)
                for token in editor_tokens:
                    text_value = str(token.get("text", ""))
                    if not text_value:
                        continue
                    row = self.furigana_table.rowCount()
                    self.furigana_table.insertRow(row)
                    self.furigana_table.setItem(
                        row, 0, QTableWidgetItem(text_value)
                    )
                    self.furigana_table.setItem(
                        row,
                        1,
                        QTableWidgetItem(str(token.get("reading") or "")),
                    )
            finally:
                del blocker
                self.loading = previous_loading

            if self.furigana_table.rowCount():
                self.furigana_changed(self.furigana_table.item(0, 1))
        else:
            self.load_selected_line(self.line_list.currentRow())

        QMessageBox.information(
            self,
            "Generate Readings",
            (
                f"Generated {self.reading_mode_combo.currentText()} reading(s) "
                "for this timestamp."
                if generated
                else "No reading could be generated for this timestamp."
            ),
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
        if mode == "pinyin" or (mode == "auto" and detect_lyrics_collection_language(texts) == "chinese"):
            # Warm Mandarin readings together so Generate All starts a single
            # disposable dictionary worker instead of one process per line.
            prefetch_furigana_batches(texts, force=True)

        generated_lines = 0
        errors = 0
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

        row = self.line_list.currentRow()
        if row >= 0:
            self.load_selected_line(row)

        if errors:
            QMessageBox.warning(
                self,
                "Generate Readings",
                f"Generated readings for {generated_lines} lyric line(s), "
                f"with {errors} error(s).",
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
                    reading = str(reading or "").strip()
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
        position = 0
        for token in tokens:
            token_text = str(token.get("text", ""))
            if not token_text:
                continue
            token_start = position
            token_end = position + len(token_text)
            reading = str(token.get("reading") or "") or None
            source = token.get("source")

            # Manual/imported ruby always wins over generated reading.
            for span_start, span_end, span_reading, span_source in spans:
                if span_start < token_end and span_end > token_start:
                    if span_source != "generated" or include_generated:
                        reading = span_reading
                        source = span_source
                        break

            if reading and source == "generated" and not include_generated:
                reading = None
                source = None

            result.append({"text": token_text, "reading": reading, "source": source})
            position = token_end

        return result

    def _stored_reading_tokens(self, line, include_generated=False):
        """Return reading-editor tokens using text boundaries, never karaoke boundaries."""
        tokens = self._furigana_editor_tokens(line, include_generated=include_generated)
        if not tokens:
            return []
        return tokens if any(token.get("reading") for token in tokens) else []

    def _refresh_ruby_preview(self, line):
        stored = self._furigana_editor_tokens(line)
        if stored:
            self.ruby_preview.setText("  ".join(
                f"{part['text']}({part['reading']})"
                if part.get("reading") else part["text"]
                for part in stored
            ))
            return

        self.ruby_preview.setText(
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
        media = getattr(self.player, "media_player", None)
        if media is not None:
            self._update_playback_line_style(media.position())

    def _audio_to_lyric_seconds(self, position_ms):
        return (float(position_ms) - self.lyrics_offset_ms) / 1000.0

    def _lyric_to_audio_ms(self, seconds):
        return round(float(seconds) * 1000.0 + self.lyrics_offset_ms)

    def _update_furigana_editor_availability(self, line):
        """Keep one furigana editor authoritative for each line.

        Once a line has karaoke timing, its ruby belongs to the karaoke segment
        layer. The Lyrics-tab reading table is therefore read-only/disabled so
        it cannot accidentally rewrite karaoke structure. Existing ruby data is
        left untouched and can be edited from the Karaoke table.
        """
        has_karaoke = bool(getattr(line, "has_karaoke", False)) if line is not None else False
        enabled = not has_karaoke
        if hasattr(self, "furigana_table"):
            self.furigana_table.setEnabled(enabled)
        if hasattr(self, "reset_furigana_button"):
            self.reset_furigana_button.setEnabled(enabled)
        if hasattr(self, "furigana_karaoke_notice"):
            self.furigana_karaoke_notice.setVisible(has_karaoke)

    def _populate_furigana_editor(self, line):
        if not hasattr(self, "furigana_table"):
            return

        self._update_furigana_editor_availability(line)

        # Preserve an outer loading operation. This method is called while a
        # selected line is being populated, so it must not accidentally turn
        # loading off and let the remaining UI setup mark the document dirty.
        previous_loading = self.loading
        self.loading = True
        blocker = QSignalBlocker(self.furigana_table)
        try:
            self.furigana_table.setRowCount(0)

            tokens = self._furigana_editor_tokens(line)

            for token in tokens:
                text_value = str(token.get("text", ""))
                if not text_value:
                    continue
                row = self.furigana_table.rowCount()
                self.furigana_table.insertRow(row)
                self.furigana_table.setItem(row, 0, QTableWidgetItem(text_value))
                reading = str(token.get("reading") or "")
                self.furigana_table.setItem(row, 1, QTableWidgetItem(reading))
        finally:
            del blocker
            self.loading = previous_loading

    def furigana_changed(self, item):
        if self.loading or item.column() != 1:
            return
        _, line = self._current_line()
        if line is None:
            return


        self._push_undo_state()
        tokens = []
        for row in range(self.furigana_table.rowCount()):
            base_item = self.furigana_table.item(row, 0)
            reading_item = self.furigana_table.item(row, 1)
            if base_item is None:
                continue
            base = base_item.text()
            reading = (
                reading_item.text().strip()
                if reading_item is not None
                else ""
            ) or None
            tokens.append((base, reading))

        if "".join(base for base, _ in tokens) != line.text:
            return


        old_segments = list(line.segments) or [
            LyricSegment(line.start, line.text)
        ]

        # IMPORTANT: furigana tokens and karaoke chunks are different layers.
        # Rebuilding line.segments from ruby tokens used to overwrite the real
        # karaoke boundaries, so merely editing/readjusting a reading could turn
        # one timed chunk into many tiny timed chunks after save/reload.
        if getattr(line, "has_karaoke", False):
            # Furigana is metadata *inside* karaoke chunks. Never rebuild or
            # split line.segments here: their text/timestamps are authoritative.
            token_ranges = []
            position = 0
            for base, reading in tokens:
                base = str(base or "")
                end_position = position + len(base)
                if reading:
                    runs = list(re.finditer(r"[\u3400-\u9fff]+", base))
                    if runs:
                        # Manual readings apply to the first kanji run, with
                        # okurigana left outside the ruby span.
                        match = runs[0]
                        token_ranges.append((
                            position + match.start(),
                            position + match.end(),
                            str(reading).strip(),
                        ))
                position = end_position

            if position != len(line.text):
                return

            global_position = 0
            for source in old_segments:
                source_text = str(source.text or "")
                source_start = global_position
                source_end = source_start + len(source_text)
                spans = []

                for ruby_start, ruby_end, reading in token_ranges:
                    overlap_start = max(source_start, ruby_start)
                    overlap_end = min(source_end, ruby_end)
                    if overlap_start >= overlap_end:
                        continue
                    local_start = overlap_start - source_start
                    local_end = overlap_end - source_start
                    span_reading = reading
                    if (overlap_start != ruby_start or overlap_end != ruby_end) and split_reading_by_text_ranges is not None:
                        span_reading = split_reading_by_text_ranges(
                            line.text[ruby_start:ruby_end], reading,
                            [(overlap_start-ruby_start, overlap_end-ruby_start)],
                        )[0]
                    if span_reading:
                        spans.append((local_start, local_end, span_reading))

                source.ruby_spans = spans
                # Legacy whole-segment ruby remains available only when the
                # span covers the entire karaoke chunk.
                if len(spans) == 1 and spans[0][0] == 0 and spans[0][1] == len(source_text):
                    source.ruby = spans[0][2]
                    source.ruby_source = "manual"
                else:
                    source.ruby = None
                    source.ruby_source = None
                global_position = source_end

            line.has_karaoke = True
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
                    segments.append(replacement)
                offset = end
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
            for index, segment in enumerate(line.segments):
                if (
                    segment.ruby
                    and getattr(segment, "ruby_source", None) == "manual"
                ):
                    line.segments[index] = LyricSegment(
                        segment.start,
                        segment.text,
                        None,
                        segment.instant,
                        segment.end,
                        None,
                    )
                    changed = True
        if changed:
            self.document_dirty = True
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
                self.document_dirty = True
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
                    next_line.start = value
                    self.document_dirty = True
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
                    self.document_dirty = True
                    self._populate_lines(preserve_line_index=index + 1)
                    self._select_line_index(index + 1)
                    self.load_selected_line(self.line_list.currentRow())
            self._waveform_lyric_preview_handle = None
            self._waveform_lyric_preview_ms = None
            self._refresh_lyrics_waveform_markers()
            return

        position = float(self.line_time.value())
        changed = abs(float(line.start) - position) > 1e-9
        if changed:
            self._push_undo_state()
            line.start = position
            self.document_dirty = True
        else:
            # A second Commit is still a valid synchronization action. Keep
            # the editor state, marker positions, and playback-line cache
            # authoritative even when the timestamp is already equal.
            line.start = position
        timeline = sorted(enumerate(self.lines), key=lambda item: (item[1].start, item[0]))
        self._playback_line_starts = [line.start for _, line in timeline]
        self._playback_line_rows = [row for row, _ in timeline]
        self.refresh_line_label(index)
        self._refresh_lyrics_waveform_markers()
        self._refresh_karaoke_waveform_editor()
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
        line.text, line.style_spans, _offsets = parse_emphasis(markup)
        self.quote_button.setChecked(line.is_blockquote)
        # Text edits invalidate old segment boundaries rather than silently
        # leaving mismatched karaoke text behind.
        if line.segments and "".join(s.text for s in line.segments) != line.text:
            line.segments = [LyricSegment(line.start, line.text)]
            self._preview_segments_line_index = None
            line.has_karaoke = False
            self.loading = True
            if self.karaoke_enabled:
                self._populate_segments(line)
            self.loading = False
        elif line.segments:
            # Keep line.start independent from segment 0 so a leading pause is valid.
            pass
        self._mark_document_dirty()
        self.refresh_line_label(index)
        if self.karaoke_enabled:
            self.full_line_label.setText(line.text)
            self._build_separator_buttons(line)
        self._refresh_ruby_preview(line)
        self._update_lyric_search()
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
        index, _ = self._current_line()
        if index is None:
            return
        self._push_undo_state()
        del self.lines[index]
        self._populate_lines()
        if self.lines:
            self._select_line_index(min(index, len(self.lines) - 1))
        else:
            self.load_selected_line(-1)
        self._mark_document_dirty()

    def move_line(self, direction):
        if not self._commit_pending_source():
            return
        index, _ = self._current_line()
        if index is None:
            return
        target = index + direction
        if not (0 <= target < len(self.lines)):
            return
        self._push_undo_state()
        self.lines[index], self.lines[target] = self.lines[target], self.lines[index]
        self._populate_lines()
        self._select_line_index(target)
        self._mark_document_dirty()

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
        if self.translation_panel is not None and not self.translation_panel.confirm_close():
            return False
        self._closed = True
        if self._share_card is not None:
            self._share_card.shutdown()
        if self.translation_panel is not None:
            self.translation_panel.shutdown()
        if self._segment_preview_range is not None:
            media = getattr(self.player, "media_player", None)
            if media is not None:
                media.pause()
            self._segment_preview_range = None
        self._discard_autosave()
        self._reading_generation_epoch += 1
        self._autosave_timer.stop()
        self._line_reading_timer.stop()
        self._precise_display_timer.stop()
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
            ):
                try:
                    getattr(media, signal_name).disconnect(callback)
                except (AttributeError, RuntimeError, TypeError):
                    pass
        super().done(result)
        return True
