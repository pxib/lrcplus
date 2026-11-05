"""Playback transport, waveform editing and keyboard input for the lyrics editor."""

from pathlib import Path
import time
from PySide6.QtCore import Qt, QSignalBlocker, QEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QLineEdit,
    QMessageBox,
    QDoubleSpinBox,
    QTextEdit,
    QWidget,
    QTableWidget,
    QComboBox,
    QApplication,
)
from lyrics.writer import format_timestamp
from lyrics.karaoke_timing import next_lyric_start, segment_end


class LyricsEditorPlaybackMixin:
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
