"""A circular-target rhythm game using the timing of the current song's lyrics."""

from array import array
from collections import deque
from dataclasses import dataclass, replace
from heapq import nsmallest
from io import BytesIO
from itertools import pairwise
from math import atan2, cos, exp, floor, hypot, pi, sin, sqrt, tau
from pathlib import Path
from time import monotonic
import wave

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)


# The default chart uses AR5, OD5 and CS4 on a 512×384 playfield.
OVERALL_DIFFICULTY = 5
CIRCLE_SIZE = 4
LEAD_IN_MS = 1200
FADE_IN_MS = 400
MAX_VISIBLE_TARGETS = 5
DENSE_PREVIEW_MS = 450
HIT_WINDOWS = ((300, 80 - 6 * OVERALL_DIFFICULTY),
               (100, 140 - 8 * OVERALL_DIFFICULTY),
               (50, 200 - 10 * OVERALL_DIFFICULTY))
# Stable rounds the input error before comparing it against each strict window.
HIT_WINDOW_MS = HIT_WINDOWS[-1][1] - 0.5
EARLY_MISS_MS = 400
MIN_SLIDER_MS = 425
MAX_SLIDER_MS = 1600
SLIDER_FOLLOW_SCALE = 2.1
HIT_ANIMATION_SECONDS = 0.22
MISS_ANIMATION_SECONDS = 0.36
FAST_CIRCLE_GAP_MS = 200
WIGGLE_GAP_MS = 105
REGULAR_JUMP_GAP_MS = 420
MAX_PATTERN_NOTES = 7
MIN_SPIRAL_NOTES = 10
MAX_SPIRAL_NOTES = 14
LOOKAHEAD_CHOICES = 3
MAX_JUMP_PX = 180
HIT_SOUND_PATH = Path(__file__).resolve().parents[3] / "assets" / "rhythm" / "hit.wav"
HIT_SOUND_GAIN = 0.5
HOW_TO_PLAY = (
    "Aim and tap Z, X, Space, or either mouse button as the ring closes. "
    "Hold to follow sliders or circle around the center of a spinner."
)


@dataclass(frozen=True)
class Note:
    time_ms: int
    text: str
    reading: str = ""
    end_ms: int | None = None
    spinner: bool = False
    spans: int = 1

    @property
    def finish_ms(self):
        return self.time_ms if self.end_ms is None else self.end_ms

    @property
    def slider(self):
        return self.end_ms is not None and not self.spinner

    def slider_progress(self, time_ms):
        spans = self.spans * (time_ms - self.time_ms) / (self.end_ms - self.time_ms)
        return 1 - abs(1 - min(self.spans, max(0, spans)) % 2)

    def slider_checkpoints(self):
        span_ms = (self.end_ms - self.time_ms) / self.spans
        checkpoints = []
        for span in range(self.spans):
            if span_ms >= 500:
                checkpoints.append((self.time_ms + span_ms * (span + 0.5), 10))
            checkpoints.append((self.time_ms + span_ms * (span + 1), 30))
        return tuple(checkpoints)


@dataclass(frozen=True)
class Passage:
    label: str
    notes: tuple[Note, ...]
    full_song: bool = False


@dataclass(frozen=True)
class NoteFeedback:
    index: int
    started: float
    value: int
    offset: QPointF

    def remaining(self, now):
        duration = HIT_ANIMATION_SECONDS if self.value else MISS_ANIMATION_SECONDS
        return max(0.0, 1.0 - (now - self.started) / duration)


DEMO = Passage("Demo", (
    Note(1000, "Tap"), Note(1125, "Tap again"), Note(1500, "Tap"),
    Note(2000, "Hold and follow the ball back", end_ms=3000, spans=2),
    Note(3500, "Tap"), Note(3750, "Tap"),
    Note(4250, "Hold and spin around the center", end_ms=6250, spinner=True),
    *(Note(time, "Follow the arc") for time in range(6750, 7251, 125)),
    *(Note(time, "Follow the triangle") for time in range(7750, 8251, 250)),
    *(Note(time, "Follow the square") for time in range(9000, 9751, 250)),
    *(Note(time, "Follow the wiggle") for time in range(10500, 11001, 100)),
    *(Note(time, "Tap back and forth") for time in range(11500, 12501, 250)),
    *(Note(time, "Follow the spiral") for time in range(13250, 14626, 125)),
))


def fit_sliders(notes):
    """Leave the next lyric's entire hit window free after a sustained lyric."""
    for index, note in enumerate(notes):
        if note.end_ms is not None:
            end_ms = min(note.end_ms, note.time_ms + MAX_SLIDER_MS)
            if index + 1 < len(notes):
                end_ms = min(end_ms, notes[index + 1].time_ms - HIT_WINDOWS[-1][1])
            note = replace(note, end_ms=end_ms if end_ms - note.time_ms >= MIN_SLIDER_MS else None)
        yield note


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
            end_ms = time_ms
            if segment.end is not None and not segment.instant:
                end_ms = round(segment.end * 1000 + file_offset_ms)
                if duration_ms:
                    end_ms = min(end_ms, duration_ms)
            chunks.setdefault(time_ms, []).append((text, reading, end_ms))
        notes = tuple(fit_sliders([
            Note(time_ms, " ".join(text for text, _, _ in words),
                 " ".join(reading for _, reading, _ in words if reading),
                 max(end_ms for _, _, end_ms in words))
            for time_ms, words in sorted(chunks.items())
        ]))
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
            simultaneous = by_time.setdefault(note.time_ms, {})
            key = note.text, note.reading
            simultaneous[key] = max(simultaneous.get(key, note.time_ms), note.finish_ms)
    notes = tuple(fit_sliders([
        Note(time_ms, " / ".join(text for text, _ in simultaneous),
             " / ".join(reading for _, reading in simultaneous if reading), max(simultaneous.values()))
        for time_ms, simultaneous in sorted(by_time.items())
    ]))
    return Passage("Full song", notes, full_song=True)


def circle_patterns(notes):
    """Read short, regular runs from the authored timestamps."""
    start = 0
    while start < len(notes):
        end = start + 1
        first_gap = float("inf")
        if notes[start].end_ms is None and end < len(notes):
            first_gap = notes[end].time_ms - notes[start].time_ms
            low, high = ((0, FAST_CIRCLE_GAP_MS) if first_gap < FAST_CIRCLE_GAP_MS
                         else (FAST_CIRCLE_GAP_MS, REGULAR_JUMP_GAP_MS))
            tolerance = max(30, first_gap * 0.25)
            while end < len(notes) and notes[end].end_ms is None:
                gap = notes[end].time_ms - notes[end - 1].time_ms
                if not low <= gap < high or abs(gap - first_gap) > tolerance:
                    break
                end += 1

        pattern_limit = MAX_PATTERN_NOTES
        run_gap = (notes[end - 1].time_ms - notes[start].time_ms) / max(1, end - start - 1)
        if WIGGLE_GAP_MS < run_gap < FAST_CIRCLE_GAP_MS:
            while end - start > MAX_SPIRAL_NOTES:
                size = min(MAX_SPIRAL_NOTES, end - start - 4)
                yield notes[start:start + size]
                start += size
            if end - start >= MIN_SPIRAL_NOTES:
                pattern_limit = MAX_SPIRAL_NOTES

        # Divide the remaining notes evenly to avoid a single tap at the end of a run.
        run_length = end - start
        group_count = (run_length + pattern_limit - 1) // pattern_limit
        if run_length == 2 and first_gap >= FAST_CIRCLE_GAP_MS:
            group_count = 2
        length, extra = divmod(run_length, group_count)
        for index in range(group_count):
            stop = start + length + (index < extra)
            yield notes[start:stop]
            start = stop


def jump_distance(gap, radius):
    return min(MAX_JUMP_PX, max(radius * 2 + 10, gap * 0.30))


def stream_spacing(gap, radius):
    return max(radius * 0.65, min(radius * 1.25, gap * 0.27))


def spiral_offsets(pattern, radius, angle, bend):
    # Begin tangent to the entry direction, and make each step widen the curve.
    angle -= bend * tau / 4
    orbit = radius * 1.35
    start = QPointF(cos(angle), sin(angle)) * orbit
    offsets = [QPointF()]
    for previous, note in pairwise(pattern):
        next_orbit = orbit + radius * 0.12
        spacing = stream_spacing(note.time_ms - previous.time_ms, radius)
        angle += bend * spacing / ((orbit + next_orbit) / 2)
        orbit = next_orbit
        offsets.append(QPointF(cos(angle), sin(angle)) * orbit - start)
    return offsets


def pattern_offsets(pattern, radius, angle, bend):
    count = len(pattern)
    if count == 1:
        return [QPointF()]
    first_gap = pattern[1].time_ms - pattern[0].time_ms
    fast = first_gap < FAST_CIRCLE_GAP_MS
    if fast and count <= 3:
        # osu! scales each step in a stack to a tenth of the hit-circle radius.
        return [QPointF(radius * 0.1, radius * 0.1) * index for index in range(count)]
    if fast and count >= MIN_SPIRAL_NOTES:
        return spiral_offsets(pattern, radius, angle, bend)

    offsets = [QPointF()]
    heading = QPointF(cos(angle), sin(angle))
    normal = QPointF(-heading.y(), heading.x()) * bend
    average_gap = (pattern[-1].time_ms - pattern[0].time_ms) / (count - 1)
    wiggle = average_gap <= WIGGLE_GAP_MS
    for index, (previous, note) in enumerate(pairwise(pattern)):
        gap = note.time_ms - previous.time_ms
        if fast:
            turn = index * tau / 18
            if wiggle:
                turn = tau / 8 if index % 2 == 0 else -tau / 8
            direction = angle + bend * turn
            step = QPointF(cos(direction), sin(direction)) * stream_spacing(gap, radius)
        elif count in (3, 4):
            direction = angle + bend * index * tau / count
            spacing = jump_distance(average_gap if count == 4 else gap, radius)
            step = QPointF(cos(direction), sin(direction)) * spacing
        else:
            # Offset each return so the two columns of the zigzag stay readable.
            lateral = radius * 0.55
            spacing = jump_distance(gap, radius)
            across = sqrt(spacing * spacing - lateral * lateral)
            step = heading * (across if index % 2 == 0 else -across) + normal * lateral
        offsets.append(offsets[-1] + step)
    return offsets


def fit_pattern(offsets, anchor, field):
    min_x = min(point.x() for point in offsets)
    max_x = max(point.x() for point in offsets)
    min_y = min(point.y() for point in offsets)
    max_y = max(point.y() for point in offsets)
    scale = min(1.0, field.width() / max(1, max_x - min_x), field.height() / max(1, max_y - min_y))
    left, right = field.left() - min_x * scale, field.right() - max_x * scale
    top, bottom = field.top() - min_y * scale, field.bottom() - max_y * scale
    origin = QPointF(min(right, max(left, anchor.x())), min(bottom, max(top, anchor.y())))
    return tuple(origin + point * scale for point in offsets)


def slider_endpoint(note, index, center, field):
    distance = min(160, (note.end_ms - note.time_ms) / note.spans * 0.27)
    for step in range(8):
        angle = (index * 3 + step) * tau / 8
        endpoint = center + QPointF(cos(angle), sin(angle)) * distance
        if field.contains(endpoint):
            return endpoint
    return center


def pattern_layouts(pattern, index, origin, gap, recent, field, radius):
    """Score orientations by crowding and how far fitting changes the intended entry."""
    min_spacing = radius * 2 + 10
    if gap is None:
        distance = clearance = 0
    elif gap < FAST_CIRCLE_GAP_MS:
        distance = max(radius * 0.7, gap * 0.22)
        clearance = radius * 0.6
    else:
        distance = jump_distance(gap, radius)
        clearance = min_spacing

    simultaneous = tuple(point for time, point in recent
                         if pattern[0].time_ms - time <= LEAD_IN_MS + HIT_WINDOW_MS)
    preferred_bend = 1 if index % 2 == 0 else -1
    rotates = len(pattern) > 3 or (len(pattern) == 3
                                  and pattern[1].time_ms - pattern[0].time_ms >= FAST_CIRCLE_GAP_MS)
    bends = (preferred_bend, -preferred_bend) if rotates else (preferred_bend,)
    directions = 1 if gap is None and not rotates else 8
    for step in range(directions):
        angle = (index * 3 + step) * tau / 8
        anchor = origin + QPointF(cos(angle), sin(angle)) * distance
        for bend in bends:
            offsets = pattern_offsets(pattern, radius, angle, bend)
            candidate = fit_pattern(offsets, anchor, field)
            nearest = min((hypot(point.x() - other.x(), point.y() - other.y())
                           for point in candidate for other in simultaneous), default=clearance)
            drift = hypot(candidate[0].x() - anchor.x(), candidate[0].y() - anchor.y())
            cost = max(0, clearance - nearest) + drift
            yield cost, candidate


def generate_circle_centers(notes, field, radius):
    """Lay out each pattern with one pattern of lookahead."""
    centers = []
    recent = deque()
    origin = field.center()
    previous = None
    for pattern, following in pairwise((*circle_patterns(notes), ())):
        first, last = pattern[0], pattern[-1]
        while recent and first.time_ms - recent[0][0] > LEAD_IN_MS + HIT_WINDOW_MS:
            recent.popleft()
        if first.spinner:
            origin = field.center()
            centers.append(origin)
            previous = last
            continue

        index = len(centers)
        gap = None if previous is None else first.time_ms - previous.finish_ms
        layouts = pattern_layouts(pattern, index, origin, gap, recent, field, radius)
        choices = nsmallest(LOOKAHEAD_CHOICES, layouts, key=lambda layout: layout[0])
        best_cost = float("inf")
        for cost, candidate in choices:
            exit_point = candidate[-1]
            if last.slider and last.spans % 2:
                exit_point = slider_endpoint(last, index, exit_point, field)
            if following and not following[0].spinner:
                next_gap = following[0].time_ms - last.finish_ms
                if next_gap < LEAD_IN_MS:
                    obstacles = (*recent, *((note.time_ms, point) for note, point in zip(pattern, candidate)))
                    next_choices = pattern_layouts(
                        following, index + len(pattern), exit_point, next_gap, obstacles, field, radius,
                    )
                    cost += 0.6 * min(next_cost for next_cost, _ in next_choices)
            if cost < best_cost:
                best_cost, best, best_exit = cost, candidate, exit_point

        recent.extend((note.time_ms, point) for note, point in zip(pattern, best))
        centers.extend(best)
        origin = best_exit
        previous = last
    return centers


@dataclass(frozen=True)
class Judgement:
    index: int
    value: int


class Round:
    def __init__(self, notes):
        self.notes = notes
        self.index = self.score = self.combo = self.best_combo = 0
        self.counts = dict.fromkeys((300, 100, 50, 0), 0)
        drain_seconds = max(1, (notes[-1].finish_ms - notes[0].time_ms) / 1000) if notes else 1
        density = min(16, len(notes) / drain_seconds * 8)
        self.score_multiplier = round((CIRCLE_SIZE + OVERALL_DIFFICULTY + density) * 5 / 38)
        self._reset_object()

    @property
    def current(self):
        return self.notes[self.index] if self.index < len(self.notes) else None

    @property
    def accuracy(self):
        if self.index == 0:
            return 1.0
        return sum(value * count for value, count in self.counts.items()) / (300 * self.index)

    @property
    def grade(self):
        great = self.counts[300] / self.index if self.index else 0
        no_miss = self.counts[0] == 0
        if great == 1:
            return "SS"
        if no_miss and great > 0.9 and self.counts[50] < self.index * 0.01:
            return "S"
        if great > 0.9 or (no_miss and great > 0.8):
            return "A"
        if great > 0.8 or (no_miss and great > 0.7):
            return "B"
        return "C" if great > 0.6 else "D"

    def _reset_object(self):
        self.slider_head = None
        self.slider_parts = 0
        self.next_checkpoint = 0
        self._checkpoints = self.current.slider_checkpoints() if self.current and self.current.slider else ()
        self._spin_angle = None
        self._spin_remainder = 0.0
        self._half_turns = 0

    def _increment_combo(self):
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)

    def _judge(self, value, increment_combo=True):
        result = Judgement(self.index, value)
        self.counts[value] += 1
        self.score += value + value * max(0, self.combo - 1) * self.score_multiplier // 25
        if not value:
            self.combo = 0
        elif increment_combo:
            self._increment_combo()
        self.index += 1
        self._reset_object()
        return result

    def tap(self, position_ms, target_index):
        """Return the final judgement, if any, and whether to play the hit sound."""
        note = self.current
        if note is None or target_index != self.index or note.spinner or self.slider_head is not None:
            return None, False
        delta = position_ms - note.time_ms
        if delta <= -EARLY_MISS_MS or delta >= HIT_WINDOW_MS:
            return None, False
        value = next((value for value, window in HIT_WINDOWS if abs(delta) < window - 0.5), 0)
        if note.slider:
            self.slider_head = bool(value)
            if value:
                self.slider_parts = 1
                self._increment_combo()
            else:
                self.combo = 0
            return None, bool(value)
        return self._judge(value), bool(value)

    def _advance_slider(self, position_ms, tracking_at):
        if self.slider_head is None and position_ms >= self.current.time_ms + HIT_WINDOW_MS:
            self.slider_head = False
            self.combo = 0
        while self.next_checkpoint < len(self._checkpoints):
            time_ms, points = self._checkpoints[self.next_checkpoint]
            if position_ms < time_ms:
                return None
            self.next_checkpoint += 1
            if tracking_at is not None and position_ms - time_ms <= 65 and tracking_at(time_ms):
                self.slider_parts += 1
                self.score += points
                self._increment_combo()
            elif self.next_checkpoint < len(self._checkpoints):
                self.combo = 0
        total = 1 + len(self._checkpoints)
        if self.slider_parts == total:
            return 300
        if 2 * self.slider_parts >= total:
            return 100
        return 50 if self.slider_parts else 0

    @property
    def _required_half_turns(self):
        duration = (self.current.end_ms - self.current.time_ms) / 1000
        return floor(2 * (duration * (1.25 + 0.25 * OVERALL_DIFFICULTY) + 0.5))

    @property
    def spinner_progress(self):
        turns = self._half_turns + abs(self._spin_remainder) / pi
        return min(1.0, turns / max(1, self._required_half_turns))

    def _advance_spinner(self, position_ms, angle):
        if position_ms < self.current.time_ms:
            return None
        if position_ms <= self.current.end_ms:
            if angle is not None and self._spin_angle is not None:
                self._spin_remainder += (angle - self._spin_angle + pi) % tau - pi
                if abs(self._spin_remainder) + 1e-9 >= pi:
                    self._half_turns += 1
                    self._spin_remainder -= pi if self._spin_remainder > 0 else -pi
                    if self._half_turns % 2 == 0:
                        self.score += 100 if self._half_turns <= self._required_half_turns else 1100
            self._spin_angle = angle
        if position_ms < self.current.end_ms:
            return None
        required = self._required_half_turns
        if self._half_turns >= required:
            return 300
        if self._half_turns and self._half_turns >= required - 2:
            return 100
        return 50 if self._half_turns and 4 * self._half_turns >= required else 0

    def advance(self, position_ms, tracking_at=None, spin_angle=None):
        judgements = []
        while (note := self.current) is not None:
            if note.slider:
                value = self._advance_slider(position_ms, tracking_at)
            elif note.spinner:
                value = self._advance_spinner(position_ms, spin_angle)
            else:
                value = 0 if position_ms >= note.time_ms + HIT_WINDOW_MS else None
            if value is None:
                break
            judgements.append(self._judge(value, increment_combo=not note.slider))
        return judgements


def demo_wave():
    """Generate an original click track for the demo."""
    sample_rate = 22050
    end_ms = DEMO.notes[-1].finish_ms
    samples = array("h", [0]) * (sample_rate * (end_ms + 750) // 1000)
    beats = set(range(1000, end_ms + 1, 500)) | {note.time_ms for note in DEMO.notes}
    for beat_ms in sorted(beats):
        first = beat_ms * sample_rate // 1000
        frequency = 880 if (beat_ms - 1000) % 2000 == 0 else 550
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
    pointer_changed = Signal(QPointF)
    tap_keys = (Qt.Key.Key_Z, Qt.Key.Key_X, Qt.Key.Key_Space)
    tap_buttons = (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)
    combo_colors = ("#327e87", "#8a5497", "#9a643d", "#4e7956")
    judgement_colors = {300: "#69d6d4", 100: "#a4dc89", 50: "#f0c27a", 0: "#ed8184"}

    def __init__(self, parent):
        super().__init__(parent)
        self.setMinimumSize(520, 300)
        self.setAccessibleName("Rhythm playfield")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.notes = ()
        self._dense_indices = set()
        self._layout_notes = None
        self._held = set()
        self.pointer = QPointF(-1, -1)
        self.round = None
        self.first = 0
        self.position_ms = 0
        self._feedback = []
        self._feedback_timer = QTimer(self, interval=16, timerType=Qt.TimerType.PreciseTimer)
        self._feedback_timer.timeout.connect(self._advance_feedback)
        self._layout_targets()
        self._number_targets()

    def set_frame(self, notes, first, position_ms, round_=None):
        if notes is not self.notes:
            self.clear_feedback()
            self.notes = notes
            self._layout_targets()
            self._number_targets()
            self._find_dense_circles()
        self.first, self.position_ms = first, position_ms
        self.round = round_
        self.update()

    def resizeEvent(self, event):
        self._layout_targets()
        super().resizeEvent(event)

    def _layout_targets(self):
        radius = (54.4 - 4.48 * CIRCLE_SIZE) * 1.00041
        self._scale = min((self.width() - 20) / 512, (self.height() - 20) / 384)
        self.target_radius = radius * self._scale
        self._field = QRectF(0, 0, 512 * self._scale, 384 * self._scale)
        self._field.moveCenter(QRectF(self.rect()).center())
        if self._layout_notes is not self.notes:
            field = QRectF(radius + 8, radius + 8, 512 - 2 * (radius + 8), 384 - 2 * (radius + 8))
            self._logical_centers = generate_circle_centers(self.notes, field, radius)
            self._logical_slider_ends = {
                index: slider_endpoint(note, index, center, field)
                for index, (note, center) in enumerate(zip(self.notes, self._logical_centers)) if note.slider
            }
            self._layout_notes = self.notes
        origin = self._field.topLeft()
        self._centers = [origin + center * self._scale for center in self._logical_centers]
        self._slider_ends = {index: origin + end * self._scale for index, end in self._logical_slider_ends.items()}

    def _number_targets(self):
        self._labels = []
        color_index, number = -1, 0
        previous = None
        for note in self.notes:
            if previous is None or previous.spinner or note.time_ms - previous.finish_ms >= 1500 or number == 8:
                color_index += 1
                number = 0
            number += 1
            self._labels.append((number, QColor(self.combo_colors[color_index % len(self.combo_colors)])))
            previous = note

    def _find_dense_circles(self):
        self._dense_indices.clear()
        for start in range(len(self.notes) - 3):
            group = self.notes[start:start + 4]
            if group[-1].time_ms - group[0].time_ms <= 3 * FAST_CIRCLE_GAP_MS and all(
                note.end_ms is None for note in group
            ):
                self._dense_indices.update(range(start, start + 4))

    @property
    def held(self):
        return bool(self._held)

    def clear_inputs(self):
        self._held.clear()

    def _press(self, button, point):
        if button in self._held:
            return
        self._held.add(button)
        self.pointer = point
        self.tapped.emit(point)

    def _release(self, button, point):
        self._held.discard(button)
        self._move_pointer(point)

    def _move_pointer(self, point):
        self.pointer = point
        self.pointer_changed.emit(point)

    def mousePressEvent(self, event):
        if event.button() in self.tap_buttons:
            self._press(event.button(), event.position())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() in self.tap_buttons:
            self._release(event.button(), event.position())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        self._move_pointer(event.position())

    def keyPressEvent(self, event):
        if event.key() in self.tap_keys:
            if not event.isAutoRepeat():
                self._press(event.key(), QPointF(self.mapFromGlobal(QCursor.pos())))
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in self.tap_keys:
            if not event.isAutoRepeat():
                self._release(event.key(), QPointF(self.mapFromGlobal(QCursor.pos())))
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.clear_inputs()
        self._move_pointer(self.pointer)
        super().focusOutEvent(event)

    def leaveEvent(self, event):
        self._move_pointer(QPointF(-1, -1))
        super().leaveEvent(event)

    def target_center(self, index):
        return self._centers[index]

    def slider_ball(self, index, position_ms):
        start = self.target_center(index)
        return start + (self._slider_ends[index] - start) * self.notes[index].slider_progress(position_ms)

    def tracks_slider(self, index, point, time_ms):
        delta = point - self.slider_ball(index, time_ms)
        return (self.held and QRectF(self.rect()).contains(point)
                and delta.x() ** 2 + delta.y() ** 2 <= (SLIDER_FOLLOW_SCALE * self.target_radius) ** 2)

    def spin_angle(self, point):
        delta = point - self._field.center()
        if (self.held and QRectF(self.rect()).contains(point)
                and delta.x() ** 2 + delta.y() ** 2 >= (20 * self._scale) ** 2):
            return atan2(delta.y(), delta.x())

    def animate(self, judgement):
        index, value = judgement.index, judgement.value
        note = self.notes[index]
        center = self.slider_ball(index, note.end_ms) if note.slider else self.target_center(index)
        offset = QPointF()
        if self.target_at(center) is not None:
            # Place the badge beside the shared aim point, toward the middle of the field.
            middle = self._field.center()
            offset = QPointF(1 if center.x() <= middle.x() else -1,
                             1 if center.y() <= middle.y() else -1) * self.target_radius
        self._feedback.append(NoteFeedback(index, monotonic(), value, offset))
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

    def _preview_start(self, index):
        time_ms = self.notes[index].time_ms
        start = time_ms - LEAD_IN_MS
        if index >= MAX_VISIBLE_TARGETS:
            earlier = self.notes[index - MAX_VISIBLE_TARGETS].time_ms
            start = max(start, min(earlier, time_ms - DENSE_PREVIEW_MS))
        return start

    def _visible_indices(self):
        end = min(len(self.notes), self.first + MAX_VISIBLE_TARGETS)
        for index in range(self.first, end):
            if self.position_ms <= self._preview_start(index):
                break
            yield index

    def target_at(self, point):
        for index in self._visible_indices():
            if self.notes[index].spinner:
                continue
            delta = point - self.target_center(index)
            if delta.x() ** 2 + delta.y() ** 2 <= self.target_radius ** 2:
                return index
        return None

    def _draw_approach_ring(self, painter, index, center, color, lead_ms):
        dense = self.first in self._dense_indices or index in self._dense_indices
        remaining = self.notes[index].time_ms - self.position_ms
        opacity = min(0.9, (lead_ms - remaining) / (lead_ms * 2 / 3), 1 + remaining / 50)
        if opacity <= 0:
            return
        if dense and index > self.first:
            opacity *= 0.55 if index == self.first + 1 else 0.18
        # A later preview fades in along the same trajectory as a full preview.
        radius = self.target_radius * (1 + 3 * max(0, remaining) / LEAD_IN_MS)
        painter.save()
        painter.setOpacity(opacity)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)
        painter.restore()

    def _draw_feedback(self, painter, accent):
        if not self._feedback:
            return
        now = monotonic()
        fill = QColor(accent)
        fill.setAlphaF(0.22)
        bounds = self._field.adjusted(2, 2, -2, -2)
        metrics = painter.fontMetrics()
        painter.save()
        for effect in self._feedback:
            remaining = effect.remaining(now)
            note = self.notes[effect.index]
            center = self.slider_ball(effect.index, note.end_ms) if note.slider else self.target_center(effect.index)
            painter.setOpacity(remaining)
            if effect.value:
                radius = self.target_radius * (1.0 + 0.55 * (1.0 - remaining ** 2))
                painter.setPen(QPen(accent, 2))
                painter.setBrush(fill)
                painter.drawEllipse(center, radius, radius)
            center += effect.offset
            color = QColor(self.judgement_colors[effect.value])
            text = str(effect.value) if effect.value else "Miss"
            label = QRectF(0, 0, max(44, metrics.horizontalAdvance(text) + 12), max(24, metrics.height() + 4))
            label.moveCenter(center)
            label.moveLeft(max(bounds.left(), min(label.left(), bounds.right() - label.width())))
            label.moveTop(max(bounds.top(), min(label.top(), bounds.bottom() - label.height())))
            painter.setPen(QPen(color, 1))
            painter.setBrush(QColor("#162128"))
            painter.drawRoundedRect(label, 5, 5)
            painter.drawText(label, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _draw_circle(self, painter, center, color, label):
        radius = self.target_radius
        painter.setPen(QPen(QColor("#f5f2eb"), 2.5))
        painter.setBrush(color)
        painter.drawEllipse(center, radius, radius)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius),
                         Qt.AlignmentFlag.AlignCenter, label)

    def _draw_slider(self, painter, index, color, opacity):
        note = self.notes[index]
        center, end = self.target_center(index), self._slider_ends[index]
        radius = self.target_radius
        painter.setOpacity(opacity)
        for width, stroke in ((2 * radius, QColor("#f5f2eb")), (2 * radius - 5, color.darker(155))):
            painter.setPen(QPen(stroke, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, end)

        active = self.round is not None and index == self.first
        collected = self.round.next_checkpoint if active else 0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f5f2eb"))
        for check_time, points in note.slider_checkpoints()[collected:]:
            if points == 10:
                painter.drawEllipse(self.slider_ball(index, check_time), 3.0, 3.0)

        if note.spans > 1:
            span = int(note.spans * max(0, self.position_ms - note.time_ms) / (note.end_ms - note.time_ms))
            if span < note.spans - 1:
                tip, origin = (end, center) if span % 2 == 0 else (center, end)
                delta = origin - tip
                direction = delta * (radius / hypot(delta.x(), delta.y()))
                normal = QPointF(-direction.y(), direction.x())
                painter.setPen(QPen(QColor("#ffffff"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                for side in (-1, 1):
                    painter.drawLine(tip + direction * 0.24, tip - direction * 0.19 + normal * (side * 0.24))

        if active and self.position_ms >= note.time_ms:
            ball = self.slider_ball(index, self.position_ms)
            if self.tracks_slider(index, self.pointer, self.position_ms):
                painter.setPen(QPen(QColor("#e5be77"), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                follow = radius * SLIDER_FOLLOW_SCALE
                painter.drawEllipse(ball, follow, follow)
            painter.setPen(QPen(QColor("#f5f2eb"), 2))
            painter.setBrush(color.lighter(150))
            painter.drawEllipse(ball, radius * 0.53, radius * 0.53)

    def _draw_spinner(self, painter, index, color):
        if self.position_ms < self.notes[index].time_ms:
            return
        note = self.notes[index]
        remaining = (note.end_ms - self.position_ms) / (note.end_ms - note.time_ms)
        center, radius = self.target_center(index), self._field.height() * 0.27
        progress = self.round.spinner_progress if self.round is not None and index == self.first else 0
        ring = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
        painter.setPen(QPen(color, 3))
        painter.setBrush(color.darker(185))
        painter.drawEllipse(ring)
        painter.setPen(QPen(QColor("#f5f2eb"), 3))
        painter.drawArc(ring, 90 * 16, round(360 * 16 * remaining))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, radius * progress, radius * progress)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter, "Clear" if progress == 1 else "Spin")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        painter.fillRect(self._field, palette.alternateBase())
        font = painter.font()
        font.setPixelSize(max(13, round(23 * self._scale)))
        font.setBold(True)
        painter.setFont(font)
        self._draw_feedback(painter, palette.highlight().color())
        for index in reversed(tuple(self._visible_indices())):
            note = self.notes[index]
            center = self.target_center(index)
            number, color = self._labels[index]
            preview_start = self._preview_start(index)
            lead_ms = note.time_ms - preview_start
            opacity = min(1, (self.position_ms - preview_start) / min(FADE_IN_MS, lead_ms / 3))
            if index in self._dense_indices and index > self.first:
                opacity *= 0.72 if index == self.first + 1 else 0.45
            if note.spinner:
                self._draw_spinner(painter, index, color)
                continue
            painter.save()
            painter.setOpacity(opacity)
            if note.slider:
                self._draw_slider(painter, index, color, opacity)
            head_visible = not (note.slider and self.round is not None and index == self.first
                                and self.round.slider_head is not None)
            if not head_visible:
                painter.restore()
                continue
            self._draw_circle(painter, center, color, str(number))
            self._draw_approach_ring(painter, index, center, color.lighter(150), lead_ms)
            painter.restore()


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
        self.resize(760, 520)

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
        self.board.pointer_changed.connect(self._pointer_changed)
        layout.addWidget(self.board, 1)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Song progress")
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.caption = QLabel()
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setMinimumHeight(self.caption.fontMetrics().lineSpacing())
        layout.addWidget(self.caption)
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
        self.board.set_frame(passage.notes, 0, passage.notes[0].time_ms - LEAD_IN_MS + FADE_IN_MS)
        self.caption.clear()
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
        elif status == QMediaPlayer.MediaStatus.EndOfMedia and self.round is not None and not self.pending_start:
            if self._selected_passage().full_song:
                self.progress.setValue(self.progress.maximum())
            # Process a slider tail or spinner that ends on the last audio sample.
            position = self.player.duration() - self.offset_ms
            self._show_frame(position, self._advance_round(position, self.board.pointer))
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
        self.last_position_ms = start_ms - self.offset_ms
        self.board.set_frame(self.round.notes, 0, self.last_position_ms, self.round)
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
                duration = self.player.duration()
                if duration > 0:
                    position = min(position, duration)
        self.last_position_ms = max(self.last_position_ms, position - self.offset_ms)
        return self.last_position_ms

    def _tick(self):
        if self.preroll_until is not None:
            if monotonic() >= self.preroll_until:
                self.preroll_until = None
                self.player.play()
        elif self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self.round is None or self.pending_start:
            return
        position = self._position()
        self._show_frame(position, self._advance_round(position, self.board.pointer))
        if self._selected_passage().full_song:
            audio_position = self.player.position()
            self.progress.setValue(audio_position)
            duration = self.player.duration()
            # The media backend can reach the duration without sending EndOfMedia.
            if duration > 0 and audio_position >= duration:
                self._finish_round()
        elif position > self.round.notes[-1].finish_ms + HIT_WINDOW_MS + 200:
            self._finish_round()

    def _playing_round(self):
        return (self.round is not None and not self.pending_start
                and (self.preroll_until is not None
                     or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState))

    def _advance_round(self, position, point):
        round_ = self.round
        tracking = lambda time: self.board.tracks_slider(round_.index, point, time)
        angle = self.board.spin_angle(point) if round_.current and round_.current.spinner else None
        return round_.advance(position, tracking, angle)

    def _pointer_changed(self, point):
        if self._playing_round() and self.round.current and self.round.current.end_ms is not None:
            position = self._position()
            self._show_frame(position, self._advance_round(position, point))

    def _tap(self, point):
        if not self._playing_round() or self.round.current is None:
            return
        position = self._position()
        judgements = self._advance_round(position, point)
        self.board.set_frame(self.round.notes, self.round.index, position, self.round)
        target = self.board.target_at(point)
        if target is not None:
            judgement, play_sound = self.round.tap(position, target)
            if judgement is not None:
                judgements.append(judgement)
            if play_sound and self.hit_sound.isLoaded():
                self.hit_sound.play()
        self._show_frame(position, judgements)

    def _show_frame(self, position, judgements):
        round_ = self.round
        self.board.set_frame(round_.notes, round_.index, position, round_)
        for judgement in judgements:
            self.board.animate(judgement)
        if judgements:
            value = judgements[-1].value
            self.status.setText(str(value) if value else "Miss")
        note = round_.current
        caption = note.text if note else ""
        if note and note.reading:
            caption += f"  /  {note.reading}"
        self.caption.setText(self.caption.fontMetrics().elidedText(
            caption, Qt.TextElideMode.ElideRight, self.board.width(),
        ))
        self._show_live_score()

    def _show_live_score(self):
        self.score.setText(f"Score {self.round.score:,}   ·   {self.round.accuracy:.2%}   ·   {self.round.combo}× combo")

    def _finish_round(self):
        result = self.round
        self.round = None
        self.preroll_until = None
        self.timer.stop()
        self.player.pause()
        self.board.set_frame(result.notes, len(result.notes), self.board.position_ms)
        for judgement in result.advance(float("inf")):
            self.board.animate(judgement)
        self.caption.clear()
        self.score.setText(f"Grade {result.grade}   ·   Score {result.score:,}   ·   {result.accuracy:.2%}"
                           f"   ·   Best combo {result.best_combo}×")
        self.status.setText("   ·   ".join(f"{count} {value or 'Miss'}" for value, count in result.counts.items()))
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
        if running:
            self.board.setCursor(Qt.CursorShape.CrossCursor)
            self.board.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.board.clear_inputs()
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
