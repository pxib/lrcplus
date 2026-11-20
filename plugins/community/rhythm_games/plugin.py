"""Circle and four-key rhythm games mapped from audio and lyric timing."""

from array import array
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass, replace
from heapq import nsmallest
from io import BytesIO
from itertools import accumulate, cycle, pairwise
from math import asinh, atan2, cos, exp, floor, hypot, isfinite, pi, sin, sqrt, tau
from operator import attrgetter
from pathlib import Path
from threading import Event
from time import monotonic
import wave

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.background_job import BackgroundJob
from core.rhythm_analysis import BeatGrid, beat_grid, load_audio_rhythm
from plugins.community.rhythm_games.mania import LANE_KEYS, MANIA_DEMO, ManiaBoard, ManiaSounds, assign_lanes


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
MANIA_CHORD_GAP_MS = 900
MANIA_RELEASE_WINDOW_MS = 80
MIN_SLIDER_MS = 425
MAX_SLIDER_MS = 1600
GENERATED_TAP_GAP_MS = 180
MIN_BURST_GAP_MS = 75
SUBDIVISION_TOLERANCE_MS = 42
MIN_SPINNER_MS = 2400
MAX_INFERRED_LINE_MS = 8000
SLIDER_FOLLOW_SCALE = 2.1
HIT_ANIMATION_SECONDS = 0.22
MISS_ANIMATION_SECONDS = 0.36
FAST_CIRCLE_GAP_MS = 200
WIGGLE_GAP_MS = 105
REGULAR_JUMP_GAP_MS = 420
MAX_PATTERN_NOTES = 7
MIN_LONG_STREAM_NOTES = 10
MAX_LONG_STREAM_NOTES = 14
STREAM_SEGMENT_NOTES = 4
LOOKAHEAD_CHOICES = 3
VARIED_LOOKAHEAD_CHOICES = 8
MAX_JUMP_PX = 180
PATTERN_REST_MS = 650
PATTERN_HISTORY_MS = 6000
# Budgets for the default chart. Aim is expressed in hit-circle radii.
BURST_WINDOW_MS = 1000
BURST_TARGETS = 8
SUSTAIN_WINDOW_MS = 6000
SUSTAIN_TARGETS = 36
CURSOR_SPEED = 16
SUSTAIN_AIM_PER_SECOND = 8
AIM_BURST_RADII = 12
TURN_WEIGHT = 0.35
RELIEF_SPACING = 0.8
PATTERN_ALTERNATIVES = {
    "arc": ("line", "s_curve"),
    "wiggle": ("line", "arc"),
    "triangle": ("arc", "s_curve"),
    "square": ("arc", "s_curve"),
    "zigzag": ("s_curve", "arc"),
}
SLIDER_DISTANCE_PER_BEAT = 135
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
    on_beat: bool = False
    accented: bool = False
    beat_length_ms: int | None = None
    generated: bool = False

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
        tick_ms = self.beat_length_ms
        for span in range(self.spans):
            if tick_ms:
                for tick in range(tick_ms, int(span_ms - 35), tick_ms):
                    checkpoints.append((self.time_ms + span_ms * span + tick, 10))
            elif span_ms >= 500:
                checkpoints.append((self.time_ms + span_ms * (span + 0.5), 10))
            checkpoints.append((self.time_ms + span_ms * (span + 1), 30))
        return tuple(checkpoints)


@dataclass(frozen=True)
class Passage:
    label: str
    notes: tuple[Note, ...]
    full_song: bool = False
    line_end_ms: int | None = None


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
    """Keep sustained objects clear of the next object."""
    for index, note in enumerate(notes):
        if note.slider:
            end_ms = min(note.end_ms, note.time_ms + MAX_SLIDER_MS)
            if index + 1 < len(notes):
                end_ms = min(end_ms, notes[index + 1].time_ms - slider_release_gap(note.beat_length_ms))
            minimum = max(250, note.beat_length_ms / 2) if note.beat_length_ms else MIN_SLIDER_MS
            note = replace(note, end_ms=end_ms if end_ms - note.time_ms >= minimum else None,
                           spans=note.spans if end_ms == note.end_ms else 1)
        yield note


def lyric_passages(lines, file_offset_ms, duration_ms):
    """Create passages from karaoke chunks or from timed lyric lines."""
    lines = tuple(lines)
    starts = [round(line.start * 1000 + file_offset_ms) for line in lines]
    boundaries = sorted(set(starts))
    for line, line_start in zip(lines, starts):
        line_text = " / ".join(row.strip() for index, row in enumerate(line.text.splitlines())
                               if index not in line.translation_lines and row.strip())
        if not line.has_karaoke:
            if (line_start < 0 or (duration_ms and line_start >= duration_ms)
                    or not any(c.isalnum() for c in line_text)):
                continue
            end = (round(line.end * 1000 + file_offset_ms) if line.end is not None and line.end > line.start
                   else line_start + MAX_INFERRED_LINE_MS)
            next_index = bisect_right(boundaries, line_start)
            if next_index < len(boundaries):
                end = min(end, boundaries[next_index])
            if duration_ms:
                end = min(end, duration_ms)
            timestamp = line_start // 1000
            yield Passage(f"{timestamp // 60}:{timestamp % 60:02d}  {line_text}",
                          (Note(line_start, line_text),), line_end_ms=end)
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
            yield Passage(f"{start // 60}:{start % 60:02d}  {line_text}", notes)


def full_song_passage(passages):
    """Build one chronological chart, with one target at each timestamp."""
    passages = tuple(passages)
    authored = {}
    for passage in passages:
        if passage.line_end_ms is None:
            for note in passage.notes:
                authored[note.time_ms] = max(authored.get(note.time_ms, note.time_ms), note.finish_ms)
    starts = sorted(authored)
    # A word-timed slider can extend past more than one later lyric timestamp.
    ends = []
    for start in starts:
        ends.append(max(authored[start], ends[-1] if ends else start))

    by_time = {}
    for passage in passages:
        for note in passage.notes:
            finish = note.finish_ms
            if passage.line_end_ms is not None:
                if note.time_ms in authored:
                    finish = note.time_ms
                else:
                    index = bisect_left(starts, note.time_ms + GENERATED_TAP_GAP_MS) - 1
                    if index >= 0 and ends[index] > note.time_ms - GENERATED_TAP_GAP_MS:
                        continue
            simultaneous = by_time.setdefault(note.time_ms, {})
            key = note.text, note.reading
            candidate = note if finish == note.finish_ms else replace(note, end_ms=None, spans=1)
            prior = simultaneous.get(key)
            if prior is not None:
                candidate = replace(max(prior, candidate, key=attrgetter("finish_ms")),
                                    generated=prior.generated and candidate.generated)
            simultaneous[key] = candidate
    notes = tuple(fit_sliders([
        replace(max(simultaneous.values(), key=lambda note: note.finish_ms),
                text=" / ".join(text for text, _ in simultaneous),
                reading=" / ".join(reading for _, reading in simultaneous if reading),
                generated=all(note.generated for note in simultaneous.values()))
        for _, simultaneous in sorted(by_time.items())
    ]))
    return Passage("Full song", notes, full_song=True)


def events_between(events, start, end):
    return events[bisect_left(events, start):bisect_left(events, end)]


def spaced_taps(candidates, reserved, gap=GENERATED_TAP_GAP_MS):
    additions = []
    for time_ms in candidates:
        neighbor = bisect_right(reserved, time_ms - gap)
        if neighbor < len(reserved) and reserved[neighbor] < time_ms + gap:
            continue
        if not additions or time_ms - additions[-1] >= gap:
            additions.append(time_ms)
    return additions


def subdivision_matches(start, end, cues):
    length = end - start
    tolerance = min(SUBDIVISION_TOLERANCE_MS, length / 12)
    for divisor in (2, 3, 4):
        if length < divisor * MIN_BURST_GAP_MS:
            continue
        matched = []
        for step in range(1, divisor):
            tick = start + round(length * step / divisor)
            near = events_between(cues, tick - tolerance, tick + tolerance + 1)
            if near:
                matched.append((step, tick, min(abs(tick - time_ms) for time_ms in near)))
        if matched:
            yield divisor, matched


def beat_subdivisions(start, end, attacks, rhythm):
    """Pick a single subdivision per beat from the attacks it explains."""
    best, best_score = (), (0, 0)
    for divisor, matched in subdivision_matches(start, end, attacks):
        emphasized = sum(rhythm.accented(tick) for _, tick, _ in matched)
        if divisor != 2 and len(matched) == 1 and not emphasized:
            continue
        if divisor == 4 and len(matched) == 2 and matched[-1][0] - matched[0][0] == 2:
            if emphasized < 2:
                continue
        score = (len(matched), -sum(error for _, _, error in matched))
        if score > best_score:
            best, best_score = tuple(tick for _, tick, _ in matched), score
    return best


def melody_subdivision(start, end, cues):
    """Fill an empty beat interval with one pitched cue and room for a longer gap."""
    matches = (
        (divisor != 2, error, divisor, tick)
        for divisor, matched in subdivision_matches(start, end, cues)
        for _, tick, error in matched
        if max(tick - start, end - tick) >= FAST_CIRCLE_GAP_MS
    )
    best = min(matches, default=None)
    return (best[-1],) if best is not None else ()


@dataclass(frozen=True)
class ChartTiming:
    grid: BeatGrid
    beats: tuple[int, ...]
    subdivisions: tuple[int, ...]
    off_grid: tuple[int, ...]
    attacks: tuple[int, ...]
    melody: tuple[int, ...]

    @classmethod
    def from_audio(cls, rhythm):
        grid = beat_grid(rhythm.beats_ms)
        attacks = tuple(sorted(set(rhythm.onsets_ms or rhythm.accents_ms) | {start for start, _ in rhythm.sustained_ms}))
        melody = set()
        for time in {note.start_ms for note in rhythm.melody_notes}:
            if grid.interval_at(time) is None:
                nearby = events_between(attacks, time - 45, time + 46)
                time = min(nearby, key=lambda attack: (abs(attack - time), attack), default=time)
            melody.add(time)
        melody = tuple(sorted(melody))
        beats = tuple(time for time in grid.beats_ms if not rhythm.silent(time) or rhythm.has_onset(time)
                      or events_between(melody, time - 45, time + 46))
        subdivisions = []
        for start, end in grid.intervals:
            ticks = beat_subdivisions(start, end, events_between(attacks, start + 1, end), rhythm)
            subdivisions.extend(ticks or melody_subdivision(start, end, events_between(melody, start + 1, end)))
        off_grid = tuple(time for time in attacks if grid.interval_at(time) is None)
        return cls(grid, beats, tuple(subdivisions), off_grid, attacks, melody)

    def taps(self, start, end):
        beats = []
        for time_ms in events_between(self.beats, start, end):
            if not beats or time_ms - beats[-1] >= GENERATED_TAP_GAP_MS:
                beats.append(time_ms)
        snapped = sorted((*beats, *events_between(self.subdivisions, start, end)))
        melodic = (time for time in events_between(self.melody, start, end) if self.grid.interval_at(time) is None)
        preferred = sorted((*snapped, *spaced_taps(melodic, snapped, FAST_CIRCLE_GAP_MS)))
        return sorted((*preferred, *spaced_taps(events_between(self.off_grid, start, end), preferred)))

    def slider_tail(self, desired, earliest, limit):
        interval = self.grid.interval_at(desired) or self.grid.interval_at(desired, 45)
        if interval:
            start, end = interval
            candidates = (start, round((start + end) / 2), end)
            nearby = [tick for tick in candidates if earliest <= tick <= limit]
            if nearby:
                tail = min(nearby, key=lambda tick: (abs(tick - desired), tick))
                if abs(tail - desired) <= (end - start) / 4:
                    return tail
        return desired


def slider_release_gap(beat_length_ms):
    if beat_length_ms:
        return min(HIT_WINDOWS[-1][1], max(MIN_BURST_GAP_MS, round(beat_length_ms / 4)))
    return HIT_WINDOWS[-1][1]


def next_attack_after(attacks, cutoff):
    index = bisect_right(attacks, cutoff)
    return attacks[index] if index < len(attacks) else float("inf")


def limit_chart_density(notes):
    """Thin crowded windows while keeping lyric anchors and authored targets."""
    for window_ms, limit in ((BURST_WINDOW_MS, BURST_TARGETS), (SUSTAIN_WINDOW_MS, SUSTAIN_TARGETS)):
        kept = [True] * len(notes)
        recent = deque()
        for index, note in enumerate(notes):
            while recent and note.time_ms - notes[recent[0]].time_ms >= window_ms:
                recent.popleft()
            recent.append(index)
            if len(recent) <= limit:
                continue

            # Prefer sustains, beats, and accents. For equal cues, open up the tightest gap.
            times = [float("-inf"), *(notes[i].time_ms for i in recent), float("inf")]
            choices = (
                (notes[i].end_ms is not None, notes[i].on_beat, notes[i].accented, end - start, -i)
                for i, start, end in zip(recent, times, times[2:]) if notes[i].generated
            )
            weakest = min(choices, default=None)
            if weakest is not None:
                dropped = -weakest[-1]
                kept[dropped] = False
                recent.remove(dropped)
        notes = tuple(note for note, keep in zip(notes, kept) if keep)
    return notes


def mania_chart(notes):
    """Map the source chart and layer occasional chords on accented beats."""
    layered = []
    next_chord = 0
    for note in notes:
        layered.append(replace(note, generated=False))
        if note.on_beat and note.accented and note.time_ms >= next_chord and note.end_ms is None:
            layered.append(replace(note, generated=True))
            next_chord = note.time_ms + MANIA_CHORD_GAP_MS
    return assign_lanes(limit_chart_density(layered))


def infer_sustained_objects(notes, rhythm, timing, end_ms, *, spinners=False):
    """Cover held sounds while keeping the next independent attack playable."""
    played_attacks = ()
    if timing.melody:
        played_attacks = tuple(note.time_ms for note in notes if any(
            events_between(cues, note.time_ms - SUBDIVISION_TOLERANCE_MS, note.time_ms + SUBDIVISION_TOLERANCE_MS + 1)
            for cues in (timing.attacks, timing.melody)
        ))
    free_at = 0
    for index, note in enumerate(notes):
        if note.time_ms < free_at:
            continue
        audio_end = rhythm.sustained_end(note.time_ms)
        melody_end = rhythm.held_melody_end(note.time_ms)
        end = melody_end if melody_end is not None else audio_end
        if end is None:
            yield note
            continue

        beat = note.beat_length_ms
        cutoff = note.time_ms + 70
        next_attack = next_attack_after(played_attacks, cutoff)
        if audio_end is not None:
            next_attack = min(next_attack, next_attack_after(timing.attacks, cutoff))
        following = bisect_left(notes, next_attack - SUBDIVISION_TOLERANCE_MS, index + 1,
                                key=attrgetter("time_ms"))
        if following < len(notes):
            next_attack = min(next_attack, notes[following].time_ms)

        spin_gap = beat or 500
        spin_minimum = note.time_ms + max(MIN_SPINNER_MS, 3 * spin_gap)
        spin_limit = min(end_ms, next_attack) - spin_gap
        if spinners and audio_end is not None:
            spin_end = min(end, audio_end)
            if min(spin_end, spin_limit) >= spin_minimum:
                tail = timing.slider_tail(min(spin_end, spin_limit), spin_minimum, min(spin_end + 25, spin_limit))
                free_at = tail + spin_gap
                yield replace(note, end_ms=tail, spinner=True)
                continue

        gap = slider_release_gap(beat)
        earliest = note.time_ms + (max(250, beat / 2) if beat else MIN_SLIDER_MS)
        limit = min(next_attack - gap, end_ms - gap, note.time_ms + MAX_SLIDER_MS)
        desired = min(end, limit)
        if desired < earliest:
            yield note
            continue

        tail = timing.slider_tail(desired, earliest, min(limit, end + 25))
        spans = 1
        if beat and note.on_beat:
            duration_beats = (tail - note.time_ms) / beat
            if duration_beats >= 2 - 0.02 and abs(duration_beats - round(duration_beats)) <= 0.02:
                spans = 2
        free_at = tail + gap
        yield replace(note, end_ms=tail, spans=spans)


def mark_rhythm(note, rhythm, timing):
    index = bisect_left(timing.beats, note.time_ms)
    on_grid_beat = index < len(timing.beats) and timing.beats[index] == note.time_ms
    return replace(note, on_beat=on_grid_beat or rhythm.on_beat(note.time_ms),
                   accented=rhythm.accented(note.time_ms), beat_length_ms=timing.grid.beat_length(note.time_ms))


def audio_notes(rhythm, timing, start, end):
    notes = tuple(mark_rhythm(Note(time_ms, "", generated=True), rhythm, timing)
                  for time_ms in timing.taps(start, end))
    return tuple(infer_sustained_objects(notes, rhythm, timing, end, spinners=True))


def audio_beat_passage(rhythm, duration_ms):
    notes = limit_chart_density(audio_notes(rhythm, ChartTiming.from_audio(rhythm), 0, duration_ms or float("inf")))
    return Passage("Audio beats", notes, full_song=True) if notes else None


def apply_audio_rhythm(passage, rhythm, timing=None):
    timing = timing or ChartTiming.from_audio(rhythm)
    notes = passage.notes
    if passage.line_end_ms is not None:
        anchor = notes[0]
        notes = (anchor, *(Note(time_ms, anchor.text, generated=True) for time_ms in timing.taps(
            anchor.time_ms + GENERATED_TAP_GAP_MS, passage.line_end_ms - GENERATED_TAP_GAP_MS + 1,
        )))
    notes = tuple(mark_rhythm(note, rhythm, timing) for note in notes)
    if passage.line_end_ms is not None:
        notes = limit_chart_density(tuple(infer_sustained_objects(notes, rhythm, timing, passage.line_end_ms)))
    return replace(passage, notes=notes)


def map_song(passages, rhythm, duration_ms):
    timing = ChartTiming.from_audio(rhythm)
    passages = tuple(apply_audio_rhythm(passage, rhythm, timing) for passage in passages)
    full_song = full_song_passage(passages)
    occupied = sorted((passage.notes[0].time_ms,
                       max(passage.notes[-1].finish_ms, passage.line_end_ms or 0)) for passage in passages)
    notes = list(full_song.notes)
    cursor = 0
    for start, end in occupied:
        if start - GENERATED_TAP_GAP_MS >= cursor:
            notes.extend(audio_notes(rhythm, timing, cursor, start - GENERATED_TAP_GAP_MS + 1))
        cursor = max(cursor, end + GENERATED_TAP_GAP_MS)
    notes.extend(audio_notes(rhythm, timing, cursor, duration_ms or float("inf")))
    return replace(full_song, notes=limit_chart_density(sorted(notes, key=attrgetter("time_ms")))), *passages


def distance_snap_gap(note, gap):
    return gap * 500 / note.beat_length_ms if note.beat_length_ms else gap


def circle_patterns(notes):
    """Group regular jumps, bursts, and streams using the local beat length."""
    start = 0
    while start < len(notes):
        end = start + 1
        first_gap = float("inf")
        if notes[start].end_ms is None and end < len(notes):
            first_gap = distance_snap_gap(notes[start], notes[end].time_ms - notes[start].time_ms)
            low, high = ((0, FAST_CIRCLE_GAP_MS) if first_gap < FAST_CIRCLE_GAP_MS
                         else (FAST_CIRCLE_GAP_MS, REGULAR_JUMP_GAP_MS))
            tolerance = max(30, first_gap * 0.25)
            while end < len(notes) and notes[end].end_ms is None:
                gap = distance_snap_gap(notes[end - 1], notes[end].time_ms - notes[end - 1].time_ms)
                if not low <= gap < high or abs(gap - first_gap) > tolerance:
                    break
                if (gap >= FAST_CIRCLE_GAP_MS and end - start >= 3
                        and notes[end].on_beat and notes[end].accented):
                    break
                end += 1

        pattern_limit = MAX_PATTERN_NOTES
        run_gap = distance_snap_gap(notes[start], (notes[end - 1].time_ms - notes[start].time_ms)
                                    / max(1, end - start - 1))
        if WIGGLE_GAP_MS < run_gap < FAST_CIRCLE_GAP_MS:
            while end - start > MAX_LONG_STREAM_NOTES:
                size = min(MAX_LONG_STREAM_NOTES, end - start - 4)
                yield notes[start:start + size]
                start += size
            if end - start >= MIN_LONG_STREAM_NOTES:
                pattern_limit = MAX_LONG_STREAM_NOTES

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


def fast_circle_join(previous, following):
    return (previous is not None and previous.end_ms is None and following.end_ms is None
            and distance_snap_gap(following, following.time_ms - previous.time_ms) < FAST_CIRCLE_GAP_MS)


def is_fast_stream(pattern, shape):
    return len(pattern) >= 2 and shape not in ("stack", "spiral") and fast_circle_join(pattern[-2], pattern[-1])


def default_pattern_shape(pattern):
    count = len(pattern)
    if count < 2:
        return "single"
    first_gap = distance_snap_gap(pattern[0], pattern[1].time_ms - pattern[0].time_ms)
    if first_gap < FAST_CIRCLE_GAP_MS:
        if count <= 3 and pattern[0].beat_length_ms is None:
            return "stack"
        if count >= MIN_LONG_STREAM_NOTES:
            return "long_stream"
        average_gap = (pattern[-1].time_ms - pattern[0].time_ms) / (count - 1)
        return "wiggle" if distance_snap_gap(pattern[0], average_gap) <= WIGGLE_GAP_MS else "arc"
    return {3: "triangle", 4: "square"}.get(count, "zigzag")


def demanding_pattern(pattern, shape, gap, radius):
    if pattern[0].end_ms is not None or shape == "stack":
        return False
    if len(pattern) > 1:
        average_gap = (pattern[-1].time_ms - pattern[0].time_ms) / (len(pattern) - 1)
        return average_gap < REGULAR_JUMP_GAP_MS
    return (gap is not None and FAST_CIRCLE_GAP_MS <= gap <= PATTERN_REST_MS
            and jump_distance(distance_snap_gap(pattern[0], gap), radius) >= 3.5 * radius)


def plan_circle_patterns(notes, radius):
    """Vary recurring shapes and ease spacing during chains of demanding patterns."""
    vary = notes is not DEMO.notes
    long_stream_shapes = cycle(("zigzag_stream", "s_curve", "line"))
    recent_shapes = deque(maxlen=4)
    consecutive = 0
    previous = None
    previous_shape = None
    for pattern, following in pairwise((*circle_patterns(notes), ())):
        first, last = pattern[0], pattern[-1]
        gap = first.time_ms - previous.finish_ms if previous is not None else None
        while recent_shapes and first.time_ms - recent_shapes[0][0] >= PATTERN_HISTORY_MS:
            recent_shapes.popleft()
        if previous is not None and previous.spinner:
            recent_shapes.clear()
        if gap is not None and (previous.spinner or gap >= max(PATTERN_REST_MS, first.beat_length_ms or 0)):
            consecutive = 0
        shape = default_pattern_shape(pattern)
        long_stream = shape == "long_stream"
        if long_stream:
            shape = next(long_stream_shapes) if vary else "spiral"
        spacing = 1.0
        if vary and demanding_pattern(pattern, shape, gap, radius):
            alternatives = PATTERN_ALTERNATIVES.get(shape)
            if len(pattern) > 2 and alternatives:
                shapes = tuple(old_shape for _, old_shape in recent_shapes)
                if shapes.count(shape) >= 2:
                    shape = min(alternatives, key=shapes.count)
                if shape == previous_shape == "arc" and fast_circle_join(previous, first):
                    # Break up consecutive curved groups within a fast stream.
                    shape = "line"
                recent_shapes.append((first.time_ms, shape))
            if consecutive == 2:
                spacing = RELIEF_SPACING
                consecutive = 0
            else:
                consecutive += 1
        else:
            consecutive = 0
        if (vary and len(pattern) >= 6 and is_fast_stream(pattern, shape)
                and (fast_circle_join(previous, first) or following and fast_circle_join(last, following[0]))):
            # Shorter groups leave room to turn before reaching the edge of the field.
            groups = (len(pattern) + STREAM_SEGMENT_NOTES - 1) // STREAM_SEGMENT_NOTES
            start = 0
            length, extra = divmod(len(pattern), groups)
            for index in range(groups):
                end = start + length + (index < extra)
                yield pattern[start:end], shape, spacing
                start = end
        else:
            yield pattern, shape, spacing
        previous = last
        previous_shape = shape


def spiral_offsets(pattern, radius, angle, bend, spacing_scale):
    # Begin tangent to the entry direction, and make each step widen the curve.
    angle -= bend * tau / 4
    orbit = radius * 1.35
    start = QPointF(cos(angle), sin(angle)) * orbit
    offsets = [QPointF()]
    for previous, note in pairwise(pattern):
        next_orbit = orbit + radius * 0.12
        spacing = stream_spacing(distance_snap_gap(previous, note.time_ms - previous.time_ms), radius) * spacing_scale
        angle += bend * spacing / ((orbit + next_orbit) / 2)
        orbit = next_orbit
        offsets.append(QPointF(cos(angle), sin(angle)) * orbit - start)
    return offsets


def pattern_offsets(pattern, radius, angle, bend, shape, spacing_scale):
    count = len(pattern)
    if shape == "single":
        return [QPointF()]
    if shape == "stack":
        # osu! scales each step in a stack to a tenth of the hit-circle radius.
        return [QPointF(radius * 0.1, radius * 0.1) * index for index in range(count)]
    if shape == "spiral":
        return spiral_offsets(pattern, radius, angle, bend, spacing_scale)

    offsets = [QPointF()]
    heading = QPointF(cos(angle), sin(angle))
    normal = QPointF(-heading.y(), heading.x()) * bend
    first_gap = distance_snap_gap(pattern[0], pattern[1].time_ms - pattern[0].time_ms)
    fast = first_gap < FAST_CIRCLE_GAP_MS
    average_gap = distance_snap_gap(pattern[0], (pattern[-1].time_ms - pattern[0].time_ms) / (count - 1))
    variable_spacing = (fast and shape in ("zigzag_stream", "s_curve", "line")
                        and 0 < sum(note.accented for note in pattern[1:]) < count - 1)
    for index, (previous, note) in enumerate(pairwise(pattern)):
        gap = distance_snap_gap(previous, note.time_ms - previous.time_ms)
        if fast:
            spacing = stream_spacing(gap, radius) * spacing_scale
            if variable_spacing:
                spacing *= 1.12 if note.accented else 0.92
        else:
            spacing = max(2 * radius, jump_distance(average_gap if shape == "square" else gap, radius) * spacing_scale)
        if shape == "zigzag_stream":
            # Keep both rows of circles within reach of the same straight cursor path.
            lateral = radius * 0.36
            forward = sqrt((spacing * 0.88) ** 2 - lateral ** 2)
            step = heading * forward + normal * (lateral if index % 2 == 0 else -lateral)
        elif shape == "zigzag":
            # Offset each return so the two columns of the zigzag stay readable.
            lateral = radius * 0.55
            across = sqrt(spacing * spacing - lateral * lateral)
            step = heading * (across if index % 2 == 0 else -across) + normal * lateral
        else:
            if shape == "wiggle":
                turn = tau / 8 if index % 2 == 0 else -tau / 8
            elif shape in ("triangle", "square"):
                turn = index * tau / count
            elif shape == "arc":
                turn = index * min(tau / 18, tau / (8 * max(1, count - 2)))
            elif shape == "s_curve":
                turn = sin(index * tau / max(3, count - 1)) * tau / 12
            else:
                turn = 0
            direction = angle + bend * turn
            step = QPointF(cos(direction), sin(direction)) * spacing
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
    # The transformed extrema can round just past a playfield edge.
    points = (origin + point * scale for point in offsets)
    return tuple(
        point if field.contains(point) else QPointF(
            min(field.right(), max(field.left(), point.x())),
            min(field.bottom(), max(field.top(), point.y())),
        )
        for point in points
    )


def slider_curve(note, index, center, field):
    """Fit a line or a quadratic curve of the intended travel length."""
    beats = (note.end_ms - note.time_ms) / (note.spans * (note.beat_length_ms or 500))
    distance = min(220, beats * SLIDER_DISTANCE_PER_BEAT)
    bend = (0.32 if index % 2 == 0 else -0.32) if distance >= 95 and index % 3 != 1 else 0
    for scale in (1, 0.85, 0.7):
        for shape in (bend, -bend, 0) if bend else (0,):
            slope = 2 * shape
            length_scale = (sqrt(1 + slope * slope) + asinh(slope) / slope) / 2 if slope else 1
            chord = distance * scale / length_scale
            for step in range(8):
                angle = (index * 3 + step) * tau / 8
                heading = QPointF(cos(angle), sin(angle))
                normal = QPointF(-heading.y(), heading.x())
                end = center + heading * chord
                control = center + (heading * 0.5 + normal * shape) * chord
                if field.contains(end) and field.contains(control):
                    return control, end
    return center, center


def quadratic_point(start, control, end, t):
    return start * ((1 - t) ** 2) + control * (2 * t * (1 - t)) + end * (t * t)


@dataclass(frozen=True)
class SliderPath:
    points: tuple[QPointF, ...]
    distances: tuple[float, ...]

    @classmethod
    def from_curve(cls, start, control, end):
        if control == (start + end) * 0.5:
            points = start, end
        else:
            points = tuple(quadratic_point(start, control, end, step / 24) for step in range(25))
        distances = tuple(accumulate((hypot(b.x() - a.x(), b.y() - a.y()) for a, b in pairwise(points)), initial=0.0))
        return cls(points, distances)

    def at(self, progress):
        distance = self.distances[-1] * progress
        index = bisect_right(self.distances, distance)
        if index >= len(self.points):
            return self.points[-1]
        before, after = self.points[index - 1:index + 1]
        fraction = (distance - self.distances[index - 1]) / (self.distances[index] - self.distances[index - 1])
        return before + (after - before) * fraction


def turn_cost(heading, movement):
    length = hypot(movement.x(), movement.y())
    alignment = QPointF.dotProduct(heading, movement) / length if length else 0
    return max(0, cos(tau / 8) - alignment)


def nearest_distance(points, obstacles):
    return min((hypot(point.x() - other.x(), point.y() - other.y())
                for point in points for other in obstacles), default=float("inf"))


def pattern_layouts(pattern, index, origin, gap, recent, field, radius, motif_bend=None, *, shape, spacing, incoming=None):
    """Score crowding, entry spacing, and turns between fast groups."""
    min_spacing = radius * 2 + 10
    if gap is not None:
        gap = distance_snap_gap(pattern[0], gap)
    if gap is None:
        distance = clearance = 0
    elif gap < FAST_CIRCLE_GAP_MS:
        distance = max(radius * 0.7, gap * 0.22) * spacing
        clearance = radius * 0.6
    else:
        distance = jump_distance(gap, radius)
        if pattern[0].accented:
            emphasis = 1.16 if pattern[0].on_beat else 1.08
            distance = min(MAX_JUMP_PX, distance * emphasis)
        distance = max(2 * radius, distance * spacing)
        clearance = max(2 * radius, min_spacing * spacing)

    simultaneous = tuple(point for time, point in recent
                         if pattern[0].time_ms - time <= LEAD_IN_MS + HIT_WINDOW_MS)
    if gap is None or gap >= FAST_CIRCLE_GAP_MS or pattern[0].end_ms is not None:
        incoming = None
    direction = None
    if incoming is not None:
        magnitude = hypot(incoming.x(), incoming.y())
        if magnitude:
            direction = atan2(incoming.y(), incoming.x())
            incoming = incoming / magnitude
        else:
            incoming = None
    connected = incoming is not None
    # The nearest two preceding circles are part of the stream join.
    older, adjacent = (simultaneous[:-2], simultaneous[-2:]) if connected else ((), simultaneous)
    keeps_streaming = connected and is_fast_stream(pattern, shape)
    preferred_bend = motif_bend if motif_bend is not None else (1 if index % 2 == 0 else -1)
    rotates = len(pattern) > 3 or (len(pattern) == 3
                                  and distance_snap_gap(pattern[0], pattern[1].time_ms - pattern[0].time_ms)
                                  >= FAST_CIRCLE_GAP_MS)
    bends = (preferred_bend, -preferred_bend) if rotates else (preferred_bend,)
    directions = 1 if gap is None and not rotates else 8
    for step in range(directions):
        if direction is None:
            angle = entry_angle = (index * 3 + step) * tau / 8
        else:
            turn = ((step + 4) % 8 - 4) * tau / 8
            angle = direction + turn
            entry_angle = direction + turn * 0.5 if abs(turn) <= tau / 4 else angle
        anchor = origin + QPointF(cos(entry_angle), sin(entry_angle)) * distance
        for bend in bends:
            offsets = pattern_offsets(pattern, radius, angle, bend, shape, spacing)
            candidate = fit_pattern(offsets, anchor, field)
            old_distance = nearest_distance(candidate, older) if older else float("inf")
            nearest = min(old_distance, nearest_distance(candidate, adjacent))
            drift = hypot(candidate[0].x() - anchor.x(), candidate[0].y() - anchor.y())
            cost = max(0, clearance - nearest) + drift
            if connected:
                cost += 1.8 * radius * turn_cost(incoming, candidate[0] - origin)
                if keeps_streaming:
                    opening = candidate[min(4, len(candidate) - 1)] - candidate[0]
                    cost += radius * turn_cost(incoming, opening)
                cost += max(0, radius * 1.25 - old_distance)
            if motif_bend is not None and bend != motif_bend:
                cost += 5
            yield cost, candidate, bend


def beat_pattern(pattern, shape):
    """Recognize repeated timing and beat accents within a small tolerance."""
    if len(pattern) < 3 or not any(note.on_beat for note in pattern):
        return None
    start = pattern[0].time_ms
    return shape, tuple((round(distance_snap_gap(pattern[0], note.time_ms - start) / 25), note.on_beat, note.accented)
                        for note in pattern)


def generate_circle_centers(notes, field, radius):
    """Lay out each pattern with one pattern of lookahead."""
    centers = []
    recent = deque()
    origin = field.center()
    previous = None
    incoming = None
    motifs = {}
    planned = (*plan_circle_patterns(notes, radius), ((), "single", 1.0))
    for (pattern, shape, spacing), (following, next_shape, next_spacing) in pairwise(planned):
        first, last = pattern[0], pattern[-1]
        while recent and first.time_ms - recent[0][0] > LEAD_IN_MS + HIT_WINDOW_MS:
            recent.popleft()
        if first.spinner:
            origin = field.center()
            centers.append(origin)
            previous = last
            incoming = None
            continue

        index = len(centers)
        gap = None if previous is None else first.time_ms - previous.finish_ms
        motif, following_motif = beat_pattern(pattern, shape), beat_pattern(following, next_shape)
        layouts = pattern_layouts(pattern, index, origin, gap, recent, field, radius, motifs.get(motif),
                                  shape=shape, spacing=spacing, incoming=incoming)
        next_gap = following[0].time_ms - last.finish_ms if following else float("inf")
        look_ahead = next_gap < LEAD_IN_MS and not following[0].spinner
        fast_stream = notes is not DEMO.notes and is_fast_stream(pattern, shape)
        continuous = look_ahead and fast_stream and fast_circle_join(last, following[0])
        # Try more placements when the next group needs room for its entry or direction.
        extended = look_ahead and (continuous or next_shape != default_pattern_shape(following))
        choice_count = VARIED_LOOKAHEAD_CHOICES if extended else LOOKAHEAD_CHOICES
        choices = nsmallest(choice_count, layouts, key=lambda layout: layout[0])
        best_cost = float("inf")
        for cost, candidate, bend in choices:
            exit_point = candidate[-1]
            outgoing = exit_point - candidate[max(0, len(candidate) - 5)] if fast_stream else None
            track = ()
            if last.slider:
                control, end = slider_curve(last, index, exit_point, field)
                track = tuple((last.finish_ms, quadratic_point(exit_point, control, end, step / 5))
                              for step in range(1, 6))
                if last.spans % 2:
                    exit_point = end
            if look_ahead:
                obstacles = (*recent, *((note.time_ms, point) for note, point in zip(pattern, candidate)), *track)
                next_choices = pattern_layouts(
                    following, index + len(pattern), exit_point, next_gap, obstacles, field, radius,
                    bend if motif is not None and motif == following_motif else motifs.get(following_motif),
                    shape=next_shape, spacing=next_spacing, incoming=outgoing,
                )
                cost += 0.6 * min(next_cost for next_cost, _, _ in next_choices)
            if cost < best_cost:
                best_cost, best, best_exit, best_bend = cost, candidate, exit_point, bend
                best_track = track
                best_heading = outgoing

        if motif is not None:
            motifs.setdefault(motif, best_bend)
        recent.extend((note.time_ms, point) for note, point in zip(pattern, best))
        recent.extend(best_track)
        centers.extend(best)
        origin = best_exit
        incoming = best_heading
        previous = last
    return centers


class AimBudget:
    """Limit peak travel speed and rolling travel effort in the playfield."""

    def __init__(self, radius):
        self.speed = CURSOR_SPEED * radius / 1000
        self.sustain_speed = SUSTAIN_AIM_PER_SECOND * radius / 1000
        self.capacity = AIM_BURST_RADII * radius
        self.history = deque()
        self.used = 0.0

    def travel(self, distance, turn_factor, duration_ms, finish_ms):
        duration_ms = max(0, duration_ms)
        start_ms = finish_ms - duration_ms
        while self.history and start_ms - self.history[0][0] >= SUSTAIN_WINDOW_MS:
            self.used -= self.history.popleft()[1]
        # Charge only effort above the sustained pace, so exhausting the burst budget
        # still leaves room to move every beat.
        sustained = self.sustain_speed * duration_ms
        effort = min(distance * turn_factor, self.speed * duration_ms,
                     sustained + max(0, self.capacity - self.used))
        excess = effort - sustained
        if excess > 0:
            self.history.append((finish_ms, excess))
            self.used += excess
        return effort / turn_factor


def aim_turn(before, after):
    if before is None:
        return 0.0
    cross = before.x() * after.y() - before.y() * after.x()
    angle = atan2(abs(cross), QPointF.dotProduct(before, after))
    return TURN_WEIGHT * angle / pi


def fit_chart_aim(notes, proposed, field, radius):
    """Budget cursor travel using the same centers and slider paths used for play."""
    budget = None if notes is DEMO.notes else AimBudget(radius)
    centers, paths = [], {}
    origin, heading, previous = None, None, None
    for index, (note, center) in enumerate(zip(notes, proposed)):
        if previous is not None:
            gap = note.time_ms - previous.finish_ms
            if gap >= PATTERN_REST_MS or note.spinner:
                heading = None
            if budget is not None and not note.spinner:
                movement = center - origin
                distance = hypot(movement.x(), movement.y())
                if distance:
                    travel = budget.travel(distance, 1 + aim_turn(heading, movement), gap, note.time_ms)
                    if travel < distance:
                        center = origin + movement * (travel / distance)
                    if travel:
                        heading = movement
        centers.append(center)
        origin = center
        if note.slider:
            path = SliderPath.from_curve(center, *slider_curve(note, index, center, field))
            distance = path.distances[-1] * note.spans
            if budget is not None and distance:
                start_heading = path.points[1] - center
                end_heading = path.points[-1] - path.points[-2]
                turn = aim_turn(start_heading, end_heading)
                turn += (aim_turn(heading, start_heading) + TURN_WEIGHT * (note.spans - 1)) / note.spans
                travel = budget.travel(distance, 1 + turn, note.finish_ms - note.time_ms, note.finish_ms)
                if travel < distance:
                    scale = travel / distance
                    path = SliderPath(tuple(center + (point - center) * scale for point in path.points),
                                      tuple(length * scale for length in path.distances))
                if travel:
                    heading = end_heading if note.spans % 2 else -start_heading
            paths[index] = path
            origin = path.at(note.slider_progress(note.finish_ms))
        previous = note
    return centers, paths


@dataclass(frozen=True)
class Judgement:
    index: int
    value: int


def hit_grade(delta):
    return next((value for value, window in HIT_WINDOWS if abs(delta) < window - 0.5), 0)


class RoundScore:
    def __init__(self, notes):
        self.notes = notes
        self.index = self.judged = self.score = self.combo = self.best_combo = 0
        self.counts = dict.fromkeys((300, 100, 50, 0), 0)
        self.finish_ms = max((note.finish_ms for note in notes), default=0)
        drain_seconds = max(1, (self.finish_ms - notes[0].time_ms) / 1000) if notes else 1
        density = min(16, len(notes) / drain_seconds * 8)
        self.score_multiplier = round((CIRCLE_SIZE + OVERALL_DIFFICULTY + density) * 5 / 38)

    @property
    def current(self):
        return self.notes[self.index] if self.index < len(self.notes) else None

    @property
    def accuracy(self):
        if self.judged == 0:
            return 1.0
        return sum(value * count for value, count in self.counts.items()) / (300 * self.judged)

    @property
    def grade(self):
        great = self.counts[300] / self.judged if self.judged else 0
        no_miss = self.counts[0] == 0
        if great == 1:
            return "SS"
        if no_miss and great > 0.9 and self.counts[50] < self.judged * 0.01:
            return "S"
        if great > 0.9 or (no_miss and great > 0.8):
            return "A"
        if great > 0.8 or (no_miss and great > 0.7):
            return "B"
        return "C" if great > 0.6 else "D"

    def _increment_combo(self):
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)

    def _record_judgement(self, index, value, increment_combo=True):
        self.judged += 1
        self.counts[value] += 1
        self.score += value + value * max(0, self.combo - 1) * self.score_multiplier // 25
        if not value:
            self.combo = 0
        elif increment_combo:
            self._increment_combo()
        return Judgement(index, value)


class Round(RoundScore):
    def __init__(self, notes):
        super().__init__(notes)
        self._reset_object()

    def _reset_object(self):
        self.slider_head = None
        self.slider_parts = 0
        self.next_checkpoint = 0
        self._checkpoints = self.current.slider_checkpoints() if self.current and self.current.slider else ()
        self._spin_angle = None
        self._spin_remainder = 0.0
        self._half_turns = 0

    def _judge(self, value, increment_combo=True):
        result = self._record_judgement(self.index, value, increment_combo)
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
        value = hit_grade(delta)
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


class ManiaRound(RoundScore):
    def __init__(self, notes):
        super().__init__(notes)
        self._lanes = tuple(deque() for _ in LANE_KEYS)
        for index, note in enumerate(notes):
            self._lanes[note.lane].append(index)
        self._heads = {}
        self._resolved = [False] * len(notes)

    def is_judged(self, index):
        return self._resolved[index]

    def is_holding(self, index):
        return index in self._heads

    def caption_note(self, position_ms):
        if self.current is None:
            return None
        index = bisect_right(self.notes, position_ms + HIT_WINDOWS[0][1], key=attrgetter("time_ms")) - 1
        return self.notes[max(0, index)]

    def _judge_lane(self, index, value):
        self._resolved[index] = True
        while self.index < len(self.notes) and self._resolved[self.index]:
            self.index += 1
        return self._record_judgement(index, value)

    def press(self, position_ms, lane):
        pending = self._lanes[lane]
        if not pending or self.is_holding(pending[0]):
            return None, False
        index = pending[0]
        note = self.notes[index]
        value = hit_grade(position_ms - note.time_ms)
        if not value:
            return None, False
        if note.end_ms is not None:
            self._heads[index] = value
            return None, True
        pending.popleft()
        return self._judge_lane(index, value), True

    def release(self, position_ms, lane):
        pending = self._lanes[lane]
        if not pending or not self.is_holding(pending[0]):
            return None
        index = pending.popleft()
        value = self._heads.pop(index)
        return self._judge_lane(index, value if position_ms >= self.notes[index].end_ms - MANIA_RELEASE_WINDOW_MS else 0)

    def advance(self, position_ms):
        due = []
        for pending in self._lanes:
            while pending:
                index = pending[0]
                note = self.notes[index]
                holding = self.is_holding(index)
                deadline = note.end_ms if holding else note.time_ms + HIT_WINDOW_MS
                if position_ms < deadline:
                    break
                value = self._heads.pop(index) if holding else 0
                due.append((deadline, pending.popleft(), value if isfinite(position_ms) else 0))
        return [self._judge_lane(index, value) for _, index, value in sorted(due)]


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
    instructions = HOW_TO_PLAY

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
            proposed = generate_circle_centers(self.notes, field, radius)
            self._logical_centers, self._slider_paths = fit_chart_aim(self.notes, proposed, field, radius)
            self._layout_notes = self.notes
        origin = self._field.topLeft()
        self._centers = [origin + center * self._scale for center in self._logical_centers]
        self._slider_tracks = {}
        for index, path in self._slider_paths.items():
            track = QPainterPath(self._centers[index])
            for point in path.points[1:]:
                track.lineTo(origin + point * self._scale)
            self._slider_tracks[index] = track

    def _number_targets(self):
        self._labels = []
        color_index, number = -1, 0
        previous = None
        for note in self.notes:
            gap = note.time_ms - previous.finish_ms if previous is not None else float("inf")
            beat = note.beat_length_ms
            phrase = beat and (gap >= 2 * beat or (number >= 4 and gap >= beat / 2 and note.on_beat and note.accented))
            if (previous is None or previous.spinner or note.spinner or gap >= 1500 or phrase
                    or (number >= 8 and distance_snap_gap(note, gap) >= FAST_CIRCLE_GAP_MS) or number == 12):
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
        progress = self.notes[index].slider_progress(position_ms)
        return self._field.topLeft() + self._slider_paths[index].at(progress) * self._scale

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
        radius = self.target_radius
        painter.setOpacity(opacity)
        for width, stroke in ((2 * radius, QColor("#f5f2eb")), (2 * radius - 5, color.darker(155))):
            painter.setPen(QPen(stroke, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(self._slider_tracks[index])

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
                path = self._slider_paths[index]
                tip, inner = (path.points[-1], path.points[-2]) if span % 2 == 0 else path.points[:2]
                delta = inner - tip
                direction = delta * (radius / max(1e-9, hypot(delta.x(), delta.y())))
                tip = self._field.topLeft() + tip * self._scale
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
        note = self.notes[index]
        remaining = min(1, (note.end_ms - self.position_ms) / (note.end_ms - note.time_ms))
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
            painter.save()
            if note.spinner:
                if index == self.first:
                    painter.setOpacity(opacity)
                    self._draw_spinner(painter, index, color)
                painter.restore()
                continue
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
    def __init__(self, host, api, passages, *, audio_only=False, duration_ms=0):
        super().__init__(host)
        self.api = api
        self.host = host
        self.track_path = api.get_current_track()
        if passages:
            self.passages = (full_song_passage(passages), *passages, DEMO)
        elif audio_only:
            self.passages = (Passage("Audio beats", (), full_song=True), DEMO)
        else:
            self.passages = (DEMO,)
        self._audio_only = audio_only
        self._duration_ms = duration_ms
        self.round = None
        self.pending_start = False
        self.source_ready = False
        self.using_demo = None
        self.anchor = None
        self.preroll_until = None
        self.resume_main = False
        self.main_paused = False
        self._rhythm_job = None
        self._rhythm_cancelled = Event()
        self._waiting_for_rhythm = False
        self._mania_source = None
        self._mania_notes = ()
        self.setWindowTitle("Rhythm Games")
        self.setModal(True)
        self.resize(760, 520)

        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.hit_sound = QSoundEffect(self)
        self.hit_sound.setSource(QUrl.fromLocalFile(str(HIT_SOUND_PATH)))
        self.mania_sounds = None
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

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Rhythm game mode")
        self.circle_board = RhythmBoard(self.tabs)
        self.circle_board.tapped.connect(self._tap)
        self.circle_board.pointer_changed.connect(self._pointer_changed)
        self.mania_board = ManiaBoard(self.tabs, LEAD_IN_MS)
        self.mania_board.pressed.connect(lambda lane: self._lane_input(lane, True))
        self.mania_board.released.connect(lambda lane: self._lane_input(lane, False))
        self.tabs.addTab(self.circle_board, "Circles")
        self.tabs.addTab(self.mania_board, "Mania")
        layout.addWidget(self.tabs, 1)
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
        self.tabs.currentChanged.connect(self._select_passage)
        self._show_passage()

    @property
    def board(self):
        return self.tabs.currentWidget()

    def analyze_track(self):
        job = self._rhythm_job = BackgroundJob(self, "rhythm-analysis")
        job.finished.connect(self._rhythm_ready)
        job.failed.connect(lambda _message: self._rhythm_ready(None))
        job.start(load_audio_rhythm, self.track_path, self._rhythm_cancelled)
        if self._audio_only:
            self.status.setText("Analyzing rhythm…")

    def _rhythm_ready(self, rhythm):
        if self._rhythm_job is None:
            return
        self._rhythm_job.close()
        self._rhythm_job.deleteLater()
        self._rhythm_job = None
        if self._audio_only:
            chart = audio_beat_passage(rhythm, self._duration_ms) if rhythm is not None else None
            if chart is None:
                showing_audio = self._selected_passage() is not DEMO
                self.passages = (DEMO,)
                blocked = self.passage.blockSignals(True)
                self.passage.removeItem(0)
                self.passage.blockSignals(blocked)
                if showing_audio:
                    self._waiting_for_rhythm = False
                    self._set_controls_running(False)
                    self._show_passage()
                    self.status.setText("No beat cues found. Try the demo.")
                return
            self.passages = (chart, DEMO)
        elif rhythm is not None and (rhythm.beats_ms or rhythm.onsets_ms or rhythm.accents_ms
                                     or rhythm.sustained_ms or rhythm.melody_notes):
            self.passages = (*map_song(self.passages[1:-1], rhythm, self._duration_ms), DEMO)
        if self._waiting_for_rhythm:
            self._waiting_for_rhythm = False
            self._start_round()
        elif self.round is None and self._selected_passage() is not DEMO:
            self._show_passage()
            if self._audio_only:
                self.status.setText(self.board.instructions)

    def _select_passage(self):
        if self.mania_sounds is not None:
            self.mania_sounds.stop()
        if self._waiting_for_rhythm:
            self._waiting_for_rhythm = False
            self.start.setEnabled(True)
        self._show_passage()
        self.start.setText("Start")
        self.status.setText(self.board.instructions if self._selected_passage().notes else "Analyzing rhythm…")

    def _selected_passage(self):
        return self.passages[self.passage.currentIndex()]

    def _chart_notes(self, passage):
        if self.board is self.circle_board:
            return passage.notes
        if passage is DEMO:
            return MANIA_DEMO
        if self._mania_source is not passage.notes:
            self._mania_source = passage.notes
            self._mania_notes = mania_chart(passage.notes)
        return self._mania_notes

    def _show_passage(self):
        passage = self._selected_passage()
        notes = self._chart_notes(passage)
        position = notes[0].time_ms - LEAD_IN_MS + FADE_IN_MS if notes else 0
        self.board.set_frame(notes, 0, position)
        self.caption.clear()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(passage.full_song)
        self.score.setText(f"{len(notes)} targets" if notes else "")

    def _start_round(self):
        if self.pending_start or self.round is not None:
            return
        passage = self._selected_passage()
        if passage is not DEMO and self._rhythm_job is not None:
            self._waiting_for_rhythm = True
            self.start.setEnabled(False)
            self.status.setText("Analyzing rhythm…")
            return
        notes = self._chart_notes(passage)
        if not notes:
            return
        if not self.main_paused:
            self.resume_main = self.host.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self.api.pause()
            self.main_paused = True
        device = self.host.audio_output.device()
        volume = min(1.0, self.host.volume_slider.value() / 100)
        self.output.setDevice(device)
        self.output.setVolume(volume)
        self._stop_effects()
        self.hit_sound.setAudioDevice(device)
        self.hit_sound.setVolume(volume * HIT_SOUND_GAIN)
        if self.board is self.mania_board:
            if self.mania_sounds is None:
                self.mania_sounds = ManiaSounds(self, HIT_SOUND_PATH.parent)
            self.mania_sounds.configure(device, volume * HIT_SOUND_GAIN)
            self.round = ManiaRound(notes)
        else:
            self.round = Round(notes)
        self.board.clear_inputs()
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
            self._show_frame(position, self._advance_round(position))
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
        self._show_frame(position, self._advance_round(position))
        if self._selected_passage().full_song:
            audio_position = self.player.position()
            self.progress.setValue(audio_position)
            duration = self.player.duration()
            # The media backend can reach the duration without sending EndOfMedia.
            if duration > 0 and audio_position >= duration:
                self._finish_round()
        elif position > self.round.finish_ms + HIT_WINDOW_MS + 200:
            self._finish_round()

    def _playing_round(self):
        return (self.round is not None and not self.pending_start
                and (self.preroll_until is not None
                     or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState))

    def _advance_round(self, position, point=None):
        round_ = self.round
        if isinstance(round_, ManiaRound):
            return round_.advance(position)
        if point is None:
            point = self.circle_board.pointer
        tracking = lambda time: self.board.tracks_slider(round_.index, point, time)
        angle = self.board.spin_angle(point) if round_.current and round_.current.spinner else None
        return round_.advance(position, tracking, angle)

    def _pointer_changed(self, point):
        if (isinstance(self.round, Round) and self._playing_round()
                and self.round.current and self.round.current.end_ms is not None):
            position = self._position()
            self._show_frame(position, self._advance_round(position, point))

    def _tap(self, point):
        if not isinstance(self.round, Round) or not self._playing_round() or self.round.current is None:
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

    def _lane_input(self, lane, pressed):
        if not isinstance(self.round, ManiaRound) or not self._playing_round():
            return
        position = self._position()
        judgements = self.round.advance(position)
        play_sound = False
        if pressed:
            judgement, play_sound = self.round.press(position, lane)
        else:
            judgement = self.round.release(position, lane)
        if judgement is not None:
            judgements.append(judgement)
        self._show_frame(position, judgements)
        if play_sound and self.mania_sounds is not None:
            sound = self.mania_sounds.holds[lane] if judgement is None else self.mania_sounds.taps[judgement.value][lane]
            self.mania_sounds.play(sound, self.hit_sound)

    def _show_frame(self, position, judgements):
        round_ = self.round
        self.board.set_frame(round_.notes, round_.index, position, round_)
        for judgement in judgements:
            self.board.animate(judgement)
            if isinstance(round_, ManiaRound) and self.mania_sounds is not None:
                note = round_.notes[judgement.index]
                if note.end_ms is not None:
                    self.mania_sounds.holds[note.lane].stop()
        if judgements:
            value = judgements[-1].value
            self.status.setText(str(value) if value else "Miss")
        note = round_.caption_note(position) if isinstance(round_, ManiaRound) else round_.current
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
        if isinstance(result, ManiaRound) and self.mania_sounds is not None:
            self._stop_effects()
            self.mania_sounds.play_result(result.counts)

    def _stop_effects(self):
        self.hit_sound.stop()
        if self.mania_sounds is not None:
            self.mania_sounds.stop()

    def _playback_error(self, error, message):
        self.timer.stop()
        self._stop_effects()
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
        self.tabs.setTabEnabled(1 - self.tabs.currentIndex(), not running)
        if running:
            if self.board is self.circle_board:
                self.board.setCursor(Qt.CursorShape.CrossCursor)
            self.board.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.board.clear_inputs()
            self.board.unsetCursor()

    def _release_audio(self, _result):
        self._rhythm_cancelled.set()
        self._waiting_for_rhythm = False
        if self._rhythm_job is not None:
            self._rhythm_job.close()
            self._rhythm_job = None
        self.timer.stop()
        self._stop_effects()
        self.board.clear_feedback()
        self.round = None
        self.board.clear_inputs()
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
        has_audio = bool(path and Path(path).is_file())
        duration_ms = self.api.get_duration() if has_audio else 0
        if has_audio and not getattr(self.window, "plain_lyrics_mode", False):
            passages = tuple(lyric_passages(
                getattr(self.window, "lyric_lines", ()),
                getattr(self.window, "lyrics_offset_seconds", 0) * 1000,
                duration_ms,
            ))
        self.dialog = RhythmDialog(self.window, self.api, passages,
                                   audio_only=has_audio and not passages, duration_ms=duration_ms)
        self.dialog.finished.connect(self._close_game)
        if has_audio:
            self.dialog.analyze_track()
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
