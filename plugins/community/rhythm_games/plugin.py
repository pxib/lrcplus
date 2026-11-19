"""A circular-target rhythm game using the timing of the current song's lyrics."""

from array import array
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from itertools import pairwise
from math import cos, exp, sin, tau
from pathlib import Path
from time import monotonic
import wave

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)


# Rounded timing windows for osu!standard at Overall Difficulty 5.
GREAT_WINDOW_MS = 50
OK_WINDOW_MS = 100
HIT_WINDOW_MS = 150
HIT_ANIMATION_SECONDS = 0.22
MISS_ANIMATION_SECONDS = 0.36
LEAD_IN_MS = 1000
REPEAT_POSITION_BELOW_MS = 200
MAX_JUMP_PX = 180
HIT_SOUND_PATH = Path(__file__).resolve().parents[3] / "assets" / "rhythm" / "hit.wav"
HIT_SOUND_GAIN = 0.5
HOW_TO_PLAY = (
    "Click a circle or aim at it and press Z, X, or Space as its outer ring closes. "
    "A stack shows how many taps are coming at that spot."
)


@dataclass(frozen=True)
class Note:
    time_ms: int
    text: str
    reading: str = ""


@dataclass(frozen=True)
class Passage:
    label: str
    notes: tuple[Note, ...]
    full_song: bool = False


@dataclass(frozen=True)
class NoteFeedback:
    index: int
    started: float
    hit: bool
    offset: QPointF

    def remaining(self, now):
        duration = HIT_ANIMATION_SECONDS if self.hit else MISS_ANIMATION_SECONDS
        return max(0.0, 1.0 - (now - self.started) / duration)


DEMO = Passage("Demo", tuple(
    Note(1000 + index * 500, word)
    for index, word in enumerate("Tap the beat then try your own song".split())
))


def lyric_passages(lines, file_offset_ms, duration_ms):
    """Use authored karaoke chunks and combine chunks that start together."""
    for line in lines:
        if not line.has_karaoke:
            continue
        chunks = {}
        for segment in line.segments:
            text = segment.text.strip()
            if segment.visual_line in line.translation_lines or not any(c.isalnum() for c in text):
                continue
            time_ms = round(segment.start * 1000 + file_offset_ms)
            if time_ms < 0 or duration_ms and time_ms >= duration_ms:
                continue
            reading = segment.ruby or " ".join(span[2] for span in segment.ruby_spans)
            chunks.setdefault(time_ms, []).append((text, reading))
        notes = tuple(
            Note(time_ms, " ".join(text for text, _ in words),
                 " ".join(reading for _, reading in words if reading))
            for time_ms, words in sorted(chunks.items())
        )
        if notes:
            start = notes[0].time_ms // 1000
            text = " / ".join(row for index, row in enumerate(line.text.splitlines())
                              if index not in line.translation_lines)
            yield Passage(f"{start // 60}:{start % 60:02d}  {text}", notes)


def full_song_passage(passages):
    """Build one chronological chart, with one target at each timestamp."""
    by_time = {}
    for passage in passages:
        for note in passage.notes:
            by_time.setdefault(note.time_ms, {})[note] = None
    notes = tuple(
        Note(time_ms, " / ".join(note.text for note in simultaneous),
             " / ".join(note.reading for note in simultaneous if note.reading))
        for time_ms, simultaneous in sorted(by_time.items())
    )
    return Passage("Full song", notes, full_song=True)


def generate_circle_centers(notes, field, min_spacing):
    """Give fast notes short jumps and keep simultaneous circles apart."""
    if not notes:
        return []
    directions = tuple(QPointF(cos(step * tau / 8), sin(step * tau / 8)) for step in range(8))
    centers = [field.center()]
    recent = deque([(notes[0].time_ms, centers[0])])
    spacing_squared = (min_spacing - 0.01) ** 2
    for index, (previous, note) in enumerate(pairwise(notes), 1):
        while recent and note.time_ms - recent[0][0] > LEAD_IN_MS + HIT_WINDOW_MS:
            recent.popleft()
        center = centers[-1]
        gap_ms = note.time_ms - previous.time_ms
        if gap_ms >= REPEAT_POSITION_BELOW_MS:
            distance = min(MAX_JUMP_PX, max(min_spacing, gap_ms * 0.30))
            for step in range(len(directions)):
                direction = directions[(index * 3 + step) % len(directions)]
                candidate = center + direction * distance
                if field.contains(candidate) and all(
                    (candidate.x() - other.x()) ** 2 + (candidate.y() - other.y()) ** 2
                    >= spacing_squared
                    for _, other in recent
                ):
                    center = candidate
                    break
        if recent and center == recent[-1][1]:
            recent.pop()
        recent.append((note.time_ms, center))
        centers.append(center)
    return centers


@dataclass(frozen=True)
class TapResult:
    message: str
    missed: tuple[int, ...]
    hit: bool = False


class Round:
    def __init__(self, notes):
        self.notes = notes
        self.index = self.hits = self.combo = self.best_combo = 0
        self.failed = set()

    def _skip_failed(self):
        while self.index in self.failed:
            self.index += 1

    def _miss(self, indices):
        missed = tuple(index for index in indices if index not in self.failed)
        if missed:
            self.failed.update(missed)
            self.combo = 0
            self._skip_failed()
        return missed

    def expire(self, position_ms):
        end = self.index
        while end < len(self.notes) and position_ms > self.notes[end].time_ms + HIT_WINDOW_MS:
            end += 1
        return self._miss(range(self.index, end))

    def tap(self, position_ms, target_index):
        missed = self.expire(position_ms)
        if target_index < self.index or target_index in self.failed:
            return TapResult("Too late" if target_index in missed else "Miss", missed)
        delta = position_ms - self.notes[target_index].time_ms
        error = abs(delta)
        if error > HIT_WINDOW_MS:
            message = "Too early" if delta < 0 else "Too late"
            return TapResult(message, missed + self._miss((target_index,)))
        missed += self._miss(range(self.index, target_index))
        self.index = target_index + 1
        self._skip_failed()
        self.hits += 1
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        if error <= GREAT_WINDOW_MS:
            return TapResult("Great", missed, hit=True)
        if error <= OK_WINDOW_MS:
            return TapResult("OK", missed, hit=True)
        return TapResult("Meh", missed, hit=True)


def demo_wave():
    """Generate an original click track for the demo."""
    sample_rate = 22050
    samples = array("h", [0]) * (sample_rate * 6)
    for index, note in enumerate(DEMO.notes):
        first = note.time_ms * sample_rate // 1000
        frequency = 880 if index % 4 == 0 else 550
        for frame in range(sample_rate // 12):
            time = frame / sample_rate
            samples[first + frame] = round(9000 * sin(tau * frequency * time) * exp(-55 * time))
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, sample_rate, 0, "NONE", ""))
        audio.writeframes(samples.tobytes())
    return output.getvalue()


class RhythmBoard(QWidget):
    tapped = Signal(QPointF)
    target_radius = 35
    ring_travel = 32

    def __init__(self, parent):
        super().__init__(parent)
        self.setMinimumSize(520, 270)
        self.setAccessibleName("Rhythm playfield")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.notes = ()
        self._centers = []
        self.first = 0
        self.failed = ()
        self.position_ms = 0
        self._feedback = []
        self._feedback_timer = QTimer(self, interval=16, timerType=Qt.TimerType.PreciseTimer)
        self._feedback_timer.timeout.connect(self._advance_feedback)

    def set_frame(self, notes, first, position_ms, failed=()):
        if notes is not self.notes:
            self.clear_feedback()
            self.notes = notes
            self._layout_targets()
        self.first, self.position_ms = first, position_ms
        self.failed = failed
        self.update()

    def resizeEvent(self, event):
        self._layout_targets()
        super().resizeEvent(event)

    def _layout_targets(self):
        padding = self.target_radius + self.ring_travel + 8
        field = QRectF(self.rect()).adjusted(padding, padding, -padding, -padding)
        self._centers = generate_circle_centers(self.notes, field, 2 * self.target_radius + 10)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.tapped.emit(event.position())
            event.accept()
        else:
            super().mousePressEvent(event)

    def target_center(self, index):
        return self._centers[index]

    def animate_hit(self, index):
        self._feedback.append(NoteFeedback(index, monotonic(), True, QPointF()))
        self._show_feedback()

    def animate_misses(self, indices):
        if not indices:
            return
        now = monotonic()
        for index in indices:
            # Keep the cue visible beside the next circle when both share a spot.
            center = self.target_center(index)
            offset = QPointF(33, 32) if self.target_at(center) is not None else QPointF()
            self._feedback.append(NoteFeedback(index, now, False, offset))
        self._show_feedback()

    def _show_feedback(self):
        if not self._feedback_timer.isActive():
            self._feedback_timer.start()
        self.update()

    def clear_feedback(self):
        if not self._feedback:
            return
        self._feedback.clear()
        self._feedback_timer.stop()
        self.update()

    def _advance_feedback(self):
        now = monotonic()
        self._feedback = [effect for effect in self._feedback if effect.remaining(now) > 0]
        if not self._feedback:
            self._feedback_timer.stop()
        self.update()

    def _visible_stacks(self):
        end_of_lookahead = self.position_ms + LEAD_IN_MS
        stack = []
        for index in range(self.first, len(self.notes)):
            if self.notes[index].time_ms > end_of_lookahead:
                break
            if index in self.failed:
                continue
            if stack and self.target_center(index) != self.target_center(stack[0]):
                yield stack
                stack = []
            stack.append(index)
        if stack:
            yield stack

    def target_at(self, point):
        for stack in self._visible_stacks():
            delta = point - self.target_center(stack[0])
            if delta.x() ** 2 + delta.y() ** 2 <= self.target_radius ** 2:
                return stack[0]
        return None

    def _draw_approach_ring(self, painter, index, center, color):
        remaining = max(0, self.notes[index].time_ms - self.position_ms)
        radius = self.target_radius + self.ring_travel * remaining / LEAD_IN_MS
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)

    def _draw_feedback(self, painter, accent):
        if not self._feedback:
            return
        now = monotonic()
        fill = QColor(accent)
        fill.setAlphaF(0.22)
        painter.save()
        hit_pen = QPen(accent, 2)
        miss_pen = QPen(QColor("#d05a60"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        for effect in self._feedback:
            remaining = effect.remaining(now)
            painter.setOpacity(remaining)
            center = self.target_center(effect.index) + effect.offset
            if effect.hit:
                radius = self.target_radius * (1.0 + 0.55 * (1.0 - remaining ** 2))
                painter.setPen(hit_pen)
                painter.setBrush(fill)
                painter.drawEllipse(center, radius, radius)
            else:
                painter.setPen(miss_pen)
                diagonal = QPointF(6, 6)
                opposite = QPointF(6, -6)
                painter.drawLine(center - diagonal, center + diagonal)
                painter.drawLine(center - opposite, center + opposite)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        accent = palette.highlight().color()
        painter.fillRect(self.rect(), palette.base())
        self._draw_feedback(painter, accent)
        metrics = QFontMetrics(painter.font())
        radius = self.target_radius
        for stack in reversed(tuple(self._visible_stacks())):
            index = stack[0]
            note = self.notes[index]
            center = self.target_center(index)
            active = index == self.first
            outline = accent if active else palette.mid().color()
            if len(stack) > 1:
                # Offset the preview outlines inward so stacks at an edge stay visible.
                offset = QPointF(10 if center.x() <= self.width() / 2 else -10,
                                 10 if center.y() < self.height() / 2 else -10)
                for depth, upcoming in reversed(list(enumerate(stack[1:3], 1))):
                    self._draw_approach_ring(painter, upcoming, center + offset * depth,
                                             palette.mid().color())
            self._draw_approach_ring(painter, index, center, outline)
            painter.setBrush(palette.highlight() if active else palette.alternateBase())
            painter.drawEllipse(center, radius, radius)

            label = QRectF(center.x() - 29, center.y() - 10, 58, 20)
            painter.setPen((palette.highlightedText() if active else palette.text()).color())
            if note.reading:
                painter.drawText(label.translated(0, -9), Qt.AlignmentFlag.AlignCenter,
                                 metrics.elidedText(note.reading, Qt.TextElideMode.ElideRight, 58))
                label.translate(0, 9)
            painter.drawText(label, Qt.AlignmentFlag.AlignCenter,
                             metrics.elidedText(note.text, Qt.TextElideMode.ElideRight, 58))
            if len(stack) > 1:
                count = f"×{len(stack)}"
                width = metrics.horizontalAdvance(count) + 10
                badge = QRectF(center.x() + 19 - width / 2, center.y() + 23, width, 20)
                painter.setPen(QPen(outline, 1))
                painter.setBrush(palette.base())
                painter.drawRoundedRect(badge, 6, 6)
                painter.setPen(palette.text().color())
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, count)


class RhythmDialog(QDialog):
    def __init__(self, host, api, passages):
        super().__init__(host)
        self.api = api
        self.host = host
        self.track_path = api.get_current_track()
        self.passages = (full_song_passage(passages), *passages, DEMO) if passages else (DEMO,)
        self.round = None
        self.pending_start = False
        self.source_ready = False
        self.using_demo = None
        self.anchor = None
        self.preroll_until = None
        self.resume_main = False
        self.main_paused = False
        self.setWindowTitle("Rhythm Games")
        self.setModal(True)
        self.resize(760, 430)

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.hit_sound = QSoundEffect(self)
        self.hit_sound.setSource(QUrl.fromLocalFile(str(HIT_SOUND_PATH)))
        self.demo_buffer = QBuffer(self)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.positionChanged.connect(self._record_position)
        self.player.errorOccurred.connect(self._playback_error)
        self.timer = QTimer(self, interval=16, timerType=Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.finished.connect(self._release_audio)

        layout = QVBoxLayout(self)
        options = QHBoxLayout()
        layout.addLayout(options)
        options.addWidget(QLabel("Chart"))
        self.passage = QComboBox()
        self.passage.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.passage.setMinimumContentsLength(24)
        for passage in self.passages:
            self.passage.addItem(passage.label)
        if passages:
            position = api.get_position()
            self.passage.setCurrentIndex(1 + min(
                range(len(passages)), key=lambda index: abs(passages[index].notes[0].time_ms - position),
            ))
        options.addWidget(self.passage, 1)
        options.addWidget(QLabel("Input offset"))
        self.calibration = QSpinBox()
        self.calibration.setRange(-500, 500)
        self.calibration.setSingleStep(10)
        self.calibration.setSuffix(" ms")
        saved = api.get_setting("input_offset_ms", 0)
        self.calibration.setValue(saved if type(saved) is int else 0)
        self.calibration.setToolTip("Increase this value if your taps consistently register late.")
        options.addWidget(self.calibration)

        self.board = RhythmBoard(self)
        self.board.tapped.connect(self._tap)
        layout.addWidget(self.board, 1)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Song progress")
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel(HOW_TO_PLAY)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(2 * self.status.fontMetrics().lineSpacing())
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.score = QLabel()
        buttons.addWidget(self.score, 1)
        self.start = QPushButton("Start")
        self.start.clicked.connect(self._start_round)
        buttons.addWidget(self.start)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        self.shortcuts = []
        for key in ("Z", "X", "Space"):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setAutoRepeat(False)
            shortcut.setEnabled(False)
            shortcut.activated.connect(self._tap_at_cursor)
            self.shortcuts.append(shortcut)
        self.passage.currentIndexChanged.connect(self._select_passage)
        self._show_passage()

    def _select_passage(self):
        self._show_passage()
        self.start.setText("Start")
        self.status.setText(HOW_TO_PLAY)

    def _selected_passage(self):
        return self.passages[self.passage.currentIndex()]

    def _show_passage(self):
        passage = self._selected_passage()
        self.board.set_frame(passage.notes, 0, passage.notes[0].time_ms - LEAD_IN_MS)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(passage.full_song)
        self.score.setText(f"{len(passage.notes)} targets")

    def _start_round(self):
        if self.pending_start or self.round is not None:
            return
        if not self.main_paused:
            self.resume_main = self.host.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self.api.pause()
            self.main_paused = True
        device = self.host.audio_output.device()
        volume = min(1.0, self.host.volume_slider.value() / 100)
        self.output.setDevice(device)
        self.output.setVolume(volume)
        self.hit_sound.stop()
        self.hit_sound.setAudioDevice(device)
        self.hit_sound.setVolume(volume * HIT_SOUND_GAIN)
        passage = self._selected_passage()
        self.round = Round(passage.notes)
        self.board.clear_feedback()
        self._show_passage()
        self.pending_start = True
        self._set_controls_running(True)
        self.status.setText("Loading audio…")
        self._show_live_score()
        use_demo = passage is DEMO
        if use_demo != self.using_demo or not self.source_ready:
            if self.using_demo is not None and not self.source_ready:
                self.player.setSource(QUrl())
            self.using_demo = use_demo
            self.source_ready = False
            if use_demo:
                if not self.demo_buffer.isOpen():
                    self.demo_buffer.setData(demo_wave())
                    self.demo_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                self.demo_buffer.seek(0)
                self.player.setSourceDevice(self.demo_buffer, QUrl("demo.wav"))
            else:
                self.player.setSource(QUrl.fromLocalFile(self.track_path))
        else:
            self._play_round()

    def _media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self.source_ready = True
            if self.pending_start:
                self._play_round()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia and self.round is not None:
            if self._selected_passage().full_song:
                self.progress.setValue(self.progress.maximum())
            self._finish_round()

    def _play_round(self):
        self.pending_start = False
        self.anchor = None
        delay = getattr(self.host, "_device_lyric_delay_ms", 0)
        self.offset_ms = delay + self.calibration.value()
        start_ms = self.round.notes[0].time_ms + self.offset_ms - LEAD_IN_MS
        if self._selected_passage().full_song:
            start_ms = min(0, start_ms)
            self.progress.setRange(0, max(1, self.player.duration()))
        self.player.setPosition(max(0, start_ms))
        self.board.set_frame(self.round.notes, 0, start_ms - self.offset_ms)
        self.status.setText("Get ready")
        self.timer.start()
        if start_ms < 0:
            self.preroll_until = monotonic() - start_ms / 1000
        else:
            self.player.play()

    def _record_position(self, position_ms):
        self.anchor = position_ms, monotonic()

    def _position(self):
        if self.preroll_until is not None:
            return min(0, (monotonic() - self.preroll_until) * 1000) - self.offset_ms
        position = self.player.position()
        if self.anchor is not None and self.anchor[0] == position:
            if self.player.mediaStatus() != QMediaPlayer.MediaStatus.StalledMedia:
                position += min(100, (monotonic() - self.anchor[1]) * 1000)
        return position - self.offset_ms

    def _tick(self):
        if self.preroll_until is not None:
            if monotonic() >= self.preroll_until:
                self.preroll_until = None
                self.player.play()
        elif self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self.round is None:
            return
        position = self._position()
        missed = self.round.expire(position)
        self.board.set_frame(self.round.notes, self.round.index, position, self.round.failed)
        if missed:
            self.board.animate_misses(missed)
            self.status.setText("Miss")
            self._show_live_score()
        if self._selected_passage().full_song:
            audio_position = self.player.position()
            self.progress.setValue(audio_position)
            duration = self.player.duration()
            # The media backend can reach the duration without sending EndOfMedia.
            if duration > 0 and audio_position >= duration:
                self._finish_round()
        elif position > self.round.notes[-1].time_ms + HIT_WINDOW_MS + 200:
            self._finish_round()

    def _tap_at_cursor(self):
        self._tap(QPointF(self.board.mapFromGlobal(QCursor.pos())))

    def _tap(self, point):
        if self.round is None or self.round.index == len(self.round.notes):
            return
        if self.preroll_until is None and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        target = self.board.target_at(point)
        if target is None:
            return
        position = self._position()
        result = self.round.tap(position, target)
        self.status.setText(result.message)
        self.board.set_frame(self.round.notes, self.round.index, position, self.round.failed)
        self.board.animate_misses(result.missed)
        if result.hit:
            self.board.animate_hit(target)
            if self.hit_sound.isLoaded():
                self.hit_sound.play()
        self._show_live_score()

    def _show_live_score(self):
        self.score.setText(f"{self.round.hits} / {len(self.round.notes)} hits   ·   Combo {self.round.combo}")

    def _finish_round(self):
        result = self.round
        self.round = None
        self.preroll_until = None
        self.timer.stop()
        self.player.pause()
        self.board.set_frame(result.notes, len(result.notes), self.board.position_ms)
        self.board.animate_misses(result.expire(float("inf")))
        self.score.setText(f"{result.hits} / {len(result.notes)} hits   ·   Best combo {result.best_combo}")
        self.status.setText("Round complete")
        self.start.setText("Retry")
        self._set_controls_running(False)

    def _playback_error(self, error, message):
        self.timer.stop()
        self.hit_sound.stop()
        self.board.clear_feedback()
        self.round = None
        self.pending_start = self.source_ready = False
        self.preroll_until = None
        self.status.setText(message or "Could not play this audio.")
        self._set_controls_running(False)

    def _set_controls_running(self, running):
        self.start.setEnabled(not running)
        self.passage.setEnabled(not running)
        self.calibration.setEnabled(not running)
        for shortcut in self.shortcuts:
            shortcut.setEnabled(running)
        if running:
            self.board.setCursor(Qt.CursorShape.CrossCursor)
            self.board.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.board.unsetCursor()

    def _release_audio(self, _result):
        self.timer.stop()
        self.hit_sound.stop()
        self.board.clear_feedback()
        self.round = None
        self.pending_start = False
        self.preroll_until = None
        self.player.stop()
        self.player.setSource(QUrl())
        if self.calibration.value() != self.api.get_setting("input_offset_ms", 0):
            self.api.set_setting("input_offset_ms", self.calibration.value())
        if self.resume_main and self.host.isVisible() and self.track_path == self.api.get_current_track():
            self.api.play()


class RhythmGamesPlugin:
    def on_load(self, context):
        self.api = context.api
        self.window = self.dialog = self.secret = None
        context.on("app_ready", self.on_app_ready)
        self.api.add_menu_action("Plugins", "Rhythm Games…", self.open_game)

    def on_app_ready(self, window):
        self.window = window
        self.secret = QShortcut(QKeySequence("Ctrl+Shift+G"), window)
        self.secret.setAutoRepeat(False)
        self.secret.activated.connect(self._open_from_lyrics)

    def _open_from_lyrics(self):
        if self.window.workspace_stack.currentWidget() is self.window.lyrics_page:
            self.open_game()

    def open_game(self):
        if self.dialog is not None or self.window is None:
            return
        path = self.api.get_current_track()
        passages = ()
        if path and Path(path).is_file():
            passages = tuple(lyric_passages(
                self.window.lyric_lines,
                getattr(self.window, "lyrics_offset_seconds", 0) * 1000,
                self.api.get_duration(),
            ))
        self.dialog = RhythmDialog(self.window, self.api, passages)
        self.dialog.finished.connect(self._close_game)
        self.dialog.open()

    def _close_game(self, _result):
        dialog, self.dialog = self.dialog, None
        dialog.deleteLater()

    def on_unload(self, context):
        if self.dialog is not None:
            self.dialog.reject()
        if self.secret is not None:
            self.secret.setEnabled(False)
            self.secret.deleteLater()
        self.window = None


PLUGIN_CLASS = RhythmGamesPlugin
