import time
from bisect import bisect_left

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from core.settings import get_karaoke_settings
from lyrics.widgets import FuriganaWidget
from ui.artwork_background import ArtworkBackgroundWidget

class PitchLane(QWidget):
    """Cached piano-roll section with a smoothly moving playhead."""

    WINDOW_SECONDS = 12.0
    NOTE_NAMES = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.notes = []
        self._note_starts = []
        self._max_note_duration = 0.0
        self._pitch_range = None
        self._page_pitch_ranges = {}
        self._lowest_note_pitch = None
        self._highest_note_pitch = None
        self._base_pixmap = None
        self._cache_key = None
        self._note_rects = []
        self._notes_revision = 0
        self.position_seconds = 0.0
        self.offset_seconds = 0.0
        self.live_pitch_points = []
        self._live_pitch_was_valid = False
        self.live_pitch_alpha = 0.0
        self.setMinimumHeight(180)

    def sizeHint(self):
        return QSize(640, min(360, self.maximumHeight()))

    def set_notes(self, notes):
        self.notes = sorted(
            (
                float(note["start"]),
                float(note["end"]),
                int(note["pitch"]),
            )
            for note in (notes or [])
        )
        self._note_starts = [note[0] for note in self.notes]
        self._max_note_duration = max(
            (end - start for start, end, _pitch in self.notes),
            default=0.0,
        )
        self._lowest_note_pitch = min((pitch for _start, _end, pitch in self.notes), default=None)
        self._highest_note_pitch = max((pitch for _start, _end, pitch in self.notes), default=None)
        self._pitch_range = (
            (min(note[2] for note in self.notes), max(note[2] for note in self.notes))
            if self.notes else None
        )
        self._page_pitch_ranges.clear()
        self._notes_revision += 1
        self._clear_pitch_trail()
        self._invalidate_chart()
        self.update()

    def set_position(self, position_ms):
        position = max(0.0, float(position_ms) / 1000.0)
        old_x = self._playhead_x()
        old_section = self._section_start()
        self.position_seconds = position
        if self._section_start() != old_section:
            self._clear_pitch_trail()
            self._invalidate_chart()
            self.update()
            return
        new_x = self._playhead_x()
        if old_x is not None and new_x is not None:
            x = max(0, int(min(old_x, new_x)) - 4)
            width = max(8, int(abs(new_x - old_x)) + 8)
            self.update(x, 0, width, self.height())
        else:
            self.update()

    def set_offset(self, offset_seconds):
        self.offset_seconds = float(offset_seconds)
        self._page_pitch_ranges.clear()
        self._invalidate_chart()
        self.update()

    def _pitch_range_for_section(self, section_start):
        """Return the note range for one 12-second page of the lane."""
        if section_start not in self._page_pitch_ranges:
            section_end = section_start + self.WINDOW_SECONDS
            pitches = [
                pitch for start, end, pitch in self.notes
                if end + self.offset_seconds >= section_start
                and start + self.offset_seconds <= section_end
            ]
            self._page_pitch_ranges[section_start] = (
                (min(pitches), max(pitches)) if pitches else self._pitch_range
            )
        return self._page_pitch_ranges[section_start]

    def set_live_pitch(self, midi_pitch=None, alpha=0.0, position=None):
        if midi_pitch is not None and position is not None:
            position = float(position)
            section = self._section_start()
            if not section <= position < section + self.WINDOW_SECONDS:
                return
            self.live_pitch_alpha = 1.0
            self.live_pitch_points.append((position, float(midi_pitch), time.monotonic()))
            self._live_pitch_was_valid = True
        else:
            # Silence, pause, and mic stop end input without hiding this page's trail.
            self._live_pitch_was_valid = False
        self.update()

    def _clear_pitch_trail(self):
        self.live_pitch_points.clear()
        self.live_pitch_alpha = 0.0
        self._live_pitch_was_valid = False

    def _section_start(self):
        return int(self.position_seconds // self.WINDOW_SECONDS) * self.WINDOW_SECONDS

    def _plot_geometry(self):
        return 42.0, max(43.0, float(self.width() - 8)), 24.0, max(25.0, float(self.height() - 26))

    def _playhead_x(self):
        if self.width() <= 0:
            return None
        left, right, _top, _bottom = self._plot_geometry()
        ratio = (self.position_seconds - self._section_start()) / self.WINDOW_SECONDS
        return left + max(0.0, min(1.0, ratio)) * (right - left)

    def _invalidate_chart(self):
        self._base_pixmap = None
        self._cache_key = None
        self._note_rects = []

    def resizeEvent(self, event):
        self._invalidate_chart()
        super().resizeEvent(event)

    def _render_chart(self):
        size = self.size()
        section_start = self._section_start()
        key = (size.width(), size.height(), section_start, self._notes_revision, self.offset_seconds)
        if self._base_pixmap is not None and self._cache_key == key:
            return

        pixmap = QPixmap(size)
        pixmap.fill(QColor(7, 18, 28, 105))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, top, bottom = self._plot_geometry()
        plot_width, plot_height = right - left, bottom - top
        if plot_width <= 0 or plot_height <= 0:
            painter.end()
            self._base_pixmap, self._cache_key = pixmap, key
            return

        page_pitch_range = self._pitch_range_for_section(section_start)
        if page_pitch_range is None:
            painter.setPen(QColor("#b8c8ce"))
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "Import a MIDI file to show its melody")
            painter.end()
            self._base_pixmap, self._cache_key = pixmap, key
            return

        low_pitch, high_pitch = page_pitch_range
        pitch_count = high_pitch - low_pitch + 1
        row_height = plot_height / max(1, pitch_count)
        start_time = section_start
        end_time = start_time + self.WINDOW_SECONDS

        def y_for(pitch):
            return bottom - (int(pitch) - low_pitch + 0.5) * row_height

        for pitch in range(low_pitch, high_pitch + 1):
            y = y_for(pitch)
            color = QColor("#385b69") if pitch % 12 == 0 else QColor("#244654")
            painter.setPen(QPen(color, 1))
            painter.drawLine(int(left), int(y), int(right), int(y))
            if pitch % 12 == 0:
                painter.setPen(QColor("#9ab3bc"))
                painter.drawText(2, int(y + 4), f"C{pitch // 12 - 1}")

        first_second = int(start_time)
        for second in range(first_second, int(end_time) + 1):
            x = left + (second - start_time) / self.WINDOW_SECONDS * plot_width
            painter.setPen(QPen(QColor("#294854"), 1, Qt.PenStyle.DotLine))
            painter.drawLine(int(x), int(top), int(x), int(bottom))
            if second % 2 == 0 and second < end_time:
                painter.setPen(QColor("#8ba7b0"))
                painter.drawText(int(x + 2), int(bottom + 18), f"{second}s")

        # Notes are sorted by start. Only inspect notes that can overlap this section.
        search_start = max(0.0, start_time - self.offset_seconds - self._max_note_duration)
        first_note = bisect_left(self._note_starts, search_start)
        for note_index in range(first_note, len(self.notes)):
            note_start_raw, note_end_raw, pitch = self.notes[note_index]
            note_start = note_start_raw + self.offset_seconds
            if note_start > end_time:
                break
            note_end = note_end_raw + self.offset_seconds
            if note_end < start_time:
                continue
            x1 = left + (note_start - start_time) / self.WINDOW_SECONDS * plot_width
            x2 = left + (note_end - start_time) / self.WINDOW_SECONDS * plot_width
            if x2 <= left or x1 >= right:
                continue
            # Notes can span a page boundary. Keep the visible fragment inside
            # this page's time grid instead of drawing it into the margin.
            x1 = max(left, x1)
            x2 = min(right, x2)
            y = y_for(pitch)
            if pitch == self._lowest_note_pitch:
                color = QColor("#a855f7")
                outline = QColor("#e3c4ff")
                played_color = QColor("#d8b4fe")
            elif pitch == self._highest_note_pitch:
                color = QColor("#dc3545")
                outline = QColor("#ffc2c8")
                played_color = QColor("#ff9aa5")
            else:
                color = QColor("#527887")
                outline = QColor("#96b3bd")
                played_color = QColor("#8be5ff")
            color.setAlpha(210)
            painter.setPen(QPen(outline, 1))
            painter.setBrush(color)
            note_width = max(3.0, x2 - x1)
            note_height = max(4.0, row_height * 0.76)
            note_top = y - row_height * 0.38
            painter.drawRoundedRect(x1, note_top, note_width, note_height, 3, 3)
            self._note_rects.append((note_start, note_end, pitch, x1, note_top, note_width, note_height, played_color))

            label = f"{self.NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"
            label_width = painter.fontMetrics().horizontalAdvance(label)
            if note_width >= label_width + 4:
                painter.setPen(QColor("#d8f6ff"))
                painter.drawText(
                    int(x1), int(note_top - painter.fontMetrics().height() - 2),
                    int(note_width), painter.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                    label,
                )

        painter.end()
        self._base_pixmap, self._cache_key = pixmap, key

    def paintEvent(self, _event):
        if self._cache_key is None:
            self._render_chart()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._base_pixmap)
        _left, _right, top, bottom = self._plot_geometry()
        playhead_x = self._playhead_x()
        if playhead_x is not None:
            # Wipe a bright fill through each note as playback crosses it.
            # The cached chart keeps note outlines and labels stable; only
            # these small clipped overlays need repainting on each tick.
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            position = self.position_seconds
            for note_start, note_end, _pitch, x, y, width, height, played_color in self._note_rects:
                if position <= note_start:
                    continue
                progress = max(0.0, min(1.0, (position - note_start) / max(1e-9, note_end - note_start)))
                if progress <= 0.0:
                    continue
                painter.save()
                painter.setClipRect(x, y, width * progress, height)
                painter.setPen(QPen(QColor("#e8fbff"), 1))
                painter.setBrush(played_color)
                painter.drawRoundedRect(x, y, width, height, 3, 3)
                painter.restore()
            painter.restore()
            painter.setPen(QPen(QColor("#ffcf42"), 3))
            painter.drawLine(int(playhead_x), int(top), int(playhead_x), int(bottom))
        if (len(self.live_pitch_points) > 1 and self.live_pitch_alpha > 0
                and self._pitch_range is not None):
            pitch_range = self._pitch_range_for_section(self._section_start())
            if pitch_range is None:
                painter.end()
                return
            low_pitch, high_pitch = pitch_range
            left, right, top, bottom = self._plot_geometry()
            section_start = self._section_start()
            plot_width = right - left
            pitch_height = bottom - top
            path = QPainterPath()
            started = False
            previous_detected_at = None
            previous_position = None
            for position, pitch, detected_at in self.live_pitch_points:
                if (previous_detected_at is not None
                        and (detected_at - previous_detected_at > 0.14
                             or position <= previous_position)):
                    started = False
                previous_detected_at = detected_at
                previous_position = position
                if not (section_start <= position <= section_start + self.WINDOW_SECONDS
                        and low_pitch <= pitch <= high_pitch):
                    started = False
                    continue
                x = left + (position - section_start) / self.WINDOW_SECONDS * plot_width
                row_height = pitch_height / max(1, high_pitch - low_pitch + 1)
                y = bottom - (pitch - low_pitch + 0.5) * row_height
                if started:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
                    started = True
            glow = QColor("#ffffff")
            glow.setAlpha(round(55 * self.live_pitch_alpha))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(glow, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            streak = QColor("#ffffff")
            streak.setAlpha(round(245 * self.live_pitch_alpha))
            painter.setPen(QPen(streak, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
        painter.end()


class KaraokeScreen(QDialog):
    """Full-screen lyric stage with a compact, independent melody toolbar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Karaoke Screen")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(1280, 800)
        self.setMinimumSize(720, 540)
        self.setStyleSheet("""
            QDialog { background: #10171d; color: #edf5f3; }
            QLabel { background: transparent; border: none; color: #edf5f3; }
            QWidget#controls, QWidget#melody {
                background: rgba(12, 24, 30, 180);
                border: 1px solid rgba(173, 207, 210, 30); border-radius: 12px;
            }
            QPushButton, QComboBox, QDoubleSpinBox {
                background: rgba(40, 60, 67, 190); color: #e6f1ef;
                border: 1px solid rgba(173, 207, 210, 55);
                border-radius: 7px; padding: 7px 10px; font-size: 12px;
                min-height: 20px;
            }
            QPushButton:hover, QComboBox:hover, QDoubleSpinBox:hover {
                background: #304b52; border-color: #75cbbb;
            }
            QPushButton:pressed { background: #203c42; }
            QPushButton:focus, QComboBox:focus, QDoubleSpinBox:focus {
                border-color: #91e5cf;
            }
            QPushButton#import { background: #91e5cf; color: #122e2a;
                border-color: #91e5cf; font-weight: 600; }
            QPushButton#import:hover { background: #b0f0df; }
            QComboBox QAbstractItemView { background: #1b2d34; color: #edf5f3;
                selection-background-color: #365d60; padding: 6px; }
            QComboBox:disabled, QDoubleSpinBox:disabled { color: #71858a; }
        """)

        self.background = ArtworkBackgroundWidget(parent=self)
        self.background.set_blur_radius(10)
        self.background.set_darkness(150)
        self.background.set_background_enabled(True)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(self.background)
        self.content_layout = QVBoxLayout(self.background)
        self.content_layout.setContentsMargins(32, 24, 32, 24)
        self.content_layout.setSpacing(16)
        layout = self.content_layout

        header = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(5)
        eyebrow = QLabel("K A R A O K E")
        eyebrow.setStyleSheet("color: #91e5cf; font-size: 11px; font-weight: 600;")
        self.track_label = QLabel("Your lyric stage")
        self.track_label.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.track_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.track_label.setMinimumWidth(0)
        title_column.addWidget(eyebrow)
        title_column.addWidget(self.track_label)
        header.addLayout(title_column, 1)
        self.close_label = QPushButton("Close  \u00b7  Esc")
        self.close_label.setToolTip("Close karaoke and return to the player (Esc)")
        self.close_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_label.clicked.connect(self.close)
        header.addWidget(self.close_label)
        layout.addLayout(header)

        controls = QWidget()
        controls.setObjectName("controls")
        self.controls_layout = QHBoxLayout(controls)
        self.controls_layout.setContentsMargins(14, 10, 14, 10)
        self.controls_layout.setSpacing(10)
        self.midi_button = QPushButton("Import MIDI\u2026")
        self.midi_button.setObjectName("import")
        self.midi_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.track_combo = QComboBox()
        self.track_combo.setPlaceholderText("Choose a melody track")
        self.track_combo.setMinimumWidth(140)
        self.track_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.track_combo.setToolTip("Melody track from the imported MIDI file")
        self.track_combo.setAccessibleName("Melody track")
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-30.0, 30.0)
        self.offset_spin.setDecimals(2)
        self.offset_spin.setSingleStep(0.05)
        self.offset_spin.setSuffix(" s")
        self.offset_spin.setPrefix("Sync ")
        self.offset_spin.setToolTip("Shift MIDI notes earlier or later to match the recording")
        self.offset_spin.setAccessibleName("MIDI sync offset in seconds")
        self.transpose_spin = QDoubleSpinBox()
        self.transpose_spin.setRange(-24, 24)
        self.transpose_spin.setDecimals(0)
        self.transpose_spin.setSingleStep(1)
        self.transpose_spin.setSuffix(" st")
        self.transpose_spin.setPrefix("Key ")
        self.transpose_spin.setToolTip("Transpose the MIDI melody by semitones")
        self.transpose_spin.setAccessibleName("MIDI transposition in semitones")
        for control in (self.midi_button, self.track_combo, self.offset_spin, self.transpose_spin):
            self.controls_layout.addWidget(control)
        layout.addWidget(controls)

        melody = QWidget()
        melody.setObjectName("melody")
        melody_layout = QVBoxLayout(melody)
        melody_layout.setContentsMargins(14, 12, 14, 10)
        melody_layout.setSpacing(8)
        melody_header = QHBoxLayout()
        caption = QLabel("MELODY GUIDE")
        caption.setStyleSheet("color: #9db6bb; font-size: 10px; font-weight: 600;")
        melody_header.addWidget(caption)
        melody_header.addStretch(1)
        self.square_wave_status = QLabel("Import a MIDI file to follow the melody")
        self.square_wave_status.setStyleSheet("color: #9db6bb; font-size: 11px;")
        self.square_wave_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.square_wave_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        melody_header.addWidget(self.square_wave_status, 1)
        self.mic_pitch_status = QLabel("Mic off")
        self.mic_pitch_status.setStyleSheet("color: #c3d7d8; font-size: 12px;")
        self.mic_pitch_status.setMinimumWidth(100)
        self.mic_pitch_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        melody_header.addWidget(self.mic_pitch_status)
        self.mic_button = QPushButton("Start mic")
        self.mic_button.setCheckable(True)
        self.mic_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_button.setToolTip("Show your voice as a white pitch trail over the melody")
        self.mic_button.setStyleSheet(
            "QPushButton:checked { background: #365d60; border-color: #91e5cf; }"
        )
        melody_header.addWidget(self.mic_button)
        melody_layout.addLayout(melody_header)
        self.pitch_lane = PitchLane(self)
        self.pitch_lane.setMinimumHeight(90)
        self.pitch_lane.setMaximumHeight(280)
        melody_layout.addWidget(self.pitch_lane)
        layout.addWidget(melody)

        # Keep the optional scoring interface available to callers.
        self.judgment_status = QLabel("Score 0  |  Combo 0  |  Ready")
        self.judgment_counter = QLabel("AMAZING 0  PERFECT 0  GOOD 0  OK 0  BAD 0  MISS 0")
        for widget in (self.judgment_status, self.judgment_counter):
            layout.addWidget(widget)
            widget.hide()

        layout.addStretch(1)
        self.current_lyric = FuriganaWidget()
        self.current_lyric.setMinimumHeight(145)
        self.current_lyric.set_alignment("left")
        self.current_lyric.set_font(size=56, ruby_size=28)
        self.current_lyric.set_colors("#ffffff", "#91e5cf")
        self.current_lyric.set_ruby_visible(True)
        self.current_lyric.set_reserve_ruby_space(True)
        self._add_lyric_outline(self.current_lyric)
        self.current_lyric.set_karaoke_sweep_style(
            get_karaoke_settings().get("sweep_style", "classic")
        )
        layout.addWidget(self.current_lyric, 2)

        self.next_lyric = FuriganaWidget()
        self.next_lyric.setMinimumHeight(110)
        self.next_lyric.set_alignment("right")
        self.next_lyric.set_font(size=44, ruby_size=23)
        self.next_lyric.set_colors("#b8cccf", "#91e5cf")
        self.next_lyric.set_ruby_visible(True)
        self.next_lyric.set_reserve_ruby_space(True)
        self._add_lyric_outline(self.next_lyric)
        layout.addWidget(self.next_lyric, 1)
        layout.addStretch(1)

        footer = QHBoxLayout()
        hint = QLabel("Sing along \u00b7 follow the highlighted words")
        hint.setStyleSheet("color: #9db6bb; font-size: 11px;")
        footer.addWidget(hint)
        footer.addStretch(1)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("color: #c3d7d8; font-size: 12px;")
        footer.addWidget(self.time_label)
        layout.addLayout(footer)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            "QProgressBar { background: rgba(173,207,210,40); border: none; border-radius: 2px; }"
            "QProgressBar::chunk { background: #91e5cf; border-radius: 2px; }"
        )
        self.progress.valueChanged.connect(self._update_time_label)
        layout.addWidget(self.progress)

    def _update_time_label(self, _value=None):
        def stamp(milliseconds):
            seconds = max(0, int(milliseconds) // 1000)
            return f"{seconds // 60}:{seconds % 60:02d}"
        self.time_label.setText(f"{stamp(self.progress.value())} / {stamp(self.progress.maximum())}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "next_lyric"):
            return
        compact = self.width() < 1000 or self.height() < 700
        margin = 18 if compact else 32
        self.content_layout.setContentsMargins(margin, 16 if compact else 24, margin, 16 if compact else 24)
        self.content_layout.setSpacing(8 if compact else 16)
        self.pitch_lane.setMinimumHeight(60 if self.height() < 600 else 90)
        self.pitch_lane.setMaximumHeight(max(150, min(360, (self.height() - 240) // 2)))
        sizes = (36, 19, 28, 15) if compact else (56, 28, 44, 23)
        if self.height() < 700:
            sizes = (30, 16, 24, 13)
        if getattr(self, "_lyric_sizes", None) != sizes:
            self._lyric_sizes = sizes
            self.current_lyric.set_font(size=sizes[0], ruby_size=sizes[1])
            self.next_lyric.set_font(size=sizes[2], ruby_size=sizes[3])
            self.current_lyric.setMinimumHeight(90 if self.height() < 700 else 105 if compact else 145)
            self.next_lyric.setMinimumHeight(72 if self.height() < 700 else 85 if compact else 110)

    @staticmethod
    def _add_lyric_outline(widget):
        outline = QGraphicsDropShadowEffect(widget)
        outline.setBlurRadius(8)
        outline.setOffset(0, 2)
        outline.setColor(QColor(0, 0, 0, 220))
        widget.setGraphicsEffect(outline)

    def set_background_pixmap(self, pixmap):
        if pixmap is not None and not pixmap.isNull():
            self.background.set_artwork_pixmap(pixmap)
        else:
            self.background.clear_artwork()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
