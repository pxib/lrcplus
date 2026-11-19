"""Playback transport, waveform editing and keyboard input for the lyrics editor."""

from pathlib import Path
import math
import time
from PySide6.QtCore import Qt, QSignalBlocker, QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QLineEdit,
    QMessageBox,
    QDoubleSpinBox,
    QTextEdit,
    QPlainTextEdit,
    QWidget,
    QTableWidget,
    QComboBox,
    QApplication,
)
from lyrics.writer import format_timestamp
from lyrics.karaoke_timing import next_lyric_start, segment_end


class LyricsEditorPlaybackMixin:
    # Letters are timing controls only outside text inputs. Modified shortcuts
    # remain available while editing, and all are confined to the Lyrics tab.
    _LYRICS_SHORTCUTS = (
        ("Space", "Play / pause", "toggle_preview", ()),
        ("Q", "Seek -100 ms", "_seek_preview_relative", (-100,)),
        ("E", "Seek +100 ms", "_seek_preview_relative", (100,)),
        ("A", "Seek -1 second", "_seek_preview_relative", (-1000,)),
        ("D", "Seek +1 second", "_seek_preview_relative", (1000,)),
        ("F", "Mark Playback Time and advance", "mark_current_line_playback_time", ()),
        ("Ctrl+Return", "Mark Playback Time and advance", "mark_current_line_playback_time", ()),
        ("Ctrl+Shift+Return", "Commit timestamp / waveform boundary", "commit_line_timestamp", ()),
        ("Alt+Up", "Select previous lyric in sidebar order", "_select_relative_lyric", (-1,)),
        ("Alt+Down", "Select next lyric in sidebar order", "_select_relative_lyric", (1,)),
        ("Ctrl+N", "Add line", "add_line", ()),
        ("Alt+Shift+Up", "Move line up in source order", "move_line", (-1,)),
        ("Alt+Shift+Down", "Move line down in source order", "move_line", (1,)),
    )

    def _setup_lyrics_shortcuts(self):
        self._lyrics_shortcut_bindings = {}
        for sequence, _label, handler, args in self._LYRICS_SHORTCUTS:
            combination = QKeySequence(sequence)[0].toCombined()
            self._lyrics_shortcut_bindings[combination] = (handler, args)
            # Treat the main Enter key and the numeric keypad Enter alike.
            if "Return" in sequence:
                combination = QKeySequence(sequence.replace("Return", "Enter"))[0].toCombined()
                self._lyrics_shortcut_bindings[combination] = (handler, args)

    def _select_relative_lyric(self, direction):
        row = self.line_list.currentRow()
        target = row + direction
        if 0 <= target < self.line_list.count():
            self._select_line_index(self._view_line_indices[target])
            self.line_list.scrollToItem(self.line_list.currentItem())

    def show_lyrics_shortcuts(self):
        from ui.editor_shortcuts import EditorShortcutsDialog

        sections = [
            ("Document", [
                ("F1", "Open this shortcut and waveform reference"),
                ("Ctrl+S", "Save and keep editing"),
                ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
                ("Ctrl+F", "Find lyrics"),
                ("F3 / Shift+F3", "Next / previous search match"),
                ("Ctrl+wheel", "Zoom source text (Source tab)"),
                ("Delete", "Delete selected lines (lyric list only)"),
                ("Ctrl+B / Ctrl+I / Ctrl+U", "Bold / italic / underline in lyric text"),
                ("Ctrl+Shift+X", "Strikethrough in lyric text"),
            ]),
            ("Lyrics timing — Lyrics tab", [
                (sequence.replace("Return", "Enter"), label)
                for sequence, label, _handler, _args in self._LYRICS_SHORTCUTS
            ] + [
                ("While typing", "Space and Q/E/A/D/F are disabled while typing in text, reading, search, or timestamp fields"),
                ("Hold a seek key", "Repeat seeking; marking, adding, and moving lines require separate presses"),
            ]),
            ("Waveform — navigation", [
                ("Ctrl+left click", "Seek without changing timing or starting playback"),
                ("Shift+click waveform", "Play continuously from the clicked position (Lyrics tab)"),
                ("Ctrl+wheel", "Zoom around the mouse cursor"),
                ("Mouse wheel", "Pan earlier / later"),
                ("Middle-button drag", "Pan the timeline"),
                ("+ / −", "Zoom in / out (waveform focused)"),
                ("Left / Right", "Pan earlier / later (waveform focused)"),
                ("Home", "Fit the selected lyric line (waveform focused)"),
                ("Ctrl+Home", "Show all audio (waveform focused)"),
            ]),
            ("Waveform — boundaries", [
                ("Left click / drag", "Place / move the selected lyric or segment Start boundary"),
                ("Right click / drag", "Place / move the selected lyric or segment End boundary"),
                ("Commit / Ctrl+Shift+Enter", "Apply provisional lyric boundaries (Lyrics tab)"),
            ]),
        ]
        if self.karaoke_enabled:
            sections.extend([
                ("Karaoke timing — Karaoke tab", [
                    ("Space", "Play / pause, outside text inputs"),
                    ("Q / E", "Seek −100 / +100 ms"),
                    ("A / D", "Seek −1 / +1 second"),
                    ("F", "Mark the current Start / End and advance"),
                    ("Start / End cells", "Timing letters also work in timestamp cells; other text fields keep normal typing"),
                    ("Earlier / Later buttons", "Nudge the selected Start / End by the chosen step"),
                    ("Preview Segment button", "Play the selected segment with extra context if it is very short"),
                ]),
                ("Waveform — karaoke segments", [
                    ("Ctrl+right click", "Preview the clicked segment with extra context if it is very short"),
                    ("Alt+left / right drag", "Move a shared boundary with its connected neighboring segment"),
                    ("Hold Shift during a drag", "Disable snapping to adjacent segment boundaries"),
                    ("Release mouse button", "Finish the timing edit; use Undo to restore the previous boundary"),
                ]),
            ])
        dialog = EditorShortcutsDialog(sections, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _handle_lyrics_shortcut(self, watched, event):
        if (
            not isinstance(watched, QWidget)
            or watched.window() is not self
            or self.tabs.currentWidget() is not self.lyrics_page
        ):
            return False
        binding = getattr(self, "_lyrics_shortcut_bindings", {}).get(
            event.keyCombination().toCombined() & ~Qt.KeyboardModifier.KeypadModifier.value
        )
        if binding is None:
            return False
        if (
            event.modifiers() == Qt.KeyboardModifier.NoModifier
            and (self._focus_is_text_input(watched) or self._focus_is_text_input())
        ):
            return False
        handler, args = binding
        if not event.isAutoRepeat() or handler in {"_seek_preview_relative", "_select_relative_lyric"}:
            getattr(self, handler)(*args)
        event.accept()
        return True

    def preview_position_changed(self, position_ms):
        if self._segment_preview_range is not None:
            start_ms, end_ms = self._segment_preview_range
            if position_ms >= end_ms:
                self._finish_segment_preview()
                return
            elif position_ms < start_ms - 100:
                self._cancel_segment_preview()
            else:
                self._schedule_segment_preview_stop(position_ms)
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
        self._refresh_lyrics_preview(position_ms, rebuild_segments=False)

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
        self.lyrics_waveform_controls.setVisible(checked)
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
        self.karaoke_waveform_controls.setVisible(checked)

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
            [self._lyric_to_audio_ms(line.start) / 1000.0 for line in self.lines]
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
        start_ms = self._lyric_to_audio_ms(selected_start)

        # Use the actual yellow-marker timestamps shown on the waveform.
        # Identical timestamps collapse to one visual marker.
        later_markers = sorted({
            self._lyric_to_audio_ms(other.start)
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
            hard_boundary_ms = self._lyric_to_audio_ms(self.lines[hard_index].start)

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
        value_ms = max(0, round(float(value_ms) - self.lyrics_offset_ms))
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
        preview_end_ms = value_ms if handle == "end" else (
            round(self.lines[index + 1].start * 1000) if index + 1 < len(self.lines)
            else min(self.lyrics_waveform._duration_ms - self.lyrics_offset_ms, start_ms + 5000)
        )
        self.lyrics_waveform.set_selected_lyric_timing(
            self._lyric_to_audio_ms((start_ms if handle == "end" else value_ms) / 1000.0),
            self._lyric_to_audio_ms(preview_end_ms / 1000.0),
        )
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

        line_start_ms = self._lyric_to_audio_ms(line.start)

        # The karaoke line's real right boundary is the NEXT LYRIC timestamp,
        # not the last karaoke segment's current end. That is the reference
        # boundary the editor needs to show and enforce.
        next_line_start = next_lyric_start(self.lines, line)

        if next_line_start is not None:
            line_end_ms = self._lyric_to_audio_ms(next_line_start)
        elif waveform._duration_ms > 0:
            # Last lyric: audio end is the only natural hard boundary.
            line_end_ms = int(waveform._duration_ms)
        else:
            last = line.segments[-1]
            fallback_end = getattr(last, "end", None)
            if fallback_end is None:
                fallback_end = getattr(last, "start", line.start)
            line_end_ms = self._lyric_to_audio_ms(fallback_end)

        line_end_ms = max(line_start_ms + 1, line_end_ms)

        ranges = []
        for i, segment in enumerate(line.segments):
            seg_end = segment_end(line, i, self._audio_to_lyric_seconds(line_end_ms))
            label = getattr(segment, "text", None)
            if label is None:
                label = getattr(segment, "lyric", None)
            if label is None:
                label = getattr(segment, "value", "")
            ranges.append((
                self._lyric_to_audio_ms(segment.start) / 1000.0,
                self._lyric_to_audio_ms(seg_end) / 1000.0,
                label,
            ))

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
        self._refresh_lyrics_preview()

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
        new_start = self._audio_to_lyric_seconds(value_ms) if handle == "start" else old_start
        new_end = self._audio_to_lyric_seconds(value_ms) if handle == "end" else old_end
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
        value_ms = float(value_ms) - self.lyrics_offset_ms
        old = line.segments[row]
        hard_end = next_lyric_start(self.lines, line)
        duration_ms = getattr(self.karaoke_waveform, "_duration_ms", 0)
        if duration_ms > 0:
            audio_end = self._audio_to_lyric_seconds(duration_ms)
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
            self._schedule_segment_preview_stop(self.player.media_player.position())
        else:
            self._precise_display_timer.stop()
            self._segment_preview_timer.stop()

    def _update_precise_playback_display(self):
        if (
            not self._display_position_playing
            or self._display_position_anchor_monotonic is None
        ):
            return

        elapsed_ms = (
            time.monotonic() - self._display_position_anchor_monotonic
        ) * 1000.0
        media = getattr(self.player, "media_player", None)
        rate = getattr(media, "playbackRate", lambda: 1.0)()
        estimated_position_ms = max(
            0.0,
            self._display_position_anchor_ms + elapsed_ms * rate,
        )
        self._set_precise_time_display(estimated_position_ms)
        self._refresh_lyrics_preview(estimated_position_ms, rebuild_segments=False)

    def preview_duration_changed(self, duration_ms):
        self._schedule_timing_validation()
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
        self._cancel_segment_preview()
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

    def _focus_is_text_input(self, widget=None):
        """Do not hijack normal typing while an editor widget has focus."""
        widget = widget if widget is not None else QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)):
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
        self.show_lyrics_shortcuts()

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        if event.key() == Qt.Key.Key_Space and watched.property("waveformNavigation"):
            # Space activates a focused navigation button through Qt. Other
            # controls keep the editor's familiar Space-to-play behavior.
            return super().eventFilter(watched, event)
        if (event.key() == Qt.Key.Key_F1
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and isinstance(watched, QWidget) and watched.window() is self):
            if not event.isAutoRepeat():
                self.show_lyrics_shortcuts()
            event.accept()
            return True
        source = getattr(self, "source_editor", None)
        if source is not None and (
            watched is source or (isinstance(watched, QWidget) and source.isAncestorOf(watched))
        ):
            # QTextEdit consumes its own Undo/Redo keys even when native text
            # history is empty. Route them like the toolbar so a committed
            # source edit can still be undone through document history.
            if event.matches(QKeySequence.StandardKey.Undo):
                self.undo()
                event.accept()
                return True
            if event.matches(QKeySequence.StandardKey.Redo):
                self.redo()
                event.accept()
                return True
        if hasattr(self, "lyrics_page") and self._handle_lyrics_shortcut(watched, event):
            return True
        if event.modifiers() != Qt.KeyboardModifier.NoModifier:
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
        if getattr(self, "_choosing_document", False):
            return super().keyPressEvent(event)
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

    def preview_selected_timing(self):
        self.preview_status.clear()
        if not self._commit_pending_source():
            return
        if self._karaoke_shortcut_active():
            self.preview_karaoke_segment()
            return
        _, line = self._current_line()
        media = getattr(self.player, "media_player", None)
        if line is None or media is None:
            self.preview_status.setText("Load a song and select a lyric to preview.")
            return
        end = line.end if line.end is not None else next_lyric_start(self.lines, line)
        if end is None and media.duration() > 0:
            end = self._audio_to_lyric_seconds(media.duration())
        if end is None or end <= line.start:
            self.preview_status.setText("Set an end time or a later lyric to preview this line.")
            return
        self.play_waveform_segment(self._lyric_to_audio_ms(line.start), self._lyric_to_audio_ms(end))

    def _sync_preview_speed(self, rate):
        if not hasattr(self, "preview_speed"):
            return
        blocker = QSignalBlocker(self.preview_speed)
        index = self.preview_speed.findData(rate)
        if index < 0:
            self.preview_speed.addItem(f"{rate:g}×", rate)
            index = self.preview_speed.count() - 1
        self.preview_speed.setCurrentIndex(index)
        del blocker

    def change_preview_speed(self, _index):
        change_speed = getattr(self.player, "handle_speed_change", None)
        if callable(change_speed):
            # The controller also updates PCM processing, pitch compensation,
            # gapless routing and queued audio when the playback rate changes.
            change_speed(round(self.preview_speed.currentData() * 100))

    def play_karaoke_waveform_segment(self, start_ms, end_ms):
        self.play_waveform_segment(start_ms, end_ms, minimum_playback_ms=250)

    def play_waveform_segment(self, start_ms, end_ms, *, minimum_playback_ms=0):
        """Preview a bounded range with optional padding and repetition."""
        if self.player is None or not hasattr(self.player, "media_player"):
            return
        if end_ms <= start_ms:
            return
        start_ms = max(0, int(start_ms) - round(self.preview_lead_in.value() * 1000))
        end_ms = int(end_ms) + round(self.preview_lead_out.value() * 1000)
        media = self.player.media_player
        duration = media.duration()
        if duration > 0:
            end_ms = min(end_ms, duration)
        if end_ms <= start_ms:
            self.preview_status.setText("This selection is outside the audio duration.")
            return
        pause_ms = end_ms
        if minimum_playback_ms:
            rate = getattr(media, "playbackRate", lambda: 1.0)()
            minimum_span_ms = math.ceil(minimum_playback_ms * rate)
            missing_ms = minimum_span_ms - (end_ms - start_ms)
            if missing_ms > 0:
                # Allow the output buffer to play the selection before pausing.
                start_ms = max(0, start_ms - missing_ms // 2)
                end_ms = start_ms + minimum_span_ms
                if duration > 0 and end_ms > duration:
                    end_ms = duration
                    start_ms = max(0, end_ms - minimum_span_ms)
        self.preview_status.clear()
        self._start_segment_preview(start_ms, end_ms, pause_ms)

    def _start_segment_preview(self, start_ms, end_ms, pause_ms):
        media_player = self.player.media_player
        self._cancel_segment_preview()
        media_player.setPosition(int(start_ms))
        self._segment_preview_range = (int(start_ms), int(end_ms))
        self._segment_preview_pause_ms = pause_ms
        play = getattr(media_player, "play", None)
        if callable(play):
            play()
            self._schedule_segment_preview_stop(start_ms)

    def _cancel_segment_preview(self):
        self._segment_preview_range = None
        self._segment_preview_pause_ms = None
        self._segment_preview_timer.stop()

    def _schedule_segment_preview_stop(self, position_ms):
        self._segment_preview_timer.stop()
        if self._segment_preview_range is None:
            return
        media = getattr(self.player, "media_player", None)
        if media is None:
            return
        is_playing = getattr(media, "isPlaying", None)
        if callable(is_playing) and not is_playing():
            return
        rate = getattr(media, "playbackRate", lambda: 1.0)()
        remaining_ms = self._segment_preview_range[1] - position_ms
        estimated_delay_ms = math.ceil(remaining_ms / max(0.01, rate))
        # Poll between position notifications without spinning when the backend
        # reports a position just short of the end.
        self._segment_preview_timer.start(max(15, estimated_delay_ms))

    def _check_segment_preview_end(self):
        if self._segment_preview_range is None:
            return
        media = self.player.media_player
        position_ms = media.position()
        if position_ms >= self._segment_preview_range[1]:
            self._finish_segment_preview()
        else:
            # A seek or decoder startup can consume the entire estimated
            # duration of a short preview before the audio starts moving.
            self._schedule_segment_preview_stop(position_ms)

    def _segment_preview_rate_changed(self, rate):
        self._sync_preview_speed(rate)
        media = getattr(self.player, "media_player", None)
        if media is not None:
            self._schedule_segment_preview_stop(media.position())
            self._display_position_anchor_ms = float(media.position())
            self._display_position_anchor_monotonic = time.monotonic()

    def _finish_segment_preview(self):
        if self._segment_preview_range is None:
            return
        start_ms, end_ms = self._segment_preview_range
        pause_ms = self._segment_preview_pause_ms
        if self.preview_loop.isChecked() and not self._closed:
            self._start_segment_preview(start_ms, end_ms, pause_ms)
            return
        self._cancel_segment_preview()
        media = getattr(self.player, "media_player", None)
        if media is not None:
            media.pause()
            media.setPosition(pause_ms)
        self.preview_position_changed(pause_ms)

    def seek_preview(self, position_ms):
        self._cancel_segment_preview()
        if self.player is not None and hasattr(self.player, "media_player"):
            self.player.media_player.setPosition(int(position_ms))

    def play_preview_from(self, position_ms):
        """Start continuous playback at an audio position without editing timing."""
        if self.player is None or not hasattr(self.player, "media_player"):
            return
        self.seek_preview(position_ms)
        self.player.media_player.play()

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

        position = max(0.0, self._audio_to_lyric_seconds(self.player.media_player.position()))
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
