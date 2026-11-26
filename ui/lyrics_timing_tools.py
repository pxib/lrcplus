"""Batch timing and validation tools for the structured lyrics editor."""

from copy import deepcopy
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QProgressDialog, QPushButton, QSpinBox, QVBoxLayout, QStyle,
)

from core.audio_timing_scan import correct_audio_timing
from core.background_job import BackgroundJob
from lyrics.timing import shift_timing, validate_timing
from lyrics.timing_drift import estimate_timing_correction, load_timing_reference
from lyrics.writer import format_timestamp


class LyricsTimingToolsMixin:
    def _build_inline_timing_issues(self, layout):
        self.timing_drift_status = QLabel()
        self.timing_drift_status.setTextFormat(Qt.TextFormat.PlainText)
        self.timing_drift_status.setWordWrap(True)
        self.timing_drift_status.hide()
        layout.addWidget(self.timing_drift_status)
        self.timing_issues_toggle = QPushButton("Timing: no issues")
        self.timing_issues_toggle.setCheckable(True)
        self.timing_issues_toggle.setChecked(True)
        self.timing_issues_toggle.setToolTip("Show or hide timing issues. Overlaps may be intentional.")
        layout.addWidget(self.timing_issues_toggle)
        self.timing_issues_list = QListWidget()
        self.timing_issues_list.setAccessibleName("Timing issues; select an issue to edit its lyric")
        self.timing_issues_list.setWordWrap(True)
        self.timing_issues_list.setMaximumHeight(130)
        self.timing_issues_list.itemClicked.connect(self._go_to_timing_issue)
        self.timing_issues_list.itemActivated.connect(self._go_to_timing_issue)
        layout.addWidget(self.timing_issues_list)
        self.timing_issues_toggle.toggled.connect(lambda expanded: self.timing_issues_list.setVisible(
            expanded and self.timing_issues_list.count() > 0))
        self._timing_validation_timer = QTimer(self)
        self._timing_validation_timer.setSingleShot(True)
        self._timing_validation_timer.setInterval(200)
        self._timing_validation_timer.timeout.connect(self._refresh_inline_timing_issues)

    def _schedule_timing_validation(self):
        timer = getattr(self, "_timing_validation_timer", None)
        if timer is not None and not self._closed:
            timer.start()

    def _refresh_inline_timing_issues(self):
        if self._closed:
            return
        self._timing_validation_timer.stop()
        media = getattr(self.player, "media_player", None)
        pending = self._source_dirty
        plain_text = self._source_suffix == ".txt"
        issues = [] if pending or plain_text else validate_timing(
            self.lines, duration_ms=getattr(media, "duration", lambda: None)(),
            offset_ms=self.lyrics_offset_ms)
        by_line = {}
        for issue in issues:
            by_line.setdefault(issue.line_index, []).append(issue.message)
        warning = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        blocker = QSignalBlocker(self.line_list)
        for row in range(self.line_list.count()):
            item = self.line_list.item(row)
            messages = by_line.get(item.data(Qt.ItemDataRole.UserRole), [])
            item.setIcon(warning if messages else QIcon())
            item.setToolTip("\n".join(messages))
        del blocker
        # Keep keyboard focus and selection stable across a live refresh.
        current = self.timing_issues_list.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.timing_issues_list.clear()
        for issue in issues:
            item = QListWidgetItem(f"Line {issue.line_index + 1}: {issue.message}")
            item.setData(Qt.ItemDataRole.UserRole, issue.line_index)
            item.setToolTip(item.text())
            self.timing_issues_list.addItem(item)
            if issue.line_index == selected:
                self.timing_issues_list.setCurrentItem(item)
        message = f"Timing issues ({len(issues)})" if issues else "Timing: no issues"
        if pending:
            message = "Timing: source edits pending"
        elif plain_text:
            message = "Timing: plain text"
        self.timing_issues_toggle.setText(message)
        self.timing_issues_toggle.setEnabled(bool(issues))
        self.timing_issues_list.setVisible(bool(issues) and self.timing_issues_toggle.isChecked())

    def _go_to_timing_issue(self, item):
        if not self._commit_pending_source():
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        self.tabs.setCurrentWidget(self.lyrics_page)
        self._select_line_index(index)

    def _prepare_timing_tools(self):
        if self._source_dirty:
            self.reload_model_from_source()
        if self._source_dirty:
            return False
        if not self.lines:
            QMessageBox.information(self, "Lyric Timing", "Add lyric lines before using timing tools.")
            return False
        return True

    def _apply_timing_shift(self, delta_ms, first, last):
        if delta_ms == 0:
            return
        self._apply_timing_edit(shift_timing(self.lines, delta_ms, first=first, last=last))

    def _apply_timing_edit(self, lines, *, offset_ms=None):
        selected = self._current_line_index()
        self._push_undo_state()
        self.lines = lines
        if offset_ms is not None:
            self.lyrics_offset_ms = offset_ms
            with QSignalBlocker(self.lyrics_offset):
                self.lyrics_offset.setValue(offset_ms)
        self._populate_lines(preserve_line_index=selected)
        if selected is not None:
            self._select_line_index(selected, preserve_selection=True)
        self._refresh_karaoke_waveform_editor()
        self._mark_document_dirty()

    def correct_current_timing_drift(self):
        if not self._prepare_timing_tools():
            return
        status = self.timing_drift_status
        status.show()
        if self._source_suffix == ".txt":
            status.setText("Start with lyric lines that already have timestamps.")
            return
        audio = self._current_song_path()
        if audio is None or not audio.is_file() or not self._is_editing_current_song():
            status.setText("Load the local recording for these lyrics to correct timing drift.")
            return
        lines = deepcopy(self.lines)
        offset = self.lyrics_offset_ms
        media = getattr(self.player, "media_player", None)
        duration = getattr(media, "duration", lambda: 0)()
        cancelled = Event()
        outcome = []
        progress = QProgressDialog("Checking timing for the loaded recording…", "Cancel", 0, 0, self)
        progress.setWindowTitle("Correct Timing Drift")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        job = BackgroundJob(progress, "lyric-timing-drift")

        def finish(result=None, error=None):
            outcome.append((result, error))
            progress.accept()

        progress.canceled.connect(cancelled.set)
        progress.canceled.connect(progress.reject)
        job.finished.connect(finish)
        job.failed.connect(lambda error: finish(error=error))
        status.hide()
        job.start(correct_audio_timing, audio, lines, offset, duration, cancelled)
        try:
            progress.exec()
        finally:
            cancelled.set()
            job.close()
            progress.deleteLater()
        if not outcome or self._closed:
            return
        result, error = outcome[0]
        if not self._is_editing_current_song() or self._source_dirty or self.lines != lines or self.lyrics_offset_ms != offset:
            status.setText("The lyrics or recording changed during analysis. Run the correction again.")
        elif error:
            status.setText(error)
        elif result is None:
            status.setText("The current lyrics already follow the recording.")
        else:
            updated, corrected_offset = result
            self._apply_timing_edit(updated, offset_ms=corrected_offset)
            status.setText("Timing corrected. You can undo the edit or save the lyrics.")
        status.show()

    def show_timing_drift_dialog(self):
        if not self._prepare_timing_tools():
            return
        title = "Match Timing to Reference"
        if self._source_suffix == ".txt":
            QMessageBox.information(self, title, "Start with lyric lines that already have timestamps.")
            return
        reference, _ = QFileDialog.getOpenFileName(
            self, "Choose timed lyrics or subtitles for this recording", str(self.lrc_path.parent),
            "Timed lyrics, LRCX backups, and subtitles (*.lrc *.lrcx *.lrcx.bak *.srt *.vtt)",
        )
        if not reference:
            return
        try:
            if Path(reference).resolve() == self.lrc_path.resolve():
                raise ValueError("Choose a separate timing reference for this recording.")
            reference_lines, reference_offset = load_timing_reference(reference)
            offset = self.lyrics_offset_ms
            correction = estimate_timing_correction(
                self.lines, reference_lines, offset_ms=offset, reference_offset_ms=reference_offset,
            )
            if correction is None:
                QMessageBox.information(self, title, "The current timings closely match the reference.")
                return
            media = getattr(self.player, "media_player", None)
            can_listen = bool(self._editing_song_path and media is not None
                              and self._is_editing_current_song() and media.duration() > 0)
            updated, corrected_offset = correction.apply(
                self.lines, offset_ms=offset, duration_ms=media.duration() if can_listen else 0,
            )
        except (OSError, ValueError, OverflowError) as error:
            QMessageBox.information(self, title, str(error))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        summary = QLabel(
            f"{len(correction.matches)} matched lines. Timestamp spacing changes by "
            f"{(correction.scale - 1):+.2%}. The largest difference from the reference "
            f"is {correction.max_residual * 1000:.0f} ms."
        )
        if corrected_offset != offset:
            summary.setText(summary.text() + f" The lyrics offset changes from {offset:g} to {corrected_offset:g} ms.")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        matches = QListWidget()
        matches.setAccessibleName("Matched lyrics with current and proposed playback times")
        for match in correction.matches:
            index = match.line_index
            current = format_timestamp(round(self.lines[index].start + offset / 1000, 3))
            proposed = format_timestamp(round(updated[index].start + corrected_offset / 1000, 3))
            text = " / ".join(self.lines[index].text.splitlines())
            matches.addItem(f"{current} → {proposed}   {text}")
        matches.setCurrentRow(0)
        layout.addWidget(matches)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        listen = buttons.addButton("Listen at proposed time", QDialogButtonBox.ButtonRole.ActionRole)
        listen.setEnabled(can_listen)
        layout.addWidget(buttons)
        previous_playback = None

        def preview():
            nonlocal previous_playback
            if not self._is_editing_current_song():
                return
            if previous_playback is None:
                previous_playback = media.position(), media.playbackState()
            index = correction.matches[matches.currentRow()].line_index
            start = round(updated[index].start * 1000 + corrected_offset)
            self._start_segment_preview(max(0, start - 1000), min(media.duration(), start + 2500), start)

        listen.clicked.connect(preview)
        dialog.resize(620, 340)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            if previous_playback is not None:
                self._cancel_segment_preview()
                if self._is_editing_current_song():
                    position, state = previous_playback
                    if state == QMediaPlayer.PlaybackState.StoppedState:
                        media.stop()
                    elif state == QMediaPlayer.PlaybackState.PausedState:
                        media.pause()
                    media.setPosition(position)
                    if state == QMediaPlayer.PlaybackState.PlayingState:
                        media.play()
            dialog.deleteLater()
        if accepted:
            self._apply_timing_edit(updated, offset_ms=corrected_offset)

    def show_timing_shift_dialog(self):
        if len(self._selected_line_indices()) > 1:
            self.show_selected_timing_shift_dialog()
            return
        if not self._prepare_timing_tools():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Shift Lyric Timing")
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "Shift a range of lines in source order, including all karaoke segment starts and ends. "
            "Positive values move lyrics later. The lyrics offset stays unchanged. Undo restores the previous timing."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        first = QSpinBox()
        last = QSpinBox()
        for field in (first, last):
            field.setRange(1, len(self.lines))
        last.setValue(len(self.lines))
        delta = QSpinBox()
        delta.setRange(-3_600_000, 3_600_000)
        delta.setSingleStep(50)
        delta.setSuffix(" ms")
        form.addRow("First line:", first)
        form.addRow("Last line:", last)
        form.addRow("Shift by:", delta)
        layout.addLayout(form)
        summary = QLabel()
        summary.setWordWrap(True)
        layout.addWidget(summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)

        def update_preview():
            try:
                shifted = shift_timing(self.lines, delta.value(), first=first.value() - 1, last=last.value() - 1)
                count = last.value() - first.value() + 1
                start = shifted[first.value() - 1].start
                summary.setText(f"{count} line(s). First affected line will start at {start:.3f} s.")
                apply_button.setEnabled(delta.value() != 0)
            except ValueError as error:
                summary.setText(str(error))
                apply_button.setEnabled(False)

        def apply():
            try:
                self._apply_timing_shift(delta.value(), first.value() - 1, last.value() - 1)
            except ValueError as error:
                QMessageBox.warning(dialog, "Shift Lyric Timing", str(error))
                return
            dialog.accept()

        for field in (first, last, delta):
            field.valueChanged.connect(update_preview)
        apply_button.clicked.connect(apply)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        update_preview()
        dialog.resize(460, 250)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def show_timing_validation(self):
        if not self._prepare_timing_tools():
            return
        media = getattr(self.player, "media_player", None)
        duration = media.duration() if media is not None else None
        issues = validate_timing(self.lines, duration_ms=duration, offset_ms=self.lyrics_offset_ms)
        if not issues:
            QMessageBox.information(self, "Validate Timing", "No timing issues found.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Validate Lyric Timing")
        layout = QVBoxLayout(dialog)
        hint = QLabel(f"{len(issues)} issue(s) to review. Overlaps may be intentional. Select an issue and choose Go to Line.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        results = QListWidget()
        for issue in issues:
            item = QListWidgetItem(f"Line {issue.line_index + 1}: {issue.message}")
            item.setData(Qt.ItemDataRole.UserRole, issue.line_index)
            results.addItem(item)
        results.setCurrentRow(0)
        layout.addWidget(results)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        jump = QPushButton("Go to Line")
        buttons.addButton(jump, QDialogButtonBox.ButtonRole.ActionRole)

        def go_to_line():
            item = results.currentItem()
            if item is not None:
                self.tabs.setCurrentIndex(0)
                self._select_line_index(item.data(Qt.ItemDataRole.UserRole))
                dialog.accept()

        jump.clicked.connect(go_to_line)
        results.itemDoubleClicked.connect(lambda _item: go_to_line())
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(680, 400)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
