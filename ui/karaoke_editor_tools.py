"""Precise boundary nudging and bounded audio preview for the Karaoke tab."""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton

from lyrics.karaoke_timing import next_lyric_start, segment_end
from lyrics.writer import format_timestamp


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
        self.preview_karaoke_segment_button.setToolTip("Play from the selected segment's start to its end, then pause.")
        self.preview_karaoke_segment_button.clicked.connect(self.preview_karaoke_segment)
        row.addWidget(self.preview_karaoke_segment_button)
        layout.addLayout(row)

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
        value_ms = round(boundary * 1000 + direction * self.karaoke_nudge_step.value())
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
            fallback = media.duration() / 1000.0
        end = segment_end(line, row, fallback)
        start = line.segments[row].start
        if end is None or end <= start:
            self.karaoke_hint.setText("Set an End timestamp after this segment's Start to preview it.")
            return
        self.play_waveform_segment(round(start * 1000), round(end * 1000))

    def _update_karaoke_timing_cells(self, updates):
        blocker = QSignalBlocker(self.segment_table)
        try:
            for row, column, value in updates:
                item = self.segment_table.item(row, column)
                if item is not None:
                    item.setText(format_timestamp(value) if value is not None else "")
        finally:
            del blocker
