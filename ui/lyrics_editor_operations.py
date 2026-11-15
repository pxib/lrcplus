"""Selection-based editing and previews for split, merge and bulk actions."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QListWidget, QMenu, QMessageBox, QPushButton, QSpinBox, QTextEdit,
    QVBoxLayout, QWidget,
)

from lyrics.editor_operations import merge_lyric_lines, set_line_format, split_lyric_line, visible_text_position
from lyrics.karaoke_timing import next_lyric_start
from lyrics.timing import shift_timing
from lyrics.writer import format_timestamp


class LyricsEditorOperationsMixin:
    def _record_line_text_change(self, position, removed, added):
        raw = self.line_text.toPlainText()
        previous = getattr(self, "_line_markup", "")
        self._line_markup = raw
        self._line_text_change = None if self.loading else (
            visible_text_position(previous, position),
            visible_text_position(previous, position + removed),
            visible_text_position(raw, position),
            visible_text_position(raw, position + added),
        )

    def _update_text_timing_notice(self, line):
        changed = [str(row + 1) for row, segment in enumerate(line.segments)
                   if segment.needs_timing_review] if line is not None else []
        for name in ("text_timing_notice", "karaoke_text_timing_notice"):
            notice = getattr(self, name, None)
            if notice is not None:
                notice.setText("Timing kept. Review changed chunks: " + ", ".join(changed) + "."
                               if changed else "")
                notice.setVisible(bool(changed))

    def _selected_line_indices(self):
        if not hasattr(self, "line_list"):
            return []
        return sorted({item.data(Qt.ItemDataRole.UserRole) for item in self.line_list.selectedItems()
                       if isinstance(item.data(Qt.ItemDataRole.UserRole), int)
                       and 0 <= item.data(Qt.ItemDataRole.UserRole) < len(self.lines)})

    def _set_line_selection(self, indices, current=None):
        indices = {index for index in indices if 0 <= index < len(self.lines)}
        self.line_list.clearSelection()
        for row, index in enumerate(self._view_line_indices):
            self.line_list.item(row).setSelected(index in indices)
        if current is None or current not in indices:
            current = min(indices) if indices else None
        if current is not None:
            self._select_line_index(current, preserve_selection=True)
        self._update_line_selection_controls()

    def _build_line_selection_controls(self, layout):
        self.line_selection_label = QLabel()
        self.line_selection_label.setWordWrap(True)
        layout.addWidget(self.line_selection_label)
        self.bulk_line_controls = QWidget()
        row = QHBoxLayout(self.bulk_line_controls)
        row.setContentsMargins(0, 0, 0, 0)
        self.bulk_actions_button = QPushButton("Selected Lines…")
        self.bulk_actions_button.setAccessibleName("Bulk actions for selected lyric lines")
        menu = QMenu(self.bulk_actions_button)
        for title, handler in (
            ("Shift Timing…", self.show_selected_timing_shift_dialog),
            ("Format…", self.show_bulk_format_dialog),
            ("Move Up", lambda: self.move_line(-1)),
            ("Move Down", lambda: self.move_line(1)),
            ("Merge…", self.merge_selected_lines),
            ("Delete…", self.delete_line),
        ):
            menu.addAction(title, handler)
        self.bulk_actions_button.setMenu(menu)
        row.addWidget(self.bulk_actions_button)
        clear = QPushButton("Keep Current")
        clear.setToolTip("Reduce the selection to the lyric currently being edited.")
        clear.clicked.connect(lambda: self._set_line_selection(
            [self._current_line_index()] if self._current_line_index() is not None else []))
        row.addWidget(clear)
        layout.addWidget(self.bulk_line_controls)
        self.bulk_line_controls.hide()

    def _update_line_selection_controls(self):
        if not hasattr(self, "line_selection_label"):
            return
        indices = self._selected_line_indices()
        count = len(indices)
        current = self._current_line_index()
        if count > 1:
            self.line_selection_label.setText(
                f"{count} lines selected. Editing source line {current + 1}."
                if current is not None else f"{count} lines selected."
            )
        else:
            self.line_selection_label.setText("Shift-click a range; Ctrl-click individual lines.")
        on_lyrics = self.tabs.currentWidget() is self.lyrics_page
        on_karaoke = self.karaoke_enabled and self.tabs.currentWidget() is self.karaoke_scroll
        self.bulk_line_controls.setVisible(count > 1 and (on_lyrics or on_karaoke))
        if hasattr(self, "split_line_button"):
            self.split_line_button.setEnabled(current is not None and count <= 1)
            self.delete_line_button.setText("Delete Selected…" if count > 1 else "Delete Line")
            self.merge_lines_button.setText("Merge Selected…" if count > 1 else "Merge with Next…")
            self.merge_lines_button.setEnabled(bool(indices) and (
                count > 1 or (current is not None and current + 1 < len(self.lines))))

    def _selection_preview_dialog(self, title, indices, description):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        hint = QLabel(description)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        preview = QListWidget()
        preview.setAccessibleName("Affected lyric lines in source order")
        for index in indices:
            line = self.lines[index]
            preview.addItem(f"{index + 1}. [{format_timestamp(line.start)}] {line.text}")
        preview.setMaximumHeight(180)
        layout.addWidget(preview)
        dialog.resize(520, 330)
        return dialog, layout

    @staticmethod
    def _operation_dialog_buttons(dialog, layout, apply, label="Apply"):
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        button.setText(label)
        button.clicked.connect(apply)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return button

    @staticmethod
    def _exec_operation_dialog(dialog):
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _finish_line_operation(self, selected, current=None):
        self._reading_generation_epoch += 1
        self.karaoke_dirty = any(line.has_karaoke for line in self.lines)
        self._populate_lines(preserve_line_index=current, selected_line_indices=selected)
        if self.lines and self.line_list.currentRow() >= 0:
            self.load_selected_line(self.line_list.currentRow())
        else:
            self.load_selected_line(-1)
        self._mark_document_dirty()

    def _apply_selected_timing_shift(self, delta_ms, indices):
        if not delta_ms:
            return
        # Validate every selected line before making one atomic document edit.
        changed = shift_timing([self.lines[index] for index in indices], delta_ms)
        current = self._current_line_index()
        self._push_undo_state()
        for index, replacement in zip(indices, changed):
            line = self.lines[index]
            line.start, line.end, line.segments = replacement.start, replacement.end, replacement.segments
        self._finish_line_operation(indices, current)

    def show_selected_timing_shift_dialog(self):
        if not self._commit_pending_source():
            return
        indices = self._selected_line_indices()
        if not indices:
            return
        dialog, layout = self._selection_preview_dialog(
            "Shift Selected Lyrics", indices,
            f"Shift these {len(indices)} selected lines, including word timing. Positive values move them later.",
        )
        form = QFormLayout()
        delta = QSpinBox()
        delta.setRange(-3_600_000, 3_600_000)
        delta.setSingleStep(50)
        delta.setSuffix(" ms")
        form.addRow("Shift by:", delta)
        layout.addLayout(form)
        summary = QLabel()
        summary.setWordWrap(True)
        layout.addWidget(summary)

        def apply():
            try:
                self._apply_selected_timing_shift(delta.value(), indices)
            except ValueError as error:
                summary.setText(str(error))
                return
            dialog.accept()

        apply_button = self._operation_dialog_buttons(dialog, layout, apply)

        def update():
            try:
                shifted = shift_timing([self.lines[index] for index in indices], delta.value())
                summary.setText(f"First selected line: {format_timestamp(shifted[0].start)}. Undo restores all selected lines.")
                apply_button.setEnabled(bool(delta.value()))
            except ValueError as error:
                summary.setText(str(error))
                apply_button.setEnabled(False)

        delta.valueChanged.connect(update)
        update()
        self._exec_operation_dialog(dialog)

    def show_bulk_format_dialog(self):
        if not self._commit_pending_source():
            return
        indices = self._selected_line_indices()
        if not indices:
            return
        dialog, layout = self._selection_preview_dialog(
            "Format Selected Lyrics", indices,
            f"Apply or remove formatting on these {len(indices)} lines. Text, timing and readings are preserved.",
        )
        mode = QComboBox()
        mode.addItem("Apply formatting", True)
        mode.addItem("Remove formatting", False)
        layout.addWidget(mode)
        choices = {}
        for label, key in (("Bold", "bold"), ("Italic", "italic"), ("Underline", "underline"),
                           ("Strikethrough", "strike"), ("Quote", "quote")):
            choices[key] = QCheckBox(label)
            layout.addWidget(choices[key])

        def apply():
            styles = [key for key, field in choices.items() if field.isChecked()]
            self._push_undo_state()
            for index in indices:
                set_line_format(self.lines[index], styles, mode.currentData())
            self._finish_line_operation(indices, self._current_line_index())
            dialog.accept()

        button = self._operation_dialog_buttons(dialog, layout, apply)
        button.setEnabled(False)
        for field in choices.values():
            field.toggled.connect(lambda: button.setEnabled(any(field.isChecked() for field in choices.values())))
        self._exec_operation_dialog(dialog)

    def _delete_selected_lines(self, indices):
        self._push_undo_state()
        removed = set(indices)
        self.lines = [line for index, line in enumerate(self.lines) if index not in removed]
        current = min(indices[0], len(self.lines) - 1) if self.lines else None
        self._finish_line_operation([current] if current is not None else [], current)

    def _confirm_bulk_delete(self, indices):
        dialog, layout = self._selection_preview_dialog(
            "Delete Selected Lyrics", indices,
            f"Delete these {len(indices)} lines? Undo restores their text, timing and readings.",
        )

        def apply():
            self._delete_selected_lines(indices)
            dialog.accept()

        self._operation_dialog_buttons(dialog, layout, apply, "Delete Lines")
        self._exec_operation_dialog(dialog)

    def split_current_line(self):
        if not self._commit_pending_source():
            return
        index, line = self._current_line()
        if line is None or len(self._selected_line_indices()) > 1:
            return
        raw = self.line_text.toPlainText()
        qt_position = self.line_text.textCursor().position()
        position = visible_text_position(raw, qt_position)
        fallback = next_lyric_start(self.lines, line)
        if fallback is None:
            media = getattr(self.player, "media_player", None)
            if media is not None and media.duration() > 0:
                fallback = self._audio_to_lyric_seconds(media.duration())
        try:
            left, right = split_lyric_line(line, position, fallback_end=fallback)
        except ValueError as error:
            QMessageBox.information(self, "Split Lyric Line", str(error))
            return
        dialog, layout = self._selection_preview_dialog(
            "Split Lyric Line", [index],
            "Split at the text cursor. Existing chunk times and readings are kept; a chunk cut in two uses estimated timing.",
        )
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(f"[{format_timestamp(left.start)}] {left.text}\n\n[{format_timestamp(right.start)}] {right.text}")
        layout.addWidget(preview)

        def apply():
            self._push_undo_state()
            checked = id(line) in self._share_checked_line_ids
            self.lines[index:index + 1] = [left, right]
            if checked:
                self._share_checked_line_ids.update((id(left), id(right)))
            self._finish_line_operation([index + 1], index + 1)
            dialog.accept()

        self._operation_dialog_buttons(dialog, layout, apply, "Split Line")
        self._exec_operation_dialog(dialog)

    def merge_selected_lines(self):
        if not self._commit_pending_source():
            return
        indices = self._selected_line_indices()
        if len(indices) == 1 and indices[0] + 1 < len(self.lines):
            indices.append(indices[0] + 1)
        if len(indices) < 2:
            return
        if indices != list(range(indices[0], indices[-1] + 1)):
            QMessageBox.information(self, "Merge Lyric Lines", "Select adjacent lines in source order to merge.")
            return
        originals = [self.lines[index] for index in indices]
        dialog, layout = self._selection_preview_dialog(
            "Merge Lyric Lines", indices,
            "Merge in source order and keep each word's timing and readings. The merged line uses the first line's quote setting.",
        )
        separator = QComboBox()
        for title, value in (("Space", " "), ("Newline", "\n"), ("No separator", "")):
            separator.addItem(title, value)
        if any(line.translation_lines for line in originals):
            separator.setCurrentIndex(1)
        form = QFormLayout()
        form.addRow("Join with:", separator)
        layout.addLayout(form)
        preview = QTextEdit()
        preview.setReadOnly(True)
        layout.addWidget(preview)

        def update():
            preview.setPlainText(separator.currentData().join(line.text for line in originals))

        separator.currentIndexChanged.connect(update)
        update()

        def apply():
            try:
                merged = merge_lyric_lines(originals, separator.currentData())
            except ValueError as error:
                QMessageBox.warning(dialog, "Merge Lyric Lines", str(error))
                return
            self._push_undo_state()
            checked = any(id(line) in self._share_checked_line_ids for line in originals)
            self.lines[indices[0]:indices[-1] + 1] = [merged]
            if checked:
                self._share_checked_line_ids.add(id(merged))
            self._finish_line_operation([indices[0]], indices[0])
            dialog.accept()

        self._operation_dialog_buttons(dialog, layout, apply, "Merge Lines")
        self._exec_operation_dialog(dialog)
