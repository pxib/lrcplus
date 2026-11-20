"""Precise boundary nudging and bounded audio preview for the Karaoke tab."""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton

from lyrics.karaoke_timing import next_lyric_start, segment_end
from lyrics.writer import format_mora_reading, format_timestamp
from ui.mora_timing_dialog import MoraTimingDialog


class KaraokeEditorToolsMixin:
    def _build_karaoke_timing_tools(self, layout):
        row = QHBoxLayout()
        row.addWidget(QLabel("Nudge Start/End:"))
        self.karaoke_nudge_step = QDoubleSpinBox()
        self.karaoke_nudge_step.setDecimals(0)
        self.karaoke_nudge_step.setRange(1, 1000)
        self.karaoke_nudge_step.setValue(10)
        self.karaoke_nudge_step.setSuffix(" ms")
        row.addWidget(self.karaoke_nudge_step)
        for label, direction in (("Earlier", -1), ("Later", 1)):
            button = QPushButton(label)
            button.setToolTip("Move the selected Start or End boundary by the chosen step.")
            button.clicked.connect(lambda _checked=False, direction=direction: self.nudge_karaoke_boundary(direction))
            row.addWidget(button)
        self.preview_karaoke_segment_button = QPushButton("Preview Segment")
        self.preview_karaoke_segment_button.setToolTip("Play the selected segment. Very short segments include extra audio context.")
        self.preview_karaoke_segment_button.clicked.connect(self.preview_karaoke_segment)
        row.addWidget(self.preview_karaoke_segment_button)
        self.edit_mora_timing_button = QPushButton("Mora Timing…")
        self.edit_mora_timing_button.setToolTip("Edit the selected chunk's furigana timing on a waveform.")
        self.edit_mora_timing_button.clicked.connect(self.edit_mora_timing)
        row.addWidget(self.edit_mora_timing_button)
        layout.addLayout(row)

    def edit_mora_timing(self):
        _, line = self._current_line()
        row = self.segment_table.currentRow()
        if line is None or not 0 <= row < len(line.segments) or self.segment_table.isRowHidden(row):
            self.karaoke_hint.setText("Select a karaoke chunk to edit its mora timing.")
            return
        segment = line.segments[row]
        reading = segment.ruby
        if segment.ruby_spans:
            if len(segment.ruby_spans) != 1 or segment.ruby_spans[0][:2] != (0, len(segment.text)):
                self.karaoke_hint.setText("Split the chunk so one ruby reading covers the entire chunk.")
                return
            reading = segment.ruby_spans[0][2]
        if not reading:
            start = sum(len(chunk.text) for chunk in line.segments[:row])
            end = start + len(segment.text)
            reading = next((token["reading"] for token in self._furigana_editor_tokens(line, include_generated=True)
                            if token.get("ruby_start", token["start"]) == start
                            and token.get("ruby_end", token["end"]) == end and token.get("reading")), None)
        if not reading:
            self.karaoke_hint.setText("Add a reading in the Ruby column first.")
            return
        if segment.instant:
            self.karaoke_hint.setText("Turn off Instant for this chunk to use a mora sweep.")
            return
        end = segment_end(line, row, next_lyric_start(self.lines, line))
        if end is None or round(end * 1000) <= round(segment.start * 1000):
            self.karaoke_hint.setText("Give this chunk an End timestamp after its Start.")
            return
        dialog = MoraTimingDialog(self, segment, reading, end)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                text = format_mora_reading(reading, segment.start, dialog.cues())
                self.segment_table.item(row, 3).setText(text)
        finally:
            dialog.deleteLater()

    def nudge_karaoke_boundary(self, direction):
        _, line = self._current_line()
        row, column = self.segment_table.currentRow(), self.segment_table.currentColumn()
        if line is None or not 0 <= row < len(line.segments):
            return
        if column not in (0, 1):
            self.karaoke_hint.setText("Select a Start or End cell to nudge its timing.")
            return
        segment = line.segments[row]
        boundary = segment.start if column == 0 else segment_end(line, row, next_lyric_start(self.lines, line))
        if boundary is None:
            self.karaoke_hint.setText("Set an End timestamp before nudging this boundary.")
            return
        value_ms = self._lyric_to_audio_ms(boundary) + round(direction * self.karaoke_nudge_step.value())
        self._waveform_drag_row = -1
        self._waveform_karaoke_timing_changed("start" if column == 0 else "end", value_ms)
        self._waveform_karaoke_timing_finished()

    def preview_karaoke_segment(self):
        _, line = self._current_line()
        row = self.segment_table.currentRow()
        media = getattr(self.player, "media_player", None)
        if line is None or not 0 <= row < len(line.segments) or media is None:
            self.karaoke_hint.setText("Load a song and select a segment to preview it.")
            return
        fallback = next_lyric_start(self.lines, line)
        if fallback is None and media.duration() > 0:
            fallback = self._audio_to_lyric_seconds(media.duration())
        end = segment_end(line, row, fallback)
        start = line.segments[row].start
        if end is None or end <= start:
            self.karaoke_hint.setText("Set an End timestamp after this segment's Start to preview it.")
            return
        self.play_karaoke_waveform_segment(
            max(0, self._lyric_to_audio_ms(start)),
            self._lyric_to_audio_ms(end),
        )

    def _update_karaoke_timing_cells(self, updates):
        blocker = QSignalBlocker(self.segment_table)
        try:
            for row, column, value in updates:
                item = self.segment_table.item(row, column)
                if item is not None:
                    item.setText(format_timestamp(value) if value is not None else "")
        finally:
            del blocker
