"""Visual authoring for the mora cues of one karaoke chunk."""

from pathlib import Path
import unicodedata

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QHeaderView, QLabel, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.fonts import content_font
from lyrics.waveform import WaveformWidget
from ui.lrc_editor_controls import KaraokeSeparationWidget, LyricLabel, WaveformControls


_MORA_JOINERS = "ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ\u3099\u309a"


def _default_splits(reading):
    return {
        index for index, char in enumerate(reading) if index
        and unicodedata.normalize("NFKC", char) not in _MORA_JOINERS
        and not unicodedata.category(char).startswith("M")
    }


class MoraTimingDialog(QDialog):
    def __init__(self, editor, segment, reading, end):
        super().__init__(editor)
        self.editor = editor
        self.reading = reading
        self.start_ms = round(segment.start * 1000)
        self.end_ms = round(end * 1000)
        self.setWindowTitle("Mora Timing")
        self.resize(660, 470)
        layout = QVBoxLayout(self)
        heading = LyricLabel()
        heading.setText(segment.text)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)

        self.separator = KaraokeSeparationWidget()
        self.separator.setAccessibleName("Mora divisions")
        offsets = {offset for offset, _ in segment.mora_cues if offset}
        self.separator.set_content(reading, offsets if segment.mora_cues else _default_splits(reading))
        self.visible_splits = self.separator.boundaries()
        self.explicit_first = bool(segment.mora_cues and segment.mora_cues[0][0] == 0)
        scroll = QScrollArea()
        scroll.setWidget(self.separator)
        scroll.setFixedHeight(self.separator.sizeHint().height() + 20)
        layout.addWidget(QLabel("Click between kana to split or join morae."))
        layout.addWidget(scroll)

        self.waveform = WaveformWidget(
            defer_hidden_load=True, show_timing_without_peaks=True,
            karaoke_boundary_labels=("Chunk start", "Chunk end"),
        )
        self.waveform.setAccessibleName("Mora timing waveform")
        layout.addWidget(self.waveform)
        controls = WaveformControls(self.waveform, karaoke=True)
        controls.fit_line.setText("Fit chunk")
        controls.fit_line.setToolTip("Fit the karaoke chunk (Home).")
        controls.hint.setText(
            "Select a mora below. Left-drag its start or right-drag its end. "
            "Hold Alt to move a shared boundary. Leave a gap to pause the sweep."
        )
        layout.addWidget(controls)

        self.table = QTableWidget(0, 3)
        self.table.setAccessibleName("Mora timing")
        self.table.setHorizontalHeaderLabels(["Mora", "Start (s)", "End (s)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        preview = QPushButton("Preview chunk")
        preview.clicked.connect(lambda: self._play(self.start_ms, self.end_ms))
        buttons.addButton(preview, QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)

        self.media = getattr(editor.player, "media_player", None)
        preview.setEnabled(self.media is not None)
        path = editor._current_song_path() or editor.attached_song_path
        if path and Path(path).is_file():
            self.waveform.load_audio(path)
        duration = self.media.duration() if self.media is not None else 0
        self.waveform.set_duration(max(duration, self._to_audio(self.end_ms), 1))
        if self.media is not None:
            self.waveform.set_position(self.media.position())
            position_changed = getattr(self.media, "positionChanged", None)
            if position_changed is not None:
                position_changed.connect(self.waveform.set_position)
            self.waveform.seekRequested.connect(editor.seek_preview)
            self.waveform.segmentPlayRequested.connect(editor.play_karaoke_waveform_segment)

        self.splits = sorted(offsets if segment.mora_cues else self.separator.boundaries())
        self.ranges = self._initial_ranges(segment.mora_cues)
        self._build_table()
        self.separator.boundariesChanged.connect(self._change_splits)
        self.table.currentCellChanged.connect(lambda *_: self._refresh_timing())
        self.waveform.karaokeTimingRequested.connect(self._move_waveform_boundary)
        self.waveform.karaokeTimingFinished.connect(self._refresh_timing)

    def _initial_ranges(self, cues):
        if cues:
            times = {}
            for offset, elapsed in cues:
                times.setdefault(offset, []).append(self.start_ms + round(elapsed * 1000))
            first = times.get(0, [self.start_ms])[0]
            starts = [first] + [times[offset][-1] for offset in self.splits]
            ends = [times[offset][0] for offset in self.splits] + [self.end_ms]
            return list(zip(starts, ends))
        count = len(self.splits) + 1
        span = self.end_ms - self.start_ms
        times = [self.start_ms + round(index * span / count) for index in range(count + 1)]
        return list(zip(times, times[1:]))

    def cues(self):
        cues = []
        if self.explicit_first or not self.splits or self.ranges[0][0] > self.start_ms:
            cues.append((0, (self.ranges[0][0] - self.start_ms) / 1000))
        for index, offset in enumerate(self.splits):
            preceding_end = self.ranges[index][1]
            following_start = self.ranges[index + 1][0]
            if preceding_end < following_start:
                cues.append((offset, (preceding_end - self.start_ms) / 1000))
            cues.append((offset, (following_start - self.start_ms) / 1000))
        return tuple(cues)

    def _to_audio(self, millis):
        return self.editor._lyric_to_audio_ms(millis / 1000)

    def _play(self, start, end):
        self.editor.play_karaoke_waveform_segment(self._to_audio(start), self._to_audio(end))

    def _build_table(self, selected=0):
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(self.ranges))
            offsets = [0, *self.splits, len(self.reading)]
            for row, (first, last) in enumerate(zip(offsets, offsets[1:])):
                text = self.reading[first:last]
                label = QTableWidgetItem(text)
                label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
                label.setFont(content_font(self.table.font(), text))
                self.table.setItem(row, 0, label)
                for column, handle in ((1, "start"), (2, "end")):
                    spin = QDoubleSpinBox()
                    spin.setAccessibleName(f"{text} {handle}")
                    spin.setDecimals(3)
                    spin.setSingleStep(0.010)
                    spin.setKeyboardTracking(False)
                    spin.setRange(0, max(self.end_ms, self.ranges[row][1], self.ranges[row][0]) / 1000)
                    spin.setReadOnly(handle == "end" and row == len(self.ranges) - 1)
                    if spin.isReadOnly():
                        spin.setToolTip("The last mora ends at the chunk end.")
                    spin.editingFinished.connect(
                        lambda row=row, handle=handle, spin=spin: self._move_boundary(row, handle, round(spin.value() * 1000))
                    )
                    self.table.setCellWidget(row, column, spin)
            self.table.setCurrentCell(selected, 0)
        self._refresh_timing()

    def _refresh_timing(self):
        ranges = []
        valid = True
        previous_end = self.start_ms
        for row, (start, end) in enumerate(self.ranges):
            valid &= previous_end <= start < end <= self.end_ms
            previous_end = end
            for column, value in ((1, start), (2, end)):
                spin = self.table.cellWidget(row, column)
                with QSignalBlocker(spin):
                    spin.setValue(value / 1000)
            ranges.append((self._to_audio(start) / 1000, self._to_audio(end) / 1000,
                           self.table.item(row, 0).text()))
        row = self.table.currentRow()
        self.waveform.set_karaoke_segments(
            ranges, row, self._to_audio(self.start_ms), self._to_audio(self.end_ms),
        )
        if row >= 0:
            start, end = self.ranges[row]
            self.waveform.set_karaoke_timing(self._to_audio(start), self._to_audio(end), row)
        self.apply_button.setEnabled(valid)
        self._set_status("" if valid else "Give each mora at least 1 ms within the chunk.")

    def _set_status(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _move_waveform_boundary(self, handle, audio_ms):
        row = self.table.currentRow()
        if row >= 0:
            value = round(self.editor._audio_to_lyric_seconds(audio_ms) * 1000)
            self._move_boundary(row, handle, value)

    def _move_boundary(self, row, handle, value):
        start, end = self.ranges[row]
        earlier = self.ranges[row - 1] if row else None
        later = self.ranges[row + 1] if row + 1 < len(self.ranges) else None
        if handle in ("start", "linked_start"):
            lower = earlier[1] if earlier else self.start_ms
            linked = handle == "linked_start" and earlier and earlier[1] == start
            if linked:
                lower = earlier[0] + 1
            if lower < end:
                start = min(max(lower, value), end - 1)
                if linked:
                    self.ranges[row - 1] = (earlier[0], start)
        elif later:
            upper = later[0]
            linked = handle == "linked_end" and later[0] == end
            if linked:
                upper = later[1] - 1
            if start < upper:
                end = max(min(upper, value), start + 1)
                if linked:
                    self.ranges[row + 1] = (end, later[1])
        self.ranges[row] = (start, end)
        self._refresh_timing()

    def _change_splits(self):
        splits = self.separator.boundaries()
        added = splits.difference(self.visible_splits)
        removed = self.visible_splits.difference(splits)
        if added:
            offset, = added
            index = sum(split < offset for split in self.splits)
            start, end = self.ranges[index]
            if end - start < 2:
                self.separator.set_content(self.reading, self.splits)
                self._set_status("This mora needs at least 2 ms to split it.")
                return
            middle = (start + end) // 2
            self.ranges[index:index + 1] = [(start, middle), (middle, end)]
            self.splits.insert(index, offset)
        elif removed:
            offset, = removed
            index = self.splits.index(offset)
            self.ranges[index:index + 2] = [(self.ranges[index][0], self.ranges[index + 1][1])]
            self.splits.pop(index)
        else:
            return
        self.visible_splits = splits
        self._build_table(index)

    def done(self, result):
        self.waveform._stop_worker()
        super().done(result)
