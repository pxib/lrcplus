from pathlib import Path
from bisect import bisect_right
import copy
import json
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
from lyrics.timing import parse_timestamp
from lyrics.editor_source import extract_metadata, parse_editor_source
from lyrics.karaoke_timing import next_lyric_start, segment_end
from core.debug import debug_print
from core.file_io import atomic_write_text
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


from ui.lrc_editor_ui import LrcEditorUiMixin
from ui.lrc_editor_controls import ClickableSlider, KaraokeSeparationWidget
from ui.lyrics_timing_tools import LyricsTimingToolsMixin
from ui.lyrics_editor_search import LyricsEditorSearchMixin
from ui.karaoke_editor_tools import KaraokeEditorToolsMixin

class LrcEditorDialog(KaraokeEditorToolsMixin, LyricsEditorSearchMixin, LyricsTimingToolsMixin, LrcEditorUiMixin, QDialog):
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
        self.karaoke_enabled = bool(
            parent is not None
            and getattr(parent, "plugin_manager", None) is not None
            and "karaoke" in parent.plugin_manager.plugins
        )
        self.karaoke_sweep_style = get_karaoke_settings().get(
            "sweep_style",
            "classic",
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
    # Document / structured lyrics
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Undo / Redo + crash recovery
    # ------------------------------------------------------------------
    def _document_state(self):
        return {
            "lines": copy.deepcopy(self.lines), "offset": self.lyrics_offset_ms,
            "mode": self._current_generation_mode(), "attachment": self.attached_song_path,
            "metadata": list(self._document_metadata),
        }

    def _capture_state(self, line_index=None):
        return {
            # Ordinary typing changes one line. Bulk tools still capture the
            # entire model, and history entries identify which kind they hold.
            "lines": copy.deepcopy(self.lines if line_index is None else [self.lines[line_index]]),
            "line_index": line_index,
            "offset": float(self.lyrics_offset_ms),
            "dirty": bool(self.document_dirty),
            "karaoke_dirty": bool(self.karaoke_dirty),
            "selected_line": self._current_line_index(),
            "source": self.source_editor.toPlainText() if hasattr(self, "source_editor") else "",
            "source_dirty": bool(self._source_dirty),
            "attached_song_path": self.attached_song_path,
            "source_suffix": self._source_suffix,
            "generation_mode": self._current_generation_mode(),
            "metadata": list(self._document_metadata),
        }

    def _restore_state(self, state):
        self._history_restoring = True
        self.loading = True
        try:
            if state.get("line_index") is None:
                self.lines = copy.deepcopy(state["lines"])
            else:
                self.lines[state["line_index"]] = copy.deepcopy(state["lines"][0])
            self.lyrics_offset_ms = float(state["offset"])
            self.document_dirty = bool(state.get("dirty", True))
            self.karaoke_dirty = bool(state.get("karaoke_dirty", False))
            self._source_dirty = bool(state.get("source_dirty", False))
            self.attached_song_path = state.get("attached_song_path")
            self._document_metadata = list(state.get("metadata", []))
            self._source_suffix = state.get("source_suffix", self._source_suffix)
            self.source_format_label.setText(f"{self._source_suffix[1:].upper()} source")
            self._apply_generation_mode(state.get("generation_mode"))
            if self._saved_document_state is not None:
                self.document_dirty = self._source_dirty or self._document_state() != self._saved_document_state
            if hasattr(self, "source_editor"):
                blocker = QSignalBlocker(self.source_editor)
                self.source_editor.setPlainText(state.get("source", ""))
                del blocker
            if hasattr(self, "lyrics_offset"):
                self.lyrics_offset.setValue(self.lyrics_offset_ms)
            self._populate_lines()
            row = min(
                max(0, int(state.get("selected_line") or 0)),
                max(0, len(self.lines) - 1),
            )
            if self.lines:
                self._select_line_index(row)
                self.load_selected_line(self.line_list.currentRow())
            else:
                self._clear_editor_views()
        finally:
            self.loading = False
            self._history_restoring = False
        self._update_history_actions()
        self._update_save_button_state()
        self._update_document_title()
        self._update_song_association_ui()

    def _clear_editor_views(self):
        if hasattr(self, "line_text"):
            self.line_text.clear()
        if hasattr(self, "segment_table"):
            self.segment_table.setRowCount(0)
        if hasattr(self, "separator_widget"):
            self.separator_widget.set_content("", set())

    def _reset_history_baseline(self):
        self._undo_stack = []
        self._redo_stack = []
        if not self.document_dirty:
            self._saved_document_state = self._document_state()
        self._update_history_actions()

    def _push_undo_state(self, line_index=None):
        if self.loading or self._history_restoring:
            return
        # This is called immediately before a mutation. Keep even an equal
        # snapshot: it is the explicit checkpoint Undo should return to.
        state = self._capture_state(line_index)
        self._undo_stack.append(state)
        if len(self._undo_stack) > self._history_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_history_actions()

    def undo(self):
        if self._source_dirty and self.source_editor.hasFocus():
            self.source_editor.undo()
            return
        if not self._undo_stack:
            return
        current = self._capture_state(self._undo_stack[-1].get("line_index"))
        self._redo_stack.append(current)
        self._restore_state(self._undo_stack.pop())

    def redo(self):
        if self._source_dirty and self.source_editor.hasFocus():
            self.source_editor.redo()
            return
        if not self._redo_stack:
            return
        self._undo_stack.append(self._capture_state(self._redo_stack[-1].get("line_index")))
        state = self._redo_stack.pop()
        self._restore_state(state)

    def _update_history_actions(self):
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(bool(self._undo_stack))
        if hasattr(self, "redo_button"):
            self.redo_button.setEnabled(bool(self._redo_stack))

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

    def _autosave_payload(self):
        structured = self._serialise_with_attachment(serialize_lrcx(
            self.lines, offset_ms=self.lyrics_offset_ms,
            generation_mode=self._current_generation_mode(),
        ))
        return json.dumps({
            "format": "LyricsPlusRecovery", "version": 1,
            "structured": structured,
            "source": self.source_editor.toPlainText() if self._source_dirty else None,
            "source_suffix": self._source_suffix,
        }, ensure_ascii=False)

    def _autosave_if_needed(self):
        if not self.document_dirty:
            return
        try:
            contents = self._autosave_payload()
            tmp = self._autosave_path.with_suffix(self._autosave_path.suffix + ".tmp")
            tmp.write_text(contents, encoding="utf-8")
            os.replace(tmp, self._autosave_path)
        except Exception as error:
            debug_print(f"[Lyrics Editor] Autosave failed: {error!r}")

    def _restore_autosave_if_available(self):
        if not self._autosave_path.exists():
            return
        try:
            source_mtime = self.lrc_path.stat().st_mtime if self.lrc_path.exists() else 0
            if self._autosave_path.stat().st_mtime <= source_mtime:
                self._autosave_path.unlink(missing_ok=True)
                return
            answer = QMessageBox.question(
                self, "Recover Unsaved Lyrics?",
                "A newer crash-recovery copy was found for this lyric file.\n\nRestore those unsaved changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._discard_autosave()
                return
            contents = self._autosave_path.read_text(encoding="utf-8")
            if contents.lstrip().startswith("{"):
                payload = json.loads(contents)
                if payload.get("format") != "LyricsPlusRecovery" or payload.get("version") != 1:
                    raise ValueError("Unsupported recovery format")
                recovered_source = payload.get("source")
                suffix = payload.get("source_suffix", ".lrcx")
                if recovered_source is not None:
                    if not isinstance(recovered_source, str) or suffix not in {".lrc", ".lrcx", ".txt"}:
                        raise ValueError("Invalid source recovery data")
                    self._pending_recovered_source = (recovered_source, suffix)
                contents = payload["structured"]
            recovery = parse_editor_source(contents, ".lrcx")
            self.lines = recovery.lines
            self.lyrics_offset_ms = recovery.offset_ms
            self.attached_song_path = recovery.attachment
            self._document_metadata = recovery.metadata
            self._apply_generation_mode(recovery.generation_mode)
            self.document_dirty = True
            self.karaoke_dirty = any(line.has_karaoke for line in self.lines)
        except (OSError, ValueError, TypeError, KeyError) as error:
            debug_print(f"[Lyrics Editor] Recovery check failed: {error!r}")

    def _discard_autosave(self):
        try:
            self._autosave_path.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _read_attached_song_path(path):
        """Read the optional [attach:<song path>] metadata tag."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        match = re.search(r"(?mi)^\[attach:(.*?)\]\s*$", text)
        if not match:
            return None
        value = match.group(1).strip()
        return value or None

    @staticmethod
    def _normalise_song_path(path):
        if not path:
            return ""
        try:
            return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))
        except (OSError, ValueError) as error:
            # Fall back to the raw string only for genuinely unresolvable
            # paths (e.g. malformed input); anything else should surface.
            debug_print(f"[Lyrics Editor] Could not normalise path {path!r}: {error!r}")
            return str(path)

    def _current_song_path(self):
        path = getattr(self.player, "current_audio_path", None)
        return Path(path) if path else None

    def _serialise_with_attachment(self, contents):
        """Persist the optional song association as ordinary lyrics metadata."""
        lines = [
            line for line in str(contents).splitlines()
            if not re.match(r"(?i)^\[attach:.*\]\s*$", line)
        ]
        lines = list(self._document_metadata) + lines
        if self.attached_song_path:
            lines.insert(0, f"[attach:{self.attached_song_path}]")
        return "\n".join(lines) + ("\n" if lines else "")

    def _update_song_association_ui(self):
        if not hasattr(self, "song_association_label"):
            return
        current = self._current_song_path()
        if current is not None:
            forced = get_lyrics_attachment(current)
            if forced:
                self.song_association_label.setText(str(current))
                self.song_association_label.setToolTip(str(current))
                self.force_song_button.setText("Remove Song Association")
                self.song_association_status.setText(f"Forced lyrics file: {forced}")
                if hasattr(self, "open_song_file_button"):
                    self.open_song_file_button.setEnabled(True)
                return

        if self.attached_song_path:
            self.song_association_label.setText(self.attached_song_path)
            self.song_association_label.setToolTip(self.attached_song_path)
            self.force_song_button.setText("Remove Song Association")
            self.song_association_status.setText("This lyrics file is forced for this song.")
            if hasattr(self, "open_song_file_button"):
                self.open_song_file_button.setEnabled(True)
        else:
            current = self._current_song_path()
            if current is not None:
                text = str(current)
                self.song_association_label.setText(text)
                self.song_association_label.setToolTip(text)
                self.force_song_button.setText("Force This Lyrics File for This Song")
                self.song_association_status.setText(
                    "Not forced. Enable this to always use this lyrics file for the current song."
                )
            else:
                self.song_association_label.setText("No song is currently loaded.")
                self.force_song_button.setText("Force This Lyrics File for This Song")
                self.song_association_status.setText("Load a song to create an association.")
            self.force_song_button.setEnabled(current is not None)
            if hasattr(self, "open_song_file_button"):
                self.open_song_file_button.setEnabled(True)

    def toggle_song_association(self):
        current_song = self._current_song_path()
        affected_song = current_song
        if current_song is not None and get_lyrics_attachment(current_song):
            set_lyrics_attachment(current_song, None)
            if self.attached_song_path and self._normalise_song_path(self.attached_song_path) == self._normalise_song_path(current_song):
                self.attached_song_path = None
        elif self.attached_song_path:
            previous_song = self.attached_song_path
            affected_song = previous_song
            self.attached_song_path = None
            set_lyrics_attachment(previous_song, None)
        else:
            song = current_song
            if song is None:
                QMessageBox.information(
                    self,
                    "Song Association",
                    "Choose a song file first, or load a song before associating this lyrics file.",
                )
                return
            self.attached_song_path = str(Path(song).resolve())
            # New documents have no on-disk lyrics file yet. Keep the
            # association in the draft and persist it together with the first
            # explicit Save; never point the song at a phantom path.
            if not self.is_new_document:
                set_lyrics_attachment(self.attached_song_path, self.lrc_path)
        # Keep a small persisted index as well as the [attach:...] metadata.
        # The metadata remains portable/source-visible; the index allows the
        # player to find this lyrics file even when it sits outside lyric search folders.
        self._mark_document_dirty()
        self._association_changed_song = affected_song
        self._update_song_association_ui()
        if hasattr(self, "source_editor"):
            self.sync_source_from_model()
        if not self.is_new_document or not self.attached_song_path:
            self._refresh_player_song_association(affected_song)

    def _refresh_player_song_association(self, song):
        """Apply a persisted association after the editor action completes."""
        parent = self.player
        reload_renderer = getattr(parent, "reload_renderer", None)
        if song is None or not callable(reload_renderer):
            return
        clear_index = getattr(parent, "clear_lyrics_search_index", None)
        if callable(clear_index):
            clear_index()

        def refresh():
            # Playback can advance while the editor is open. A queued action
            # for the previous song must never replace the new song's lyrics.
            current = getattr(parent, "current_audio_path", None)
            if self._normalise_song_path(current) != self._normalise_song_path(song):
                return
            reload_renderer(rediscover=True)

        QTimer.singleShot(0, refresh)

    def _update_document_title(self):
        """Reflect real-file vs new-draft state in the editor title bar."""
        if self.is_new_document and not self.document_dirty:
            title = "No lyrics associated"
        elif self.is_new_document:
            title = "Untitled Lyrics • Unsaved"
        else:
            title = self.lrc_path.name
            if self.document_dirty:
                title += " • Unsaved"
        self.setWindowTitle(f"Lyrics Editor 2.0 — {title}")

    def open_lyrics_file(self):
        target = getattr(self, "result_path", None)
        if not target or not Path(target).exists():
            QMessageBox.information(
                self,
                "Open Lyrics File",
                "This lyrics document has not been saved yet.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(target).resolve())))

    def choose_lyrics_file_for_current_song(self):
        """Choose any lyrics file and force it for the currently loaded song."""
        song = self._current_song_path()
        if song is None:
            QMessageBox.information(
                self,
                "Song Association",
                "Load a song first, then choose the lyrics file to use for it.",
            )
            return

        current_target = get_lyrics_attachment(song) or str(self.lrc_path)
        directory = str(Path(current_target).parent) if current_target else str(song.parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Lyrics File for This Song",
            directory,
            "Lyrics Files (*.lrc *.lrcx);;All Files (*)",
        )
        if not path:
            return

        lyrics_path = Path(path).resolve()
        set_lyrics_attachment(song, lyrics_path)

        # If the chosen file is the one currently being edited, keep the
        # portable [attach:] metadata in sync on the next save. If it is an
        # external lyrics file, the persisted song -> lyrics mapping is already
        # authoritative, so we do not modify a document the user did not open.
        if lyrics_path == self.lrc_path.resolve():
            self.attached_song_path = str(Path(song).resolve())
        elif self._normalise_song_path(self.attached_song_path) == self._normalise_song_path(song):
            # Saving the old document must not restore its former association
            # over the external file the user just selected.
            self.attached_song_path = None
        if hasattr(self, "source_editor"):
            self.sync_source_from_model()

        # The association is persisted immediately, but this is still an editor
        # action and Save must become available rather than appearing inert.
        self._mark_document_dirty()
        self._association_changed_song = song
        self._update_song_association_ui()

        self._refresh_player_song_association(song)

    def _load_document(self):
        if not self.lrc_path.exists():
            self.lines = []
            return
        try:
            self.attached_song_path = self._read_attached_song_path(self.lrc_path)
            self.lines = load_lyrics(self.lrc_path)
            if self.lrc_path.suffix.lower() != ".txt":
                self._document_metadata = extract_metadata(self.lrc_path.read_text(encoding="utf-8-sig"))
            persisted_mode = get_lyrics_generation_mode(self.lrc_path)
            self._apply_generation_mode(persisted_mode)
        except Exception as error:
            QMessageBox.warning(self, "Lyrics Editor", f"Could not parse lyrics:\n{error}")
            self.lines = []

    def _mark_document_dirty(self):
        """Mark the document modified and update Save immediately."""
        self.document_dirty = True
        self._update_save_button_state()
        self._update_document_title()

    def _source_text_changed(self):
        if self.loading or getattr(self, "_syncing_source", False):
            return
        self._source_dirty = True
        self._mark_document_dirty()

    def _update_save_button_state(self):
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(bool(self.document_dirty))

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
        # Source is a full-width raw workspace.  Hide the entire navigation
        # panel, including the oversized "Go to Current Lyric" button.
        panel = getattr(self, "line_list_panel", None)
        if panel is not None:
            panel.setVisible(index != source_index)
        else:
            self.line_list.setVisible(index != source_index)
        self._set_share_selection_active(
            index == self.tabs.indexOf(self.share_page)
        )
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

    def _update_karaoke_sweep_style_button(self):
        button = getattr(self, "karaoke_sweep_style_button", None)
        if button is not None:
            button.setText("Properties")

    def _apply_karaoke_sweep_style(self):
        style = str(
            getattr(self, "karaoke_sweep_style", "classic")
        ).lower()
        if style not in {"classic", "soft", "glow", "shimmer"}:
            style = "classic"
            self.karaoke_sweep_style = style

        # Persist the style globally, since this property belongs to the
        # karaoke renderer rather than an individual lyric line.
        current = get_karaoke_settings()
        save_karaoke_settings({
            "sweep_fps": current.get("sweep_fps", 60),
            "sweep_style": style,
            "sweep_easing": current.get("sweep_easing", "linear"),
            "softness": current.get("softness", 8),
            "glow_intensity": current.get("glow_intensity", 70),
            "glow_radius": current.get("glow_radius", 1),
            "shimmer_width": current.get("shimmer_width", 10),
            "sweep_delay_ms": current.get("sweep_delay_ms", 100),
            "sweep_delay_percent": current.get(
                "sweep_delay_percent", 10
            ),
        })

        current = get_karaoke_settings()
        preview = getattr(self, "karaoke_renderer_preview", None)
        if preview is not None:
            preview.set_karaoke_properties(
                current["sweep_style"],
                current["sweep_easing"],
                current["softness"],
                current["glow_intensity"],
                current["glow_radius"],
                current.get("shimmer_width", 10),
                current.get("sweep_delay_ms", 100),
                current.get("sweep_delay_percent", 10),
            )

        main_window = self.player
        if main_window is not None:
            for widget in (
                getattr(main_window, "lyric_widget", None),
                getattr(main_window, "scrolling_lyrics_widget", None),
            ):
                if widget is not None and hasattr(
                    widget,
                    "set_karaoke_sweep_style",
                ):
                    current = get_karaoke_settings()
                    if hasattr(widget, "set_karaoke_properties"):
                        widget.set_karaoke_properties(
                            current["sweep_style"],
                            current["sweep_easing"],
                            current["softness"],
                            current["glow_intensity"],
                            current["glow_radius"],
                            current.get("shimmer_width", 10),
                            current.get("sweep_delay_ms", 100),
                            current.get("sweep_delay_percent", 10),
                        )
                    else:
                        widget.set_karaoke_sweep_style(style)

    def _show_karaoke_properties(self):
        settings = get_karaoke_settings()

        dialog = QDialog(self)
        dialog.setWindowTitle("Karaoke Properties")
        dialog.setMinimumWidth(360)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        style = QComboBox()
        style.addItem("Classic", "classic")
        style.addItem("Soft", "soft")
        style.addItem("Glow", "glow")
        style.addItem("Shimmer", "shimmer")
        style.addItem("Shimmer", "shimmer")
        style.setCurrentIndex(
            max(0, style.findData(settings.get("sweep_style", "classic")))
        )
        form.addRow("Sweep style:", style)

        easing = QComboBox()
        for label, value in (
            ("Linear", "linear"),
            ("Ease In", "ease_in"),
            ("Ease Out", "ease_out"),
            ("Ease In-Out", "ease_in_out"),
        ):
            easing.addItem(label, value)
        easing.setCurrentIndex(
            max(0, easing.findData(settings.get("sweep_easing", "linear")))
        )
        form.addRow("Easing:", easing)

        softness = QSlider(Qt.Orientation.Horizontal)
        softness.setRange(1, 40)
        softness.setValue(settings.get("softness", 8))
        softness_value = QLabel()
        softness_row = QHBoxLayout()
        softness_row.addWidget(softness)
        softness_row.addWidget(softness_value)
        softness_widget = QWidget()
        softness_widget.setLayout(softness_row)
        form.addRow("Edge softness:", softness_widget)

        glow_intensity = QSlider(Qt.Orientation.Horizontal)
        glow_intensity.setRange(0, 255)
        glow_intensity.setValue(settings.get("glow_intensity", 70))
        glow_intensity_value = QLabel()
        glow_intensity_row = QHBoxLayout()
        glow_intensity_row.addWidget(glow_intensity)
        glow_intensity_row.addWidget(glow_intensity_value)
        glow_intensity_widget = QWidget()
        glow_intensity_widget.setLayout(glow_intensity_row)
        form.addRow("Glow intensity:", glow_intensity_widget)

        glow_radius = QSlider(Qt.Orientation.Horizontal)
        glow_radius.setRange(1, 8)
        glow_radius.setValue(settings.get("glow_radius", 1))
        glow_radius_value = QLabel()
        glow_radius_row = QHBoxLayout()
        glow_radius_row.addWidget(glow_radius)
        glow_radius_row.addWidget(glow_radius_value)
        glow_radius_widget = QWidget()
        glow_radius_widget.setLayout(glow_radius_row)
        form.addRow("Glow radius:", glow_radius_widget)

        shimmer_width = QSlider(Qt.Orientation.Horizontal)
        shimmer_width.setRange(1, 40)
        shimmer_width.setValue(settings.get("shimmer_width", 10))
        shimmer_width_value = QLabel()
        shimmer_width_row = QHBoxLayout()
        shimmer_width_row.addWidget(shimmer_width)
        shimmer_width_row.addWidget(shimmer_width_value)
        shimmer_width_widget = QWidget()
        shimmer_width_widget.setLayout(shimmer_width_row)
        form.addRow("Shimmer width:", shimmer_width_widget)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Close
        )
        layout.addWidget(buttons)

        def refresh_labels():
            softness_value.setText(f"{softness.value()}%")
            glow_intensity_value.setText(str(glow_intensity.value()))
            glow_radius_value.setText(f"{glow_radius.value()} px")
            shimmer_width_value.setText(f"{shimmer_width.value()} px")

        def refresh_visibility():
            selected = style.currentData()
            softness_widget.setVisible(selected == "soft")
            form.labelForField(softness_widget).setVisible(selected == "soft")
            is_glow = selected == "glow"
            glow_intensity_widget.setVisible(is_glow)
            form.labelForField(glow_intensity_widget).setVisible(is_glow)
            glow_radius_widget.setVisible(is_glow)
            form.labelForField(glow_radius_widget).setVisible(is_glow)
            is_shimmer = selected == "shimmer"
            shimmer_width_widget.setVisible(is_shimmer)
            form.labelForField(shimmer_width_widget).setVisible(is_shimmer)

        def apply_properties():
            values = {
                "sweep_fps": settings.get("sweep_fps", 60),
                "sweep_style": style.currentData(),
                "sweep_easing": easing.currentData(),
                "softness": softness.value(),
                "glow_intensity": glow_intensity.value(),
                "glow_radius": glow_radius.value(),
                "shimmer_width": shimmer_width.value(),
            }
            save_karaoke_settings(values)
            self.karaoke_sweep_style = values["sweep_style"]
            self._apply_karaoke_sweep_style()
            self._refresh_karaoke_preview()

        refresh_labels()
        refresh_visibility()
        style.currentIndexChanged.connect(refresh_visibility)
        softness.valueChanged.connect(lambda _: refresh_labels())
        glow_intensity.valueChanged.connect(lambda _: refresh_labels())
        glow_radius.valueChanged.connect(lambda _: refresh_labels())
        shimmer_width.valueChanged.connect(lambda _: refresh_labels())
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            apply_properties
        )
        buttons.rejected.connect(dialog.close)

        dialog.exec()

    def _select_karaoke_sweep_style(self, style):
        self.karaoke_sweep_style = str(style or "classic").lower()
        self._apply_karaoke_sweep_style()
        self._refresh_karaoke_preview()

    def _populate_lines(self, preserve_line_index=None):
        """Refresh the sidebar as a view of canonical document order.

        Sorting changes only the list presentation. self.lines is never sorted.
        """
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
        if not selected_indices:
            return list(lines)
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
            label.setText("No lines checked; sharing all lyrics.")

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

        position = position_ms / 1000.0
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
            position = self.player.media_player.position() / 1000.0
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
            name="LyricsPlusReadingGeneration",
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
        if self._closed or self._current_generation_mode() != "auto":
            return
        if not (0 <= line_index < len(self.lines)):
            return
        if self._current_line_index() != line_index:
            return

        text = str(self.lines[line_index].text or "")
        if text:
            self._start_background_reading_generation([text], line_index)

    def load_selected_line(self, row):
        self._karaoke_bounds_warning = None
        if 0 <= row < len(self._view_line_indices):
            row = self._view_line_indices[row]
        else:
            row = -1
        self.loading = True
        if self.karaoke_enabled:
            self.segment_table.setRowCount(0)
            self.full_line_label.setText("")
            self.separation_preview.clear()
            self._clear_separator_buttons()
        if not (0 <= row < len(self.lines)):
            self.line_time.setValue(0)
            self.line_text.clear()
            if hasattr(self, "lyrics_waveform"):
                self.lyrics_waveform.clear_selected_lyric_timing()
            self.loading = False
            return
        line = self.lines[row]
        self._update_detected_language()
        self.line_time.setValue(line.start)
        self.line_text.setPlainText(line.text)
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

        # Run Auto providers once the selection has settled.  This is important
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
            segments = line.segments or [LyricSegment(line.start, line.text)]
            line_index = self._current_line_index()
            self.segment_table.setRowCount(len(segments))
            for row, segment in enumerate(segments):
                self.segment_table.setItem(
                    row, 0, QTableWidgetItem(format_timestamp(segment.start))
                )
                if getattr(segment, "end", None) is not None:
                    end_time = segment.end
                elif row + 1 < len(segments):
                    end_time = segments[row + 1].start
                elif line.end is not None:
                    end_time = line.end
                elif 0 <= line_index < len(self.lines) - 1:
                    end_time = self.lines[line_index + 1].start
                else:
                    end_time = segment.start
                self.segment_table.setItem(
                    row, 1, QTableWidgetItem(format_timestamp(end_time))
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
        lines = tuple(str(line.text or "") for line in self.lines if str(line.text or ""))
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
                    if (provider_id := find_reading_provider(text, language="unknown"))
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
            tokens = build_furigana_segments(source_text, force=True)
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

        generated_lines = 0
        errors = 0
        for line in self.lines:
            source_text = str(line.text or "")
            if not source_text:
                continue
            try:
                tokens = build_furigana_segments(source_text, force=True)
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
            [line.text for line in self.lines if line.text],
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

        try:
            tokens = build_furigana_segments(text) if build_furigana_segments is not None else []
        except Exception:
            tokens = []

        if not tokens:
            tokens = [{"text": text, "reading": None}]

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
        stored = self._stored_reading_tokens(line)
        if stored:
            self.ruby_preview.setText("  ".join(
                f"{part['text']}({part['reading']})"
                if part.get("reading") else part["text"]
                for part in stored
            ))
            return

        if build_furigana_segments is not None and line.text:
            try:
                parts = build_furigana_segments(line.text)
                self.ruby_preview.setText("  ".join(
                    f"{part.get('text', '')}({part.get('reading')})"
                    if part.get("reading") else part.get("text", "")
                    for part in parts
                ))
                return
            except Exception:
                pass

        self.ruby_preview.setText(
            "No stored readings. Automatic generation follows the selected mode."
        )

    def offset_changed(self, value):
        if self.loading:
            return
        self._push_undo_state()
        self.lyrics_offset_ms = float(value)
        self._mark_document_dirty()

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

            # Prefer readings already attached to the line. This is essential
            # for automatic provider generation: those generated segments have already
            # been produced successfully, and regenerating here used to replace
            # them with the generic fallback and show one empty row.
            tokens = self._stored_reading_tokens(line)
            if not tokens:
                try:
                    tokens = (
                        build_furigana_segments(line.text)
                        if line.text else []
                    )
                except Exception:
                    tokens = []

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
        answer = QMessageBox.question(
            self,
            "Reset All Readings",
            "Remove all readings from every lyric line? Automatic readings can be generated again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_state()
        changed = False
        for line in self.lines:
            for index, segment in enumerate(line.segments or []):
                if segment.ruby or getattr(segment, "ruby_source", None):
                    line.segments[index] = LyricSegment(
                        segment.start, segment.text, None, segment.instant,
                        segment.end, None,
                    )
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
        position = self.player.media_player.position() / 1000.0
        self.loading = True
        self.line_time.setValue(position)
        self.loading = False
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

    def line_details_changed(self):
        if self.loading:
            return
        index, line = self._current_line()
        if line is None:
            return
        self._push_undo_state(line_index=index)
        delta = self.line_time.value() - line.start
        line.start = self.line_time.value()
        line.text = self.line_text.toPlainText()
        if not line.has_karaoke:
            for segment in line.segments:
                segment.start += delta
                if segment.end is not None:
                    segment.end += delta
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
    # Separation / karaoke
    # ------------------------------------------------------------------
    def _boundaries_from_parts(self, text, parts):
        boundaries = set()
        offset = 0
        for part in parts[:-1]:
            offset += len(part)
            if 0 < offset < len(text):
                boundaries.add(offset)
        return boundaries

    def _build_separator_buttons(self, line, parts=None):
        """Compatibility entry point for refreshing the visual separator.

        The old implementation created one button per character and separator.
        The new widget stores only boundary character positions.
        """
        if not hasattr(self, "separator_widget"):
            return
        text = line.text
        if parts is None:
            # Generated ruby tokens are allowed to split line.segments for
            # rendering, but they are not karaoke boundaries. The karaoke UI
            # must only expose real, explicitly enabled karaoke structure.
            if getattr(line, "has_karaoke", False) and line.segments:
                parts = [segment.text for segment in line.segments]
            else:
                parts = [text]
        self.separator_widget.set_content(text, self._boundaries_from_parts(text, parts))
        self.refresh_separation_preview()

    def _clear_separator_buttons(self):
        if hasattr(self, "separator_widget"):
            self.separator_widget.set_content("", set())

    def _visual_parts(self):
        _, line = self._current_line()
        if line is None or not line.text:
            return []
        text = line.text
        boundaries = self.separator_widget.boundaries() if hasattr(self, "separator_widget") else set()
        for index, char in enumerate(text):
            if char == "\n":
                boundaries.update({index, index + 1})
        parts, start = [], 0
        for boundary in sorted(boundaries):
            parts.append(text[start:boundary])
            start = boundary
        parts.append(text[start:])
        return [part for part in parts if part]

    @staticmethod
    def _merge_whitespace_only_karaoke_parts(parts):
        """Keep structural newlines separate, attaching other spaces to text."""
        merged = []
        pending_prefix = ""
        for source_part in parts:
            for part in re.split(r"(\n)", str(source_part)):
                if not part:
                    continue
                if part == "\n":
                    if pending_prefix:
                        merged.append(pending_prefix)
                        pending_prefix = ""
                    merged.append(part)
                elif not part.strip():
                    if merged and merged[-1] != "\n":
                        merged[-1] += part
                    else:
                        pending_prefix += part
                else:
                    merged.append(pending_prefix + part)
                    pending_prefix = ""
        if pending_prefix:
            if merged and merged[-1] != "\n":
                merged[-1] += pending_prefix
            else:
                merged.append(pending_prefix)
        return merged

    def refresh_separation_preview(self, *args):
        if not self.karaoke_enabled or not hasattr(self, "separation_preview"):
            return
        parts = self._visual_parts()
        self.separation_preview.setText("  |  ".join(f"[ {part} ]" for part in parts))

    def auto_separate_current_line(self):
        _, line = self._current_line()
        if line is None:
            return

        text = line.text
        parts = []

        # First use the normal tokenizer.
        if yomi_word_surfaces is not None:
            try:
                parts = yomi_word_surfaces(text)
            except Exception as error:
                debug_print(f"[Yomi] Auto-separation failed: {error!r}")
                parts = []

        # Do not import or call PyCantonese from the UI process. Its tokenizer
        # stack is intentionally isolated in the Cantonese provider worker.
        # If the generic tokenizer cannot segment this line, keep the existing
        # safe fallback below rather than risking a process-level crash.
        fallback_is_character_split = (
            parts
            and "".join(parts) == text
            and len(parts) == len(text)
            and all(len(part) <= 1 for part in parts)
        )

        if not parts or "".join(parts) != text:
            # English/Latin lyrics should separate by words, not characters.
            # Keep punctuation attached to the surrounding word and preserve
            # the original whitespace so the generated parts still reconstruct
            # the exact lyric text.  A boundary is placed at each whitespace
            # run between two non-whitespace chunks.
            latin_word_match = re.search(r"[A-Za-z]", text) is not None
            non_han_only = not any(("\u3400" <= ch <= "\u4dbf") or ("\u4e00" <= ch <= "\u9fff") or ("\uf900" <= ch <= "\ufaff") or ("\u3040" <= ch <= "\u30ff") for ch in text)
            if latin_word_match and non_han_only and re.search(r"\S+\s+\S+", text):
                parts = re.split(r"(?<=\S)(?=\s+\S)", text)
                debug_print(f"[Karaoke] Auto-separated Latin text by words: {parts!r}")
            else:
                debug_print(f"[Karaoke] Auto-separation fell back to character split for {text!r}")
                parts = [char for char in text if char]
        parts = self._merge_whitespace_only_karaoke_parts(parts)
        self._build_separator_buttons(line, parts)
        self.karaoke_hint.setText("Automatic separation ready. Click any gap in the lyric to refine the boundaries.")

    def _update_overlap_warning(self, line=None):
        if line is None:
            _, line = self._current_line()
        if line is None:
            self.overlap_warning.setVisible(False)
            return

        segments = line.segments or []
        problems = []

        # Whitespace segments are real LRCX data, but the Karaoke editor hides
        # them as visual separators. Ignore them here too: a space should not
        # generate an overlap warning, nor should it break the comparison chain
        # between the surrounding visible segments.
        visible = [
            (i, segment)
            for i, segment in enumerate(segments)
            if str(getattr(segment, "text", "")).strip()
        ]

        for visible_index, (i, segment) in enumerate(visible):
            start = getattr(segment, "start", None)
            end = getattr(segment, "end", None)

            # Explicit end before its own start.
            if start is not None and end is not None and end < start:
                problems.append(i)
                continue

            following = next(
                ((row, item) for row, item in visible[visible_index + 1:]
                 if item.visual_line == segment.visual_line),
                None,
            )
            if following is None:
                continue

            next_row, next_segment = following
            next_start = getattr(next_segment, "start", None)

            # Existing ordering check: later starts cannot go backwards.
            if (
                start is not None
                and next_start is not None
                and next_start < start
            ):
                problems.append(next_row)

            # This segment's explicit End cannot extend beyond the next
            # *visible* segment's Start. Hidden whitespace is ignored.
            if (
                end is not None
                and next_start is not None
                and next_start < end
            ):
                problems.append(next_row)

        bounds_warning = getattr(self, "_karaoke_bounds_warning", None)
        if bounds_warning:
            self.overlap_warning.setText(bounds_warning)
            self.overlap_warning.setVisible(True)
        elif problems:
            rows = ", ".join(str(i + 1) for i in sorted(set(problems)))
            self.overlap_warning.setText(
                f"⚠ Overlapping / out-of-order karaoke timing near segment row(s): {rows}. "
                "A segment ends after the next one starts, or timestamps are out of order."
            )
            self.overlap_warning.setVisible(True)
        else:
            self.overlap_warning.setVisible(False)

    def clear_all_karaoke(self):
        if not self.lines:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Clear Karaoke")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Clear karaoke timing from every lyric line?")
        box.setInformativeText(
            "All karaoke segment start times and instant-fill flags will be reset. "
            "Lyric text and ruby readings are kept."
        )
        clear_button = box.addButton("Clear Karaoke", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not clear_button:
            return
        self._push_undo_state()
        for line in self.lines:
            # Karaoke boundaries and ruby spans are independent layers. Before
            # collapsing the karaoke chunks, collect every ruby span in global
            # lyric-text coordinates, then attach those same spans to the new
            # single segment. Resetting karaoke must never reset readings.
            preserved_spans = []
            offset = 0
            for segment in line.segments:
                segment_text = str(getattr(segment, "text", "") or "")
                spans = list(getattr(segment, "ruby_spans", []) or [])
                if spans:
                    for a, b, reading in spans:
                        if reading and 0 <= a < b <= len(segment_text):
                            preserved_spans.append((offset + a, offset + b, reading))
                else:
                    ruby = getattr(segment, "ruby", None)
                    if ruby and segment_text:
                        preserved_spans.append((offset, offset + len(segment_text), ruby))
                offset += len(segment_text)

            collapsed = LyricSegment(
                line.start,
                line.text,
                None,
                False,
                getattr(line, "end", None),
                None,
            )
            collapsed.ruby_spans = preserved_spans
            if len(preserved_spans) == 1 and preserved_spans[0][:2] == (0, len(line.text)):
                collapsed.ruby = preserved_spans[0][2]
                collapsed.ruby_source = "manual"
            line.segments = [collapsed]
            line.has_karaoke = False
        self.karaoke_dirty = True
        self._mark_document_dirty()
        row = self.line_list.currentRow()
        if row >= 0:
            self.load_selected_line(row)
        self.karaoke_hint.setText("Karaoke timing cleared. Ruby and lyric text were kept.")

    def reset_current_separation(self):
        _, line = self._current_line()
        if line is not None:
            self._build_separator_buttons(line, [line.text])

    def apply_visual_separation(self):
        index, line = self._current_line()
        if line is None:
            return

        parts = self._merge_whitespace_only_karaoke_parts(
            self._visual_parts()
        )
        if not parts or "".join(parts) != line.text:
            self.karaoke_hint.setText(
                "The visual separation no longer matches the lyric text."
            )
            return

        self._push_undo_state()
        old = list(line.segments) or [
            LyricSegment(line.start, line.text)
        ]

        # Karaoke boundaries and reading boundaries are deliberately separate.
        #
        # `parts` is the user's/automatic separator result and is the ONLY
        # source of karaoke chunk boundaries here.  Automatic furigana may
        # internally tokenize the same text much more finely (sometimes even
        # character-by-character), but those tokens must never become timed
        # karaoke chunks.
        old_ranges = []
        offset = 0
        for segment in old:
            end_offset = offset + len(segment.text)
            old_ranges.append((offset, end_offset, segment))
            offset = end_offset

        def source_for(position):
            return next(
                (
                    segment
                    for a, b, segment in old_ranges
                    if a <= position < b
                ),
                old[-1],
            )

        # Preserve an existing ruby value only when a karaoke chunk exactly
        # matches the stored ruby segment.  Generated readings do not need to
        # be copied into karaoke structure: the renderer generates/maps them
        # from the complete lyric while keeping the timing chunk intact.
        exact_ruby = {}
        for a, b, segment in old_ranges:
            ruby = getattr(segment, "ruby", None)
            if ruby:
                exact_ruby[(a, b)] = (
                    ruby,
                    getattr(segment, "ruby_source", None),
                )

        boundaries = [0]
        offset = 0
        for part in parts:
            offset += len(part)
            boundaries.append(offset)

        # Fresh splits must partition their parent's timing span instead of
        # giving every newly-created chunk the same start timestamp.
        next_line_start = next_lyric_start(self.lines, line)

        # LyricSegment is mutable/unhashable, so use its object identity as
        # the grouping key rather than putting the segment itself in a dict.
        source_split_groups = {}
        source_by_id = {id(segment): segment for segment in old}

        for a, b in zip(boundaries, boundaries[1:]):
            if a < b:
                source_id = id(source_for(a))
                source_split_groups.setdefault(source_id, []).append((a, b))

        source_allocations = {}
        for source_id, ranges in source_split_groups.items():
            source = source_by_id[source_id]
            source_range = next(
                ((x, y) for x, y, segment in old_ranges if segment is source),
                None,
            )
            if source_range is None or (len(ranges) == 1 and ranges[0] == source_range):
                continue

            start_time = float(source.start)
            end_time = getattr(source, "end", None)
            end_time = float(end_time) if end_time is not None else None

            # No explicit end: use the next original segment's start.
            if end_time is None or end_time <= start_time:
                source_index = next(
                    (i for i, segment in enumerate(old) if segment is source),
                    -1,
                )
                if 0 <= source_index < len(old) - 1:
                    candidate = float(old[source_index + 1].start)
                    if candidate > start_time:
                        end_time = candidate

            if (end_time is None or end_time <= start_time) and line.end is not None:
                if line.end > start_time:
                    end_time = float(line.end)

            # Final original segment: its natural boundary is the next lyric.
            if (end_time is None or end_time <= start_time) and next_line_start is not None:
                if next_line_start > start_time:
                    end_time = next_line_start

            if end_time is None or end_time <= start_time:
                continue

            source_a, source_b = source_range
            duration = end_time - start_time
            width = max(1, source_b - source_a)
            allocations = {
                (a, b): (
                    start_time + duration * (a - source_a) / width,
                    start_time + duration * (min(b, source_b) - source_a) / width,
                )
                for a, b in ranges
            }

            source_allocations[source_id] = allocations

        new = []
        for a, b in zip(boundaries, boundaries[1:]):
            if a >= b:
                continue

            source = source_for(a)
            ruby, ruby_source = exact_ruby.get((a, b), (None, None))

            # Interpolate new boundaries within the original timed span.
            # Existing exact chunks retain their authored starts and ends.
            allocation = source_allocations.get(id(source), {}).get((a, b))
            child_start = (
                allocation[0]
                if allocation is not None
                else float(source.start)
            )

            source_range = next(
                ((x, y) for x, y, segment in old_ranges if segment is source),
                (a, b),
            )
            source_a, source_b = source_range

            # Keep an existing instant-fill marker only for an unchanged,
            # exact karaoke chunk. A newly split chunk is a normal timed span.
            exact_chunk = a == source_a and b == source_b

            # A split child gets its allocated end, rather than inheriting the
            # entire parent's end and overlapping every later child.
            child_end = (
                getattr(source, "end", None)
                if exact_chunk
                else (allocation[1] if allocation is not None else None)
            )

            tail_source = source_for(b - 1)
            if tail_source is not source:
                tail_a, tail_b, _ = next(
                    entry for entry in old_ranges if entry[2] is tail_source
                )
                tail_index = next(i for i, segment in enumerate(old) if segment is tail_source)
                tail_end = segment_end(line, tail_index, next_line_start)
                if tail_end is not None and b < tail_b:
                    child_end = tail_source.start + (tail_end - tail_source.start) * (b - tail_a) / max(1, tail_b - tail_a)
                else:
                    child_end = tail_end

            child = LyricSegment(
                child_start,
                line.text[a:b],
                ruby,
                source.instant if exact_chunk else False,
                child_end,
                ruby_source,
            )

            child.visual_line = line.text[:a].count("\n")

            # Rebase every existing inline ruby span that overlaps this new
            # karaoke chunk. Splitting or re-separating karaoke must preserve
            # readings; only the timing boundaries are changing.
            for old_a, old_b, old_segment in old_ranges:
                if old_b <= a or old_a >= b:
                    continue
                old_spans = list(getattr(old_segment, "ruby_spans", []) or [])
                if not old_spans:
                    old_ruby = getattr(old_segment, "ruby", None)
                    if old_ruby:
                        old_spans = [(0, old_b - old_a, old_ruby)]
                for span_a, span_b, span_reading in old_spans:
                    global_a = old_a + span_a
                    global_b = old_a + span_b
                    overlap_a = max(a, global_a)
                    overlap_b = min(b, global_b)
                    if overlap_a >= overlap_b:
                        continue
                    child_reading = span_reading
                    if (overlap_a != global_a or overlap_b != global_b) and split_reading_by_text_ranges is not None:
                        child_reading = split_reading_by_text_ranges(
                            line.text[global_a:global_b],
                            span_reading,
                            [(overlap_a - global_a, overlap_b - global_a)],
                        )[0]
                    if child_reading:
                        child.ruby_spans.append((overlap_a - a, overlap_b - a, child_reading))

            if len(child.ruby_spans) == 1 and child.ruby_spans[0][:2] == (0, len(child.text)):
                child.ruby = child.ruby_spans[0][2]
                child.ruby_source = "manual"
            new.append(child)

        # Do not derive line.start from the first karaoke chunk.
        # The line may intentionally begin before segment 0.
        line.segments = new
        line.has_karaoke = True

        self.karaoke_dirty = True
        self._mark_document_dirty()
        self.loading = True
        self.segment_table.setRowCount(0)
        self._populate_segments(line)
        self.loading = False
        self.karaoke_hint.setText(
            "Separation applied. Ruby tokens do not create karaoke chunks."
        )
        self._refresh_ruby_preview(line)
        self.refresh_line_label(index)

        # _populate_segments() selects the first visible segment while loading,
        # but that selection signal is intentionally blocked. Refresh the
        # waveform explicitly so the first segment's selected marker/handles
        # appear immediately after a fresh separation.
        self._refresh_karaoke_waveform_editor()


    def _default_segment_start(self, row, line):
        if row > 0 and self.segment_table.item(row - 1, 1) is not None:
            try:
                return self._parse_time(self.segment_table.item(row - 1, 1).text())
            except Exception:
                pass
        if 0 <= row < len(line.segments):
            return float(line.segments[row].start)
        return float(line.start)

    def _default_segment_end(self, row, line, start_time):
        if row + 1 < self.segment_table.rowCount():
            next_item = self.segment_table.item(row + 1, 0)
            if next_item is not None and next_item.text().strip():
                try:
                    return max(start_time, self._parse_time(next_item.text()))
                except Exception:
                    pass
        if getattr(line, "end", None) is not None:
            return max(start_time, float(line.end))
        line_index = self._current_line_index()
        if line_index is not None and 0 <= line_index < len(self.lines) - 1:
            return max(start_time, float(self.lines[line_index + 1].start))
        if 0 <= row < len(line.segments):
            existing = getattr(line.segments[row], "end", None)
            if existing is not None:
                return max(start_time, float(existing))
        return start_time

    def _set_segment_timing(self, row, line, *, start_time=None, end_time=None,
                            change_start=False, change_end=False):
        """Update canonical karaoke timing without reading table fallbacks."""
        if line is None or not (0 <= row < len(line.segments)):
            return None

        old = line.segments[row]
        new_start = float(start_time) if change_start else float(old.start)
        new_end = end_time if change_end else getattr(old, "end", None)
        if new_end is not None:
            new_end = float(new_end)
            if new_end < new_start:
                # A manual start edit can move a chunk past an old explicit
                # end boundary. That end is now stale; clamping it to the new
                # start would still serialize an extra/phantom <timestamp>.
                # Drop the invalid explicit end and let the next chunk (or
                # line end) provide the natural boundary instead.
                new_end = None

        replacement = LyricSegment(
            new_start,
            old.text,
            old.ruby,
            old.instant,
            new_end,
            getattr(old, "ruby_source", None),
        )
        # Timing-only edits must preserve inline ruby spans.
        replacement.ruby_spans = list(getattr(old, "ruby_spans", []) or [])
        replacement.visual_line = old.visual_line
        line.segments[row] = replacement

        # Segment 0 is independent from line.start. Moving its left edge can
        # create or adjust a leading pause without moving the lyric timestamp.

        if row == len(line.segments) - 1 and change_end:
            if line.end is None or line.end == old.end or (new_end is not None and new_end > line.end):
                line.end = new_end

        return line.segments[row]

    def instant_changed(self, row, checked):
        if self.loading:
            return
        item = self.segment_table.item(row, 2)
        if item is not None:
            self.segment_changed(item)

    def segment_changed(self, item):
        if self.loading:
            return

        index, line = self._current_line()
        row = item.row()
        if line is None or not (0 <= row < len(line.segments)):
            return

        if item.column() in (0, 1) and item.text().strip():
            try:
                value = self._parse_time(item.text())
                if item.column() == 1 and value < line.segments[row].start:
                    raise ValueError("The segment end cannot be before its start.")
            except ValueError as error:
                old_value = line.segments[row].start if item.column() == 0 else line.segments[row].end
                blocker = QSignalBlocker(self.segment_table)
                item.setText(format_timestamp(old_value) if old_value is not None else "")
                del blocker
                QMessageBox.warning(self, "Invalid Timestamp", str(error))
                return
        self._push_undo_state()
        # Timing edits and text/ruby edits both preserve the canonical segment
        # object. Only the column that was actually edited may change timing.
        old = line.segments[row]
        start_time = float(old.start)
        end_time = getattr(old, "end", None)

        if item.column() == 0:
            text = item.text().strip()
            try:
                start_time = self._parse_time(text) if text else self._default_segment_start(row, line)
            except Exception:
                start_time = self._default_segment_start(row, line)
            self._set_segment_timing(
                row, line, start_time=start_time, change_start=True
            )

        elif item.column() == 1:
            text = item.text().strip()
            try:
                end_time = self._parse_time(text) if text else None
            except Exception:
                end_time = getattr(line.segments[row], "end", None)
            self._set_segment_timing(
                row, line, end_time=end_time, change_end=True
            )

        else:
            text_item = self.segment_table.item(row, 2)
            ruby_item = self.segment_table.item(row, 3)
            text_value = text_item.text() if text_item is not None else old.text
            ruby = ruby_item.text().strip() if ruby_item is not None else ""
            ruby = ruby or None
            instant_box = self.segment_table.cellWidget(row, 4)
            instant = instant_box.isChecked() if instant_box else old.instant
            replacement = LyricSegment(
                old.start,
                text_value,
                ruby,
                instant,
                getattr(old, "end", None),
                getattr(old, "ruby_source", None),
            )
            old_spans = list(getattr(old, "ruby_spans", []) or [])
            replacement.visual_line = old.visual_line
            if item.column() == 3:
                replacement.ruby_source = "manual" if ruby else None
            if item.column() == 3 and len(old_spans) == 1:
                # The Karaoke Ruby column edits the existing inline span without
                # changing its text range (e.g. 描き -> {描|が}き).
                a, b, _ = old_spans[0]
                if ruby:
                    replacement.ruby_spans = [(a, b, ruby)]
                    if not (a == 0 and b == len(text_value)):
                        replacement.ruby = None
                else:
                    replacement.ruby_spans = []
            elif item.column() != 3:
                # Timing/text/instant edits must not silently erase ruby spans.
                replacement.ruby_spans = old_spans
            line.segments[row] = replacement

        line.text = "".join(segment.text for segment in line.segments)
        line.has_karaoke = (
            line.has_karaoke or item.column() in (0, 1) or len(line.segments) > 1
            or any(segment.instant for segment in line.segments)
        )
        self.karaoke_dirty = True
        self._mark_document_dirty()
        self.refresh_line_label(index)
        self._refresh_ruby_preview(line)
        self._update_furigana_editor_availability(line)
        self._update_overlap_warning(line)
        self._refresh_karaoke_preview()
        self._refresh_karaoke_waveform_editor()

    def _refresh_karaoke_preview(self, position_ms=None, rebuild_segments=True):
        if not self.karaoke_enabled or not hasattr(self, "karaoke_renderer_preview"):
            return

        index, line = self._current_line()
        if line is None:
            if rebuild_segments or self._preview_segments_line_index is not None:
                self.karaoke_renderer_preview.set_lyric("", generate=False)
                self._preview_segments_line_index = None
            return

        if rebuild_segments or self._preview_segments_line_index != index:
            line_end = getattr(line, "end", None)
            if line_end is None:
                timeline = sorted(
                    (candidate.start, candidate_index)
                    for candidate_index, candidate in enumerate(self.lines)
                )
                timeline_index = next(
                    (i for i, (_start, candidate_index) in enumerate(timeline)
                     if candidate_index == index),
                    None,
                )
                if timeline_index is not None and timeline_index + 1 < len(timeline):
                    line_end = timeline[timeline_index + 1][0]
                elif self.player is not None and hasattr(self.player, "media_player"):
                    duration = self.player.media_player.duration()
                    if duration > 0:
                        line_end = duration / 1000.0
                if line_end is None:
                    line_end = line.segments[-1].start if line.segments else line.start
            self.karaoke_renderer_preview.set_segments(
                line.text,
                line.segments,
                line_end,
            )
            self._preview_segments_line_index = index

        if position_ms is None and self.player is not None and hasattr(self.player, "media_player"):
            position_ms = self.player.media_player.position()
        if position_ms is None:
            position_ms = int(line.start * 1000)

        # This is the only per-position operation required for the preview.
        self.karaoke_renderer_preview.set_karaoke_position(
            position_ms / 1000.0
        )

    def preview_position_changed(self, position_ms):
        if self._segment_preview_range is not None:
            start_ms, end_ms = self._segment_preview_range
            if position_ms >= end_ms:
                self._segment_preview_range = None
                media = getattr(self.player, "media_player", None)
                if media is not None:
                    media.pause()
            elif position_ms < start_ms - 100:
                self._segment_preview_range = None
        # Re-anchor the smooth display clock to the authoritative media position.
        try:
            self._display_position_anchor_ms = float(position_ms)
        except (TypeError, ValueError):
            return
        self._display_position_anchor_monotonic = time.monotonic()

        self._update_playback_line_style(position_ms)
        # Do not rebuild/set_segments on every QMediaPlayer positionChanged tick.
        # The selected line changes far less often than the playback position;
        # the sweep itself only needs the new position.
        self._refresh_karaoke_preview(
            position_ms,
            rebuild_segments=False,
        )

        # Update immediately when the backend reports a new position. The
        # dedicated 30 Hz timer fills in the smooth intermediate timestamps.
        self._set_precise_time_display(position_ms)

        seeks = [self.lyrics_seek]
        if self.karaoke_enabled:
            seeks.insert(0, self.karaoke_seek)
        for seek in seeks:
            if not seek.isSliderDown():
                blocker = QSignalBlocker(seek)
                seek.setValue(int(position_ms))
                del blocker
        for waveform_name in ("lyrics_waveform", "karaoke_waveform"):
            waveform = getattr(self, waveform_name, None)
            if waveform is not None:
                waveform.set_position(position_ms)

    def _toggle_lyrics_transport(self, checked):
        """Switch the Lyrics tab timeline between seekbar and waveform."""
        if not hasattr(self, "lyrics_transport_stack"):
            return
        self.lyrics_transport_stack.setCurrentIndex(1 if checked else 0)
        self.lyrics_transport_toggle.setText("Seekbar" if checked else "Waveform")
        if hasattr(self, "commit_line_timestamp_button"):
            self.commit_line_timestamp_button.setVisible(bool(checked))

        if checked:
            # Waveform needs its full editor height.
            self.lyrics_transport_stack.setFixedHeight(
                max(74, self.lyrics_waveform.sizeHint().height())
            )
            self._refresh_lyrics_waveform()
        else:
            # Seekbar should collapse back to its compact height instead of
            # inheriting the waveform page's size hint.
            self.lyrics_transport_stack.setFixedHeight(
                self.lyrics_seek.sizeHint().height()
            )

    def _toggle_karaoke_transport(self, checked):
        """Switch the Karaoke tab timeline between seekbar and waveform."""
        if not hasattr(self, "karaoke_transport_stack"):
            return
        self.karaoke_transport_stack.setCurrentIndex(1 if checked else 0)
        self.karaoke_transport_toggle.setText("Seekbar" if checked else "Waveform")

        if checked:
            self.karaoke_transport_stack.setFixedHeight(
                max(74, self.karaoke_waveform.sizeHint().height())
            )
            self._refresh_karaoke_waveform()
        else:
            self.karaoke_transport_stack.setFixedHeight(
                self.karaoke_seek.sizeHint().height()
            )

    def _refresh_karaoke_waveform(self):
        waveform = getattr(self, "karaoke_waveform", None)
        if waveform is None:
            return
        path = self._current_song_path()
        if path is None and self.attached_song_path:
            path = Path(self.attached_song_path)
        if path is not None and path.exists():
            waveform.load_audio(path)
        if self.player is not None and hasattr(self.player, "media_player"):
            media = self.player.media_player
            waveform.set_duration(media.duration())
            waveform.set_position(media.position())
        self._refresh_karaoke_waveform_editor()

    def _refresh_lyrics_waveform_markers(self):
        if not hasattr(self, "lyrics_waveform"):
            return
        # Regular LRC and karaoke/LRCX use different marker systems.
        # Never leave a stale karaoke A/B pair visible after switching to a
        # normal-LRC line; otherwise an old B marker can appear *before* the
        # selected line's A marker and make the timeline look invalid.
        if not self.karaoke_enabled:
            self.lyrics_waveform.set_karaoke_timing(None, None, -1)
        self.lyrics_waveform.set_lyric_times(
            [getattr(line, "start", 0.0) for line in self.lines]
        )
        self._refresh_selected_lyric_waveform_boundary()

    def _refresh_selected_lyric_waveform_boundary(self):
        if not hasattr(self, "lyrics_waveform"):
            return
        index = self._current_line_index()
        if index is None or not (0 <= index < len(self.lines)):
            self.lyrics_waveform.clear_selected_lyric_timing()
            self._waveform_lyric_preview_handle = None
            self._waveform_lyric_preview_ms = None
            return

        line = self.lines[index]
        selected_start = float(getattr(line, "start", 0.0))
        start_ms = int(round(selected_start * 1000))

        # Use the actual yellow-marker timestamps shown on the waveform.
        # Identical timestamps collapse to one visual marker.
        later_markers = sorted({
            int(round(float(getattr(other, "start", 0.0)) * 1000))
            for other in self.lines
            if float(getattr(other, "start", 0.0)) > selected_start
        })

        # B starts at the first later lyric timestamp.
        end_ms = later_markers[0] if later_markers else None

        # Diagnostic rule: B starts at index + 1, while the Timing limit is
        # the lyric at index + 2. Python indices are zero-based, so this is the
        # second lyric after the selected one.
        hard_index = index + 2
        hard_boundary_ms = None
        if hard_index < len(self.lines):
            hard_boundary_ms = int(round(
                float(getattr(self.lines[hard_index], "start", line.start)) * 1000
            ))

        if end_ms is None and self.lyrics_waveform._duration_ms > 0:
            end_ms = min(
                self.lyrics_waveform._duration_ms,
                start_ms + 5000,
            )

        self.lyrics_waveform.set_lyric_hard_boundary(hard_boundary_ms)
        self.lyrics_waveform.set_selected_lyric_timing(start_ms, end_ms)
        self.lyrics_waveform._focus_lyrics_interval()

    def _waveform_lyric_timing_changed(self, handle, value_ms):
        if self.loading:
            return
        index, line = self._current_line()
        if line is None:
            return
        value_ms = max(0, int(value_ms))
        start_ms = int(round(float(line.start) * 1000))
        selected_start = float(getattr(line, "start", 0.0))
        later_markers = sorted({
            float(getattr(other, "start", 0.0))
            for other in self.lines
            if float(getattr(other, "start", 0.0)) > selected_start
        })
        hard_index = index + 2
        hard_boundary_ms = None
        if hard_index < len(self.lines):
            hard_boundary_ms = int(round(
                float(getattr(self.lines[hard_index], "start", line.start)) * 1000
            ))

        if handle == "start":
            if hard_boundary_ms is not None:
                value_ms = min(value_ms, max(start_ms, hard_boundary_ms - 1))
            value_ms = max(0, value_ms)
            self._waveform_lyric_preview_handle = "start"
        else:
            if hard_boundary_ms is not None:
                value_ms = min(value_ms, hard_boundary_ms)
            value_ms = max(start_ms, value_ms)
            self._waveform_lyric_preview_handle = "end"
        self._waveform_lyric_preview_ms = value_ms
        # Show the provisional A value in the timestamp field; B remains only
        # on the waveform until Commit because it belongs to the next line.
        if handle == "start":
            blocker = QSignalBlocker(self.line_time)
            self.line_time.setValue(value_ms / 1000.0)
            del blocker
        self.lyrics_waveform.set_selected_lyric_timing(start_ms if handle == "end" else value_ms, value_ms if handle == "end" else (
            int(round(float(self.lines[index + 1].start) * 1000)) if index + 1 < len(self.lines) else min(self.lyrics_waveform._duration_ms, start_ms + 5000)
        ))
        self.lyrics_waveform.update()

    def _refresh_lyrics_waveform(self):
        if not hasattr(self, "lyrics_waveform"):
            return

        path = self._current_song_path()
        if path is None and self.attached_song_path:
            path = Path(self.attached_song_path)
        if path is not None and path.exists():
            self.lyrics_waveform.load_audio(path)

        if self.player is not None and hasattr(self.player, "media_player"):
            media = self.player.media_player
            self.lyrics_waveform.set_duration(media.duration())
            self.lyrics_waveform.set_position(media.position())
            if hasattr(self, "karaoke_waveform"):
                self.karaoke_waveform.set_duration(media.duration())
                self.karaoke_waveform.set_position(media.position())
        self._refresh_lyrics_waveform_markers()

    def _refresh_karaoke_waveform_editor(self):
        waveform = getattr(self, "karaoke_waveform", None)
        if waveform is None or not self.karaoke_enabled:
            return
        row = self.segment_table.currentRow() if hasattr(self, "segment_table") else -1
        index, line = self._current_line()
        if line is None or not getattr(line, "has_karaoke", False) or not getattr(line, "segments", None):
            waveform.set_karaoke_timing(None, None, -1)
            waveform.clear_selected_lyric_timing()
            waveform.clear_karaoke_segments()
            return

        # Karaoke owns segment-level timing. The redundant line-level A/B
        # editor belongs in Lyrics; here we show only the actual segments.
        # Karaoke uses the segment selection state directly. Do not reset the
        # selected segment to -1 before calling set_karaoke_segments(): when the
        # segment data itself is unchanged, set_karaoke_segments() may correctly
        # skip a redraw, which would otherwise leave the markers visually
        # deselected until the row is clicked again.
        waveform.clear_selected_lyric_timing()

        line_start_ms = int(round(float(line.start) * 1000))

        # The karaoke line's real right boundary is the NEXT LYRIC timestamp,
        # not the last karaoke segment's current end. That is the reference
        # boundary the editor needs to show and enforce.
        next_line_start = next_lyric_start(self.lines, line)

        if next_line_start is not None:
            line_end_ms = int(round(float(next_line_start) * 1000))
        elif waveform._duration_ms > 0:
            # Last lyric: audio end is the only natural hard boundary.
            line_end_ms = int(waveform._duration_ms)
        else:
            last = line.segments[-1]
            fallback_end = getattr(last, "end", None)
            if fallback_end is None:
                fallback_end = getattr(last, "start", line.start)
            line_end_ms = int(round(float(fallback_end) * 1000))

        line_end_ms = max(line_start_ms + 1, line_end_ms)

        ranges = []
        for i, segment in enumerate(line.segments):
            seg_end = segment_end(line, i, line_end_ms / 1000.0)
            label = getattr(segment, "text", None)
            if label is None:
                label = getattr(segment, "lyric", None)
            if label is None:
                label = getattr(segment, "value", "")
            ranges.append((segment.start, seg_end, label))

        waveform.set_karaoke_segments(
            ranges, row, line_start_ms, line_end_ms, line_identity=index
        )

    def _waveform_karaoke_timing_finished(self):
        # Reconcile the canonical model/table with the waveform once, after
        # the mouse is released. Rebuilding on every mouseMoveEvent causes
        # the active handles to flicker between their drag and idle states.
        self._waveform_drag_row = -1
        self._refresh_karaoke_waveform_editor()
        self._refresh_karaoke_preview(rebuild_segments=True)

    def _waveform_karaoke_line_boundary_changed(self, handle, value_ms):
        """Proportionally stretch/shrink all karaoke timings in the line.

        This mirrors Aegisub-style line retiming: moving A/B preserves every
        internal segment's relative position inside the line interval.
        """
        if not self.karaoke_enabled or self.loading:
            return
        index, line = self._current_line()
        if line is None or not getattr(line, "segments", None):
            return
        old_start = float(line.start)
        old_end = getattr(line, "end", None)
        if old_end is None:
            last = line.segments[-1]
            old_end = getattr(last, "end", None) or last.start
        old_end = float(old_end)
        if old_end <= old_start:
            return
        new_start = float(value_ms) / 1000.0 if handle == "start" else old_start
        new_end = float(value_ms) / 1000.0 if handle == "end" else old_end
        if new_end <= new_start:
            return

        # Scale every segment boundary by its normalized position.
        self._push_undo_state()
        span = old_end - old_start
        new_span = new_end - new_start
        for segment in line.segments:
            rel_start = (float(segment.start) - old_start) / span
            segment.start = new_start + rel_start * new_span
            if getattr(segment, "end", None) is not None:
                rel_end = (float(segment.end) - old_start) / span
                segment.end = new_start + rel_end * new_span
        line.start = new_start
        line.end = new_end
        self.loading = True
        self.line_time.setValue(new_start)
        self.loading = False
        self.karaoke_dirty = self.document_dirty = True
        self.refresh_line_label(index)
        self._select_line_index(index)
        self.load_selected_line(self.line_list.currentRow())
        self._refresh_karaoke_preview()
        self._refresh_karaoke_waveform_editor()

    def _waveform_karaoke_timing_changed(self, handle, value_ms):
        if not self.karaoke_enabled or self.loading:
            return
        row = self.segment_table.currentRow()
        index, line = self._current_line()
        if line is None or not 0 <= row < len(line.segments):
            return
        if handle not in {"start", "end", "linked_start", "linked_end"}:
            return
        old = line.segments[row]
        hard_end = next_lyric_start(self.lines, line)
        duration_ms = getattr(self.karaoke_waveform, "_duration_ms", 0)
        if duration_ms > 0:
            audio_end = duration_ms / 1000.0
            hard_end = min(hard_end, audio_end) if hard_end is not None else audio_end
        self._karaoke_bounds_warning = None
        updates = []
        if handle in {"start", "linked_start"}:
            lower = max(0.0, float(line.start))
            upper = segment_end(line, row, hard_end)
            upper = None if upper is None else float(upper) - 0.001
            if handle == "linked_start":
                if row == 0:
                    return
                previous = line.segments[row - 1]
                previous_end = segment_end(line, row - 1, hard_end)
                if previous_end is None or abs(previous_end - old.start) > 0.001:
                    return
                lower = max(lower, float(previous.start) + 0.001)
            if upper is not None and upper < lower:
                self._karaoke_bounds_warning = "No valid Start boundary: adjust the segment End first."
                self._update_overlap_warning(line)
                return
            value = max(lower, float(value_ms) / 1000.0)
            if upper is not None:
                value = min(value, upper)
            if handle == "linked_start":
                updates.append((row - 1, 1, value))
            updates.append((row, 0, value))
        else:
            lower = max(0.0, float(old.start) + 0.001)
            upper = hard_end
            if handle == "linked_end":
                if row + 1 >= len(line.segments):
                    return
                following = line.segments[row + 1]
                old_end = segment_end(line, row, hard_end)
                if old_end is None or abs(old_end - following.start) > 0.001:
                    return
                following_end = segment_end(line, row + 1, hard_end)
                if following_end is not None:
                    following_limit = float(following_end) - 0.001
                    upper = min(upper, following_limit) if upper is not None else following_limit
            if upper is not None and upper < lower:
                self._karaoke_bounds_warning = "No valid End boundary: adjust the segment Start first."
                self._update_overlap_warning(line)
                return
            value = max(lower, float(value_ms) / 1000.0)
            if upper is not None:
                value = min(value, upper)
            updates.append((row, 1, value))
            if handle == "linked_end":
                updates.append((row + 1, 0, value))
        if abs(value - float(value_ms) / 1000.0) > 0.0005:
            self._karaoke_bounds_warning = "Timing was limited to the valid segment and lyric boundaries."
        if all((line.segments[r].start if c == 0 else line.segments[r].end) == v for r, c, v in updates):
            self._update_overlap_warning(line)
            return
        drag_key = (index, row, handle)
        if self._waveform_drag_row != row or getattr(self, "_waveform_drag_key", None) != drag_key:
            self._push_undo_state()
            self._waveform_drag_row = row
            self._waveform_drag_key = drag_key
        for update_row, column, value in updates:
            if column == 0:
                self._set_segment_timing(update_row, line, start_time=value, change_start=True)
            else:
                self._set_segment_timing(update_row, line, end_time=value, change_end=True)
        line.has_karaoke = True
        self._update_karaoke_timing_cells(updates)
        self.karaoke_dirty = True
        self._mark_document_dirty()
        self.refresh_line_label(index)
        self._update_overlap_warning(line)

    def _set_precise_time_display(self, position_ms):
        text = format_timestamp(float(position_ms) / 1000.0)
        if self.karaoke_enabled:
            self.preview_position_label.setText(text)
        self.lyrics_position_label.setText(text)
        if self.karaoke_enabled and not self.current_time.hasFocus():
            blocker = QSignalBlocker(self.current_time)
            self.current_time.setValue(float(position_ms) / 1000.0)
            del blocker

    def _update_precise_display_playback_state(self, state):
        self._display_position_playing = (
            state == QMediaPlayer.PlaybackState.PlayingState
            if self.player is not None and hasattr(self.player, "media_player")
            else False
        )

        if self._display_position_playing:
            if self.player is not None and hasattr(self.player, "media_player"):
                position_ms = self.player.media_player.position()
                self._display_position_anchor_ms = float(position_ms)
                self._display_position_anchor_monotonic = time.monotonic()
            self._precise_display_timer.start()
        else:
            self._precise_display_timer.stop()

    def _update_precise_playback_display(self):
        if (
            not self._display_position_playing
            or self._display_position_anchor_monotonic is None
        ):
            return

        elapsed_ms = (
            time.monotonic() - self._display_position_anchor_monotonic
        ) * 1000.0
        estimated_position_ms = max(
            0.0,
            self._display_position_anchor_ms + elapsed_ms,
        )
        self._set_precise_time_display(estimated_position_ms)

    def preview_duration_changed(self, duration_ms):
        seeks = [self.lyrics_seek]
        if self.karaoke_enabled:
            seeks.insert(0, self.karaoke_seek)
        for seek in seeks:
            seek.setRange(0, max(0, int(duration_ms)))
        for waveform_name in ("lyrics_waveform", "karaoke_waveform"):
            waveform = getattr(self, waveform_name, None)
            if waveform is not None:
                waveform.set_duration(duration_ms)
        self._refresh_lyrics_waveform_markers()
        self._refresh_karaoke_waveform_editor()
        text = format_timestamp(duration_ms / 1000.0)
        if self.karaoke_enabled:
            self.preview_duration_label.setText(text)
        self.lyrics_duration_label.setText(text)

    def preview_playback_state_changed(self, state):
        if self.player is None:
            return
        media = self.player.media_player
        playing = media.playbackState() == media.PlaybackState.PlayingState
        self.preview_wants_playing = playing
        text = "Pause" if playing else "Play"
        if self.karaoke_enabled:
            self.preview_play_button.setText(text)
        self.lyrics_play_button.setText(text)

    def toggle_preview(self):
        self._segment_preview_range = None
        if self.player is None or not hasattr(self.player, "media_player"):
            return
        media = self.player.media_player
        self.preview_wants_playing = not self.preview_wants_playing
        # Update immediately; do not wait for asynchronous backend state.
        text = "Pause" if self.preview_wants_playing else "Play"
        if self.karaoke_enabled:
            self.preview_play_button.setText(text)
        self.lyrics_play_button.setText(text)
        if self.preview_wants_playing:
            media.play()
        else:
            media.pause()

    def _focus_is_text_input(self):
        """Do not hijack normal typing while an editor widget has focus."""
        widget = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QDoubleSpinBox, QComboBox)):
                return True
            widget = widget.parentWidget()
        return False

    def _seek_preview_relative(self, delta_ms):
        if self.player is None or not hasattr(self.player, "media_player"):
            return
        player = self.player.media_player
        current = int(player.position())
        duration = int(player.duration()) if hasattr(player, "duration") else 0
        target = max(0, current + int(delta_ms))
        if duration > 0:
            target = min(target, duration)
        self.seek_preview(target)

    def show_karaoke_shortcuts(self):
        QMessageBox.information(
            self,
            "Karaoke Shortcuts",
            "Q    Seek −100 ms\n"
            "E    Seek +100 ms\n\n"
            "A    Seek −1 second\n"
            "D    Seek +1 second\n\n"
            "F    Mark Start / End\n\n"
            "Shortcuts are disabled while typing in lyric or timestamp fields.",
        )

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.KeyPress or event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return super().eventFilter(watched, event)
        key = event.key()
        line_list = getattr(self, "line_list", None)
        if line_list is not None and watched in (line_list, line_list.viewport()) and key == Qt.Key.Key_Delete:
            self.delete_line()
            event.accept()
            return True
        if self._karaoke_shortcut_active():
            table = getattr(self, "segment_table", None)
            inside_table = table is not None and (
                watched is table or (isinstance(watched, QWidget) and table.isAncestorOf(watched))
            )
            timestamp_target = inside_table and table.currentColumn() in (0, 1)
            if watched is getattr(self, "current_time", None):
                timestamp_target = True
            if timestamp_target and key in {Qt.Key.Key_Q, Qt.Key.Key_E, Qt.Key.Key_A, Qt.Key.Key_D, Qt.Key.Key_F}:
                self._handle_karaoke_shortcut(key)
                event.accept()
                return True
            if key == Qt.Key.Key_Space and inside_table and (
                timestamp_target or watched in (table, table.viewport())
            ):
                self.toggle_preview()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _karaoke_shortcut_active(self):
        return (
            self.karaoke_enabled
            and hasattr(self, "tabs")
            and self.tabs.currentIndex() >= 0
            and self.tabs.tabText(self.tabs.currentIndex()) == "Karaoke"
        )

    def _handle_karaoke_shortcut(self, key):
        if key == Qt.Key.Key_Q:
            self._seek_preview_relative(-100)
        elif key == Qt.Key.Key_E:
            self._seek_preview_relative(100)
        elif key == Qt.Key.Key_A:
            self._seek_preview_relative(-1000)
        elif key == Qt.Key.Key_D:
            self._seek_preview_relative(1000)
        elif key == Qt.Key.Key_F:
            self.use_playback_time()

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key.Key_Space
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and not self._focus_is_text_input()
        ):
            self.toggle_preview()
            event.accept()
            return
        # Delete removes the selected lyric/timestamp when focus is anywhere
        # inside the shared lyric list. Keep Delete untouched in text editors.
        if (
            event.key() == Qt.Key.Key_Delete
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            focus = QApplication.focusWidget()
            line_list = getattr(self, "line_list", None)
            if line_list is not None:
                widget = focus
                while widget is not None:
                    if widget is line_list or widget is line_list.viewport():
                        self.delete_line()
                        event.accept()
                        return
                    widget = widget.parentWidget() if hasattr(widget, "parentWidget") else None

        # Karaoke shortcuts are scoped strictly to the Karaoke tab.  The dialog
        # still receives key events from child widgets, so without this guard
        # Q/E/A/D/F could edit karaoke timing while the user was elsewhere.
        karaoke_tab_active = self._karaoke_shortcut_active()

        # Keep the shortcuts out of text editors so typing lyrics/timestamps
        # behaves completely normally.
        if (
            karaoke_tab_active
            and not self._focus_is_text_input()
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            key = event.key()
            if key == Qt.Key.Key_Q:
                self._handle_karaoke_shortcut(key)
                event.accept()
                return
            if key == Qt.Key.Key_E:
                self._handle_karaoke_shortcut(key)
                event.accept()
                return
            if key == Qt.Key.Key_A:
                self._handle_karaoke_shortcut(key)
                event.accept()
                return
            if key == Qt.Key.Key_D:
                self._handle_karaoke_shortcut(key)
                event.accept()
                return
            if key == Qt.Key.Key_F:
                self._handle_karaoke_shortcut(key)
                event.accept()
                return

        super().keyPressEvent(event)

    def play_waveform_segment(self, start_ms, end_ms):
        """Seek to a clicked waveform segment and start playback."""
        if self.player is None or not hasattr(self.player, "media_player"):
            return
        if end_ms <= start_ms:
            return
        media_player = self.player.media_player
        self._segment_preview_range = None
        media_player.setPosition(int(start_ms))
        self._segment_preview_range = (int(start_ms), int(end_ms))
        play = getattr(media_player, "play", None)
        if callable(play):
            play()

    def seek_preview(self, position_ms):
        self._segment_preview_range = None
        if self.player is not None and hasattr(self.player, "media_player"):
            self.player.media_player.setPosition(int(position_ms))

    def skip_preview(self, seconds):
        if self.player is None or not hasattr(self.player, "media_player"):
            return
        media = self.player.media_player
        target = media.position() + int(seconds * 1000)
        self.seek_preview(max(0, min(target, media.duration())))

    def use_playback_time(self):
        if not self.karaoke_enabled:
            return
        if self.player is None or not hasattr(self.player, "media_player"):
            return

        position = self.player.media_player.position() / 1000.0
        self.current_time.setValue(position)

        row = self.segment_table.currentRow()
        column = self.segment_table.currentColumn()
        if row < 0 and self.segment_table.rowCount() > 0:
            row, column = 0, 0
            self.segment_table.setCurrentCell(row, column)
        if row < 0:
            return
        if column not in (0, 1):
            column = 0

        index, line = self._current_line()
        if line is None or not (0 <= row < len(line.segments)):
            return

        self._push_undo_state()
        if column == 0:
            updated = self._set_segment_timing(
                row, line, start_time=position, change_start=True
            )
            self.loading = True
            item = self.segment_table.item(row, 0)
            if item is not None:
                item.setText(format_timestamp(updated.start))
            self.loading = False

            # Start -> End on the SAME row.
            self.segment_table.setCurrentCell(row, 1)

        else:
            updated = self._set_segment_timing(
                row, line, end_time=position, change_end=True
            )
            self.loading = True
            item = self.segment_table.item(row, 1)
            if item is not None:
                # Fresh/open-ended segments can legitimately have no end timestamp.
                item.setText(
                    format_timestamp(updated.end)
                    if updated.end is not None
                    else ""
                )
            self.loading = False

            # End -> Start on the NEXT row.
            next_row = row + 1
            # Whitespace-only segments are intentionally hidden in the editor,
            # so don't advance keyboard/marking workflow onto an invisible row.
            while (
                next_row < self.segment_table.rowCount()
                and self.segment_table.isRowHidden(next_row)
            ):
                next_row += 1
            if next_row < self.segment_table.rowCount():
                self.segment_table.setCurrentCell(next_row, 0)
                self.segment_table.scrollToItem(
                    self.segment_table.item(next_row, 0),
                    QTableWidget.ScrollHint.PositionAtCenter,
                )

        line.text = "".join(segment.text for segment in line.segments)
        line.has_karaoke = True
        self.karaoke_dirty = True
        self._mark_document_dirty()
        self.refresh_line_label(index)
        self._update_overlap_warning(line)
        self._refresh_karaoke_preview()

    def refresh_line_label(self, index):
        if 0 <= index < len(self.lines):
            line = self.lines[index]
            try:
                view_row = self._view_line_indices.index(index)
            except ValueError:
                return
            item = self.line_list.item(view_row)
            if item:
                item.setText(f"{format_timestamp(line.start)}  {line.text}")

    def _serialized_for_current_format(self):
        mode = self._current_generation_mode()
        suffix = self.lrc_path.suffix.lower()
        if suffix != ".lrcx" and self._lrcx_only_features():
            suffix = ".lrcx"
        if suffix == ".txt" and self.document_dirty:
            suffix = ".lrcx"
        self._source_suffix = suffix if suffix in {".lrc", ".lrcx", ".txt"} else ".lrcx"
        if self._source_suffix == ".txt":
            return "\n".join(line.text for line in self.lines) + ("\n" if self.lines else "")
        if self._source_suffix == ".lrcx":
            return serialize_lrcx(self.lines, offset_ms=self.lyrics_offset_ms, generation_mode=mode)
        return serialize_lrc(self.lines, generation_mode=mode)

    @staticmethod
    def _share_text_for_lines(lines, include_timestamps=False):
        output = []
        for line in lines:
            text = str(line.text)
            if include_timestamps:
                text = f"[{format_timestamp(line.start)}]{text}"
            output.append(text)
        return "\n".join(output) + ("\n" if output else "")

    def _share_text(self):
        if self._source_dirty:
            self.reload_model_from_source()
            if self._source_dirty:
                return None
        include_timestamps = self.share_format_combo.currentData() == "lrc"
        lines = self._lines_for_share(
            self.lines,
            self._checked_share_line_indices(),
        )
        return self._share_text_for_lines(lines, include_timestamps)

    def copy_share_text(self):
        contents = self._share_text()
        if contents is None:
            return
        QApplication.clipboard().setText(contents)
        mode = "timestamped lyrics" if self.share_format_combo.currentData() == "lrc" else "plain lyrics"
        self.share_status_label.setText(f"Copied {mode} to clipboard.")

    def export_share_text(self):
        contents = self._share_text()
        if contents is None:
            return

        extension = ".lrc" if self.share_format_combo.currentData() == "lrc" else ".txt"
        default_path = self.lrc_path.with_name(
            f"{self.lrc_path.stem}-share{extension}"
        )
        file_filter = "LRC Files (*.lrc)" if extension == ".lrc" else "Text Files (*.txt)"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Lyrics",
            str(default_path),
            file_filter,
        )
        if not path:
            return

        target = Path(path)
        if target.suffix.lower() != extension:
            target = target.with_suffix(extension)
        try:
            atomic_write_text(target, contents, backup=True)
        except OSError as error:
            QMessageBox.warning(
                self,
                "Export Lyrics",
                f"Could not export lyrics:\n{error}",
            )
            return
        self.share_status_label.setText(f"Exported to {target}.")

    def import_ass(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import ASS/SSA Lyrics",
            str(self.lrc_path.parent),
            "ASS/SSA Subtitle Files (*.ass *.ssa);;All Files (*)",
        )
        if not path:
            return
        try:
            imported = parse_ass(path)
        except Exception as error:
            QMessageBox.warning(self, "Import ASS", f"Could not import subtitle:\n{error}")
            return
        if not imported:
            QMessageBox.information(
                self,
                "Import ASS",
                "No Dialogue lines were found in this ASS/SSA file.",
            )
            return
        self._push_undo_state()
        self.lines = imported
        self.document_dirty = True
        self.karaoke_dirty = any(
            getattr(line, "has_karaoke", False) for line in self.lines
        )
        self._populate_lines()
        self.line_list.setCurrentRow(0)
        self.sync_source_from_model()
        QMessageBox.information(
            self,
            "Import ASS",
            f"Imported {len(imported)} dialogue line(s). Karaoke tags were converted to editable LRCX timing.",
        )

    def sync_source_from_model(self):
        if self._source_dirty:
            answer = QMessageBox.question(
                self, "Replace Source Edits?",
                "Replace the pending source edits with the structured lyrics?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._push_undo_state()
        contents = self._serialized_for_current_format()
        self._syncing_source = True
        try:
            blocker = QSignalBlocker(self.source_editor)
            self.source_editor.setPlainText(self._serialise_with_attachment(contents))
            del blocker
            self._source_dirty = False
            self.source_format_label.setText(f"{self._source_suffix[1:].upper()} source")
        finally:
            self._syncing_source = False

    def _commit_pending_source(self):
        if self._source_dirty:
            self.reload_model_from_source()
        return not self._source_dirty

    def reload_model_from_source(self):
        text = self.source_editor.toPlainText()
        suffix = self._source_suffix
        if suffix == ".lrc" and (
            re.search(r"(?mi)^\[format:LRCX\]", text)
            or re.search(r"<\d+:\d+(?:\.\d+)?>|\{[^{}|]+\|[^{}]+\}", text)
        ):
            suffix = ".lrcx"
        try:
            parsed = parse_editor_source(text, suffix)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Reload Source", f"Could not parse source:\n{error}")
            return False
        self._push_undo_state()
        self._reading_generation_epoch += 1
        self.lines = parsed.lines
        self.lyrics_offset_ms = parsed.offset_ms
        self.attached_song_path = parsed.attachment
        self._document_metadata = parsed.metadata
        self._apply_generation_mode(parsed.generation_mode or "auto")
        self._source_suffix = suffix
        self._source_dirty = False
        blocker = QSignalBlocker(self.lyrics_offset)
        self.lyrics_offset.setValue(self.lyrics_offset_ms)
        del blocker
        self.source_format_label.setText(f"{suffix[1:].upper()} source")
        self._populate_lines(preserve_line_index=0)
        self.karaoke_dirty = any(line.has_karaoke for line in self.lines)
        self._update_song_association_ui()
        self._mark_document_dirty()
        return True

    def _lrcx_only_features(self):
        """Return human-readable LRCX-only features currently in the document."""
        features = []
        if any("\n" in line.text for line in self.lines):
            features.append("Multiline lyrics")

        if abs(float(getattr(self, "lyrics_offset_ms", 0.0))) > 0.0001:
            features.append("Lyric offset")

        # Automatically generated readings are reproducible display data and
        # are intentionally omitted by the LRCX serializer. Only explicitly
        # authored/imported ruby requires LRCX storage.
        has_ruby = any(
            (
                segment.ruby
                and getattr(segment, "ruby_source", None) != "generated"
            )
            or any(
                reading
                for _, _, reading in getattr(segment, "ruby_spans", []) or []
            )
            for line in self.lines
            for segment in line.segments
        )
        if has_ruby:
            features.append("Furigana")

        # Plain LRC only stores one timestamp per lyric line. Multiple segment
        # timestamps or instant markers are karaoke/LRCX data.
        # Segment count alone is not karaoke: automatic ruby generation can
        # create internal render tokens. Karaoke is an explicit LyricLine
        # property and must never be inferred from generated segmentation.
        has_karaoke = any(
            getattr(line, "has_karaoke", False)
            for line in self.lines
        )
        if has_karaoke:
            features.append("Karaoke timing")

        return features

    def _choose_lrc_save(self):
        features = self._lrcx_only_features()

        # A normal LRC edit (changing lyric text or its single line timestamp)
        # does not require a format decision. Keep the existing .lrc directly.
        # Only show the LRCX warning when the document actually contains data
        # that plain LRC cannot represent.
        if not features:
            return self.lrc_path

        box = QMessageBox(self)
        box.setWindowTitle("LRCX features detected")
        box.setIcon(QMessageBox.Icon.Warning)

        if features:
            feature_list = "\n".join(f"• {feature}" for feature in features)
            box.setText(
                "This .lrc file contains features that plain LRC cannot store."
            )
            box.setInformativeText(
                "The following data will be lost if you keep the file as .lrc:\n\n"
                f"{feature_list}\n\n"
                "Save as LRCX to preserve everything."
            )
            lrc_button = box.addButton(
                "Save as LRC anyway",
                QMessageBox.ButtonRole.DestructiveRole,
            )
        lrcx_button = box.addButton(
            "Save as LRCX",
            QMessageBox.ButtonRole.AcceptRole,
        )
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)

        box.exec()
        clicked = box.clickedButton()

        if clicked is lrcx_button:
            default = str(self.lrc_path.with_suffix(".lrcx"))
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Lyrics as LRCX",
                default,
                "LRCX Files (*.lrcx)",
            )
            return Path(path) if path else None

        if clicked is lrc_button:
            return self.lrc_path

        return None

    def _choose_txt_conversion_save(self):
        box = QMessageBox(self)
        box.setWindowTitle("Convert TXT lyrics")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("TXT cannot preserve lyric timestamps or advanced lyric features.")
        box.setInformativeText(
            "Save this edited lyric as LRC or LRCX before continuing."
        )
        lrc_button = box.addButton("Save as LRC", QMessageBox.ButtonRole.AcceptRole)
        lrcx_button = box.addButton("Save as LRCX", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is lrc_button:
            default = str(self.lrc_path.with_suffix(".lrc"))
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Lyrics as LRC", default, "LRC Files (*.lrc)"
            )
        elif clicked is lrcx_button:
            default = str(self.lrc_path.with_suffix(".lrcx"))
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Lyrics as LRCX", default, "LRCX Files (*.lrcx)"
            )
        else:
            return None
        return Path(path) if path else None

    def save_document(self, close_after_save=False):
        # Source is a real editable view. Parse it back into the model before
        # saving so typing directly in the Source tab is never silently ignored.
        if getattr(self, "_source_dirty", False):
            self.reload_model_from_source()
            if getattr(self, "_source_dirty", False):
                # Parsing failed; reload_model_from_source showed the error.
                return

        if not self.document_dirty and self.lrc_path.is_file():
            if close_after_save:
                self.accept()
            return True

        target = self.lrc_path
        if target.suffix.lower() == ".txt" and self.document_dirty:
            target = self._choose_txt_conversion_save()
            if target is None:
                return
        elif target.suffix.lower() == ".lrc" and self.document_dirty:
            target = self._choose_lrc_save()
            if target is None:
                return
        try:
            mode = self._current_generation_mode()
            if target.suffix.lower() == ".lrcx":
                contents = serialize_lrcx(
                    self.lines,
                    offset_ms=self.lyrics_offset_ms,
                    generation_mode=mode,
                )
            else:
                contents = serialize_lrc(self.lines, generation_mode=mode)
            contents = self._serialise_with_attachment(contents)
            atomic_write_text(target, contents, backup=True)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "Save Lyrics", f"Could not save lyrics:\n{error}")
            return
        self.result_path = target
        self.lrc_path = Path(target)
        self.is_new_document = False
        self._update_document_title()
        # The final save target may differ after Save As. Point the persisted
        # association at the file that actually contains [attach:...].
        if self.attached_song_path:
            try:
                set_lyrics_attachment(self.attached_song_path, target)
            except OSError as error:
                QMessageBox.warning(
                    self, "Lyrics Association",
                    f"Lyrics were saved, but the song association could not be stored:\n{error}",
                )
        self.document_dirty = False
        self._source_dirty = False
        self._update_document_title()
        self._discard_autosave()

        # Keep the main player in sync with the file that was actually saved.
        # This is especially important when Save As changes .lrc -> .lrcx (or
        # when the editor was opened on an associated external lyrics file):
        # closing the editor must not leave the main lyrics view pointing at
        # the old path. Defer one event-loop turn so the save handler completes
        # before the parent rebuilds its lyric widgets.
        parent = self.player
        clear_index = getattr(parent, "clear_lyrics_search_index", None)
        if callable(clear_index):
            clear_index()
        loader = getattr(parent, "load_lrc_file", None) if parent is not None else None
        changed_song = getattr(self, "_association_changed_song", None)
        if changed_song is not None:
            self._refresh_player_song_association(changed_song)
        elif callable(loader):
            def _refresh_saved_lyrics(path=target, owner=parent):
                try:
                    owner.load_lrc_file(str(path))
                    owner.current_lyrics_path = Path(path)
                except Exception as error:
                    debug_print(
                        f"[Lyrics Editor] Could not refresh saved lyrics "
                        f"{path!s}: {error!r}"
                    )

            QTimer.singleShot(0, _refresh_saved_lyrics)

        self._saved_document_state = self._document_state()
        self._autosave_path = self.lrc_path.with_name(f".{self.lrc_path.name}.autosave")
        self.sync_source_from_model()
        if close_after_save:
            self.accept()
        return True

    def save_and_close(self):
        return self.save_document(close_after_save=True)

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
            self._discard_autosave()
            super().reject()

    def closeEvent(self, event):
        if not self._confirm_close():
            event.ignore()
            return
        self._discard_autosave()
        self.done(QDialog.DialogCode.Rejected)
        event.accept()

    def done(self, result):
        self._closed = True
        if self._segment_preview_range is not None:
            media = getattr(self.player, "media_player", None)
            if media is not None:
                media.pause()
            self._segment_preview_range = None
        self._discard_autosave()
        self._reading_generation_epoch += 1
        self._autosave_timer.stop()
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
