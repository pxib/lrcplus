"""Extract beats, attacks, and pitched notes from local audio for rhythm charts."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from itertools import pairwise
from operator import attrgetter
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.rhythm_melody import MelodyNote


BEAT_FIT_TOLERANCE_MS = 42


@dataclass(frozen=True)
class AudioRhythm:
    beats_ms: tuple[int, ...] = ()
    accents_ms: tuple[int, ...] = ()
    onsets_ms: tuple[int, ...] = ()
    sustained_ms: tuple[tuple[int, int], ...] = ()
    silence_ms: tuple[tuple[int, int], ...] = ()
    melody_notes: tuple[MelodyNote, ...] = ()

    @staticmethod
    def _near(events, time_ms, tolerance_ms):
        index = bisect_left(events, time_ms - tolerance_ms)
        return index < len(events) and events[index] <= time_ms + tolerance_ms

    def on_beat(self, time_ms):
        return self._near(self.beats_ms, time_ms, 55)

    def accented(self, time_ms):
        return self._near(self.accents_ms, time_ms, 45)

    def has_onset(self, time_ms):
        return self._near(self.onsets_ms or self.accents_ms, time_ms, 45)

    def silent(self, time_ms):
        index = bisect_right(self.silence_ms, (time_ms, float("inf"))) - 1
        return index >= 0 and time_ms < self.silence_ms[index][1]

    def sustained_end(self, time_ms):
        index = bisect_left(self.sustained_ms, (time_ms - 70, 0))
        if index < len(self.sustained_ms) and self.sustained_ms[index][0] <= time_ms + 70:
            return self.sustained_ms[index][1]
        return None

    def held_melody_end(self, time_ms):
        by_start = attrgetter("start_ms")
        begin = bisect_left(self.melody_notes, time_ms - 50, key=by_start)
        stop = bisect_right(self.melody_notes, time_ms + 50, lo=begin, key=by_start)
        return max((note.end_ms for note in self.melody_notes[begin:stop]), default=None)


@dataclass(frozen=True)
class BeatGrid:
    beats_ms: tuple[int, ...]
    intervals: tuple[tuple[int, int], ...]

    def interval_at(self, time_ms, margin_ms=0):
        index = bisect_right(self.intervals, (time_ms + margin_ms, float("inf"))) - 1
        if index >= 0 and time_ms <= self.intervals[index][1] + margin_ms:
            return self.intervals[index]
        return None

    def beat_length(self, time_ms):
        interval = self.interval_at(time_ms) or self.interval_at(time_ms, 45)
        return interval[1] - interval[0] if interval else None


def beat_grid(beats):
    """Fit a steady clock to each run of similar beat lengths."""
    gaps = tuple(end - start for start, end in pairwise(beats))
    intervals = []
    snapped = set(beats)
    index = 0
    while index < len(gaps):
        first = index
        length = gaps[index]
        index += 1
        if not 200 <= length <= 1000:
            continue
        lengths = [length]
        steps = 1
        low_period, high_period = length - BEAT_FIT_TOLERANCE_MS, length + BEAT_FIT_TOLERANCE_MS
        while index < len(gaps):
            period = median(lengths[-5:])
            tolerance = max(35, period * 0.12)
            gap = gaps[index]
            step = 1
            if abs(gap - period) > tolerance:
                step = round(gap / period)
                # Bridge a missed detection only if the original pulse resumes immediately.
                if not (len(lengths) >= 2 and 2 <= step <= 3 and abs(gap - step * period) <= tolerance
                        and index + 1 < len(gaps) and abs(gaps[index + 1] - period) <= tolerance):
                    break
            # Limit the phase correction on every measured beat in the section.
            elapsed = beats[index + 1] - beats[first]
            sample_period = elapsed / (steps + step)
            if not low_period <= sample_period <= high_period:
                break
            if step == 1:
                lengths.append(gap)
            steps += step
            low_period = max(low_period, (elapsed - BEAT_FIT_TOLERANCE_MS) / steps)
            high_period = min(high_period, (elapsed + BEAT_FIT_TOLERANCE_MS) / steps)
            index += 1
        if steps < 2 and len(gaps) > 1:
            continue
        start, end = beats[first], beats[index]
        ticks = tuple(start + round((end - start) * step / steps) for step in range(steps + 1))
        snapped.difference_update(beats[first:index + 1])
        snapped.update(ticks)
        intervals.extend(pairwise(ticks))
    return BeatGrid(tuple(sorted(snapped)), tuple(intervals))


def silent_ranges(levels, frame_ms):
    import numpy as np

    floor = max(0.0004, np.quantile(levels, 0.9) * 0.025) if len(levels) else 0.0004
    changes = np.flatnonzero(np.diff(np.pad(levels < floor, (1, 1))))
    return tuple((round(start * frame_ms), round(end * frame_ms))
                 for start, end in zip(changes[::2], changes[1::2]) if (end - start) * frame_ms >= 180)


def level_attacks(levels, floor):
    import numpy as np

    audible = np.pad(levels >= floor, (2, 0))
    return np.flatnonzero(audible[2:] & ~audible[1:-1] & ~audible[:-2])


def align_audio_attacks(onsets, levels, frame_ms):
    """Recover attacks from silence and use them to refine nearby spectral estimates."""
    import numpy as np

    floor = max(0.001, np.quantile(levels, 0.9) * 0.08) if len(levels) else 0.001
    rises = tuple(round((frame + 0.5) * frame_ms) for frame in level_attacks(levels, floor))
    aligned = []
    for time_ms, strength in onsets:
        index = bisect_left(rises, time_ms)
        nearby = rises[max(0, index - 1):index + 1]
        closest = min(nearby, key=lambda rise: abs(rise - time_ms), default=None)
        if closest is not None and abs(closest - time_ms) <= 50:
            aligned.append((closest, strength))
            continue
        frame = int(time_ms / frame_ms)
        window = levels[max(0, frame - 2):frame + 5]
        peak = max(window, default=0)
        # Spectral flux can fire periodically on an otherwise steady held tone.
        if peak >= floor and peak - min(window) >= peak * 0.035:
            aligned.append((time_ms, strength))
    return tuple(sorted(set(rises) | {time_ms for time_ms, _ in aligned})), aligned


def sustained_sounds(onsets, levels, frame_ms):
    """Find sounds whose level continues beyond the initial attack."""
    if len(levels) < 3:
        return ()

    import numpy as np

    floor = max(0.002, np.quantile(levels, 0.9) * 0.12)
    # A clean held tone can start from silence without registering a spectral onset.
    attacks = set(onsets)
    for frame in level_attacks(levels, floor):
        time_ms = round(frame * frame_ms)
        if not AudioRhythm._near(onsets, time_ms, 70):
            attacks.add(time_ms)
    attacks = sorted(attacks)
    ends = (*attacks[1:], round(len(levels) * frame_ms))
    sustained = []
    for start, limit in zip(attacks, ends):
        if limit - start < 400:
            continue
        first = int(start / frame_ms) + 2
        stop = int(limit / frame_ms)
        active = levels[first:stop]
        reference = np.median(active[:5])
        if reference < floor:
            continue
        quiet = active < max(floor, reference * 0.45)
        pauses = np.flatnonzero(quiet[:-2] & quiet[1:-1] & quiet[2:])
        end = round((first + pauses[0] if len(pauses) else stop) * frame_ms)
        if end - start >= 400:
            sustained.append((start, end))
    return tuple(sustained)


def analyze_audio_rhythm(path, cancelled):
    if cancelled.is_set():
        return AudioRhythm()

    import aubio
    import numpy as np
    from pedalboard.io import AudioFile

    from core.rhythm_melody import MelodyAnalyzer

    rate, hop = 22050, 512
    onset = aubio.onset("specflux", hop * 2, hop, rate)
    tempo = aubio.tempo("default", hop * 2, hop, rate)
    melody = MelodyAnalyzer(rate, hop)
    beats, onsets, levels = [], [], []
    with AudioFile(str(path)).resampled_to(rate) as audio:
        while not cancelled.is_set():
            samples = audio.read(hop * 128)
            if samples.shape[1] == 0:
                break
            mono = np.mean(samples, axis=0, dtype=np.float32)
            # The stereo difference helps reveal panned pitches under center vocals.
            side = (samples[0] - samples[1]) * 0.5 if samples.shape[0] == 2 else None
            melody.feed(mono, side)
            frame_count = len(mono) // hop
            frames = samples[:, :frame_count * hop].reshape(samples.shape[0], frame_count, hop)
            levels.extend(np.sqrt(np.mean(frames * frames, axis=(0, 2))))
            for start in range(0, frame_count * hop, hop):
                frame = mono[start:start + hop]
                if tempo(frame)[0] and tempo.get_confidence() >= 0.1:
                    beats.append(round(tempo.get_last_ms()))
                if onset(frame)[0]:
                    strength = float(onset.get_descriptor())
                    time_ms = round(onset.get_last_ms())
                    if time_ms >= 0 and np.isfinite(strength) and strength > 0:
                        onsets.append((time_ms, strength))

    if cancelled.is_set():
        return AudioRhythm()
    levels = np.asarray(levels)
    frame_ms = hop * 1000 / rate
    onset_times, onsets = align_audio_attacks(onsets, levels, frame_ms)
    threshold = np.quantile([strength for _, strength in onsets], 0.75) if onsets else 0
    melody_notes = melody.finish(cancelled)
    if cancelled.is_set():
        return AudioRhythm()
    return AudioRhythm(
        beats_ms=tuple(sorted({time_ms for time_ms in beats if time_ms >= 0})),
        accents_ms=tuple(sorted({time_ms for time_ms, strength in onsets if strength >= threshold})),
        onsets_ms=onset_times,
        sustained_ms=sustained_sounds(onset_times, levels, frame_ms),
        silence_ms=silent_ranges(levels, frame_ms),
        melody_notes=melody_notes,
    )
