"""Document history, recovery, song associations, source editing and file export."""

from pathlib import Path
import copy
import json
import os
import re
from PySide6.QtCore import QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox, QApplication
from lyrics.loader import load_lyrics, get_lyrics_generation_mode
from lyrics.writer import format_timestamp, serialize_lrc, serialize_lrcx
from lyrics.editor_source import extract_metadata, parse_editor_source
from core.debug import debug_print
from core.file_io import atomic_write_text
from lyrics.ass_parser import parse_ass
from core.settings import get_lyrics_attachment, set_lyrics_attachment


class LyricsEditorDocumentMixin:
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
            self.insert_newline_button.setEnabled(False)
            self.insert_translation_button.setEnabled(False)
            self.quote_button.setChecked(False)
            for button in self.lyric_style_buttons:
                button.setEnabled(False)
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

    def _autosave_payload(self):
        structured = self._serialise_with_attachment(serialize_lrcx(
            self.lines, offset_ms=self.lyrics_offset_ms,
            generation_mode=self._current_generation_mode(),
        ))
        return json.dumps({
            "format": "LRCPlusRecovery", "version": 1,
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
                if payload.get("format") != "LRCPlusRecovery" or payload.get("version") != 1:
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

    def _is_editing_current_song(self):
        return self._normalise_song_path(self._current_song_path()) == self._editing_song_path

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
        self.setWindowTitle(f"Lyrics Editor 4.0 — {title}")

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
            self.lines = load_lyrics(self.lrc_path, infer_ends=False)
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
        if not lines:
            self.share_status_label.setText("Select lyric lines to copy or export.")
            return None
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
        if any(line.style_spans for line in self.lines):
            features.append("Text formatting")
        if any(line.is_blockquote for line in self.lines):
            features.append("Blockquotes")

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

    def _choose_lrc_save(self, target=None):
        target = Path(target) if target is not None else self.lrc_path
        features = self._lrcx_only_features()

        # A normal LRC edit (changing lyric text or its single line timestamp)
        # does not require a format decision. Keep the existing .lrc directly.
        # Only show the LRCX warning when the document actually contains data
        # that plain LRC cannot represent.
        if not features:
            return target

        box = QMessageBox(self)
        box.setWindowTitle("LRCX features detected")
        box.setIcon(QMessageBox.Icon.Warning)

        if features:
            feature_list = "\n".join(f"• {feature}" for feature in features)
            box.setText(
                "These lyrics contain features that plain LRC cannot store."
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
            default = str(target.with_suffix(".lrcx"))
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Lyrics as LRCX",
                default,
                "LRCX Files (*.lrcx)",
            )
            return Path(path) if path else None

        if clicked is lrc_button:
            return target

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
        if target.suffix.lower() == ".lrc" and self.document_dirty:
            target = self._choose_lrc_save(target)
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
        self._update_save_button_state()
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
                if not self._is_editing_current_song():
                    return
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
