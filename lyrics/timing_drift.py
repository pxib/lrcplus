"""Estimate a whole-song timing correction from a timed copy of the lyrics."""

from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
from statistics import median
import unicodedata

from lyrics.loader import load_lyrics
from lyrics.lrcx_parser import get_lrcx_offset, load_lrcx
from lyrics.models import LyricLine
from lyrics.timing import map_timing


MAX_TIMING_REFERENCE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class TimingMatch:
    line_index: int
    original: float
    reference: float


@dataclass(frozen=True)
class TimingCorrection:
    scale: float
    intercept: float
    matches: tuple[TimingMatch, ...]
    max_residual: float

    def audio_time(self, seconds):
        return self.intercept + self.scale * seconds

    def apply(self, lines, *, offset_ms=0, duration_ms=0):
        """Return corrected lines and their playback offset in milliseconds."""
        duration = duration_ms / 1000

        def remap(offset):
            def move(seconds):
                audio_time = round(self.audio_time(seconds), 9)
                if (not math.isfinite(audio_time) or audio_time < 0
                        or (duration > 0 and audio_time > duration + 0.001)):
                    raise ValueError("The correction would place a timestamp outside the recording or before 00:00.")
                result = round(audio_time - offset / 1000, 9)
                if result < 0:
                    raise _PlaybackOffsetTooLarge
                return result

            return map_timing(lines, move, duration_scale=self.scale), offset

        try:
            return remap(offset_ms)
        except _PlaybackOffsetTooLarge:
            # Valid audio times can still be negative in the document's
            # coordinates. Store them directly when the old offset is too large.
            return remap(0)


class _PlaybackOffsetTooLarge(Exception):
    pass


def sung_intervals(lines):
    """Return sung intervals, keeping authored ends and inferring missing ones."""
    timed = sorted((line.start, index, line.end) for index, line in enumerate(lines) if line.text.strip())
    starts = sorted({start for start, _, _ in timed})
    if len(starts) < 4 or not all(map(math.isfinite, starts)) or starts[0] < 0 or starts[-1] - starts[0] < 10:
        raise ValueError("Automatic correction needs at least four timed lines spread across the song.")
    spacing = median(b - a for a, b in zip(starts, starts[1:]))
    following = dict(zip(starts, starts[1:] + [starts[-1] + spacing]))
    return [(index, start, end if end is not None and math.isfinite(end) and end > start
             else min(following[start], start + 1.2 * spacing)) for start, index, end in timed]


def _key(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized
                   if char.isalnum() or unicodedata.category(char).startswith("M"))


def _anchors(lines):
    occurrences = defaultdict(dict)
    for index, line in enumerate(lines):
        if not math.isfinite(line.start) or line.start < 0:
            continue
        sung_rows = [row for number, row in enumerate(line.text.splitlines())
                     if number not in line.translation_lines and row.strip()]
        if not sung_rows:
            continue
        # A caption can wrap one sung phrase or put two phrases in one cue.
        # Its timestamp corresponds to the first phrase's onset.
        for key in {_key(sung_rows[0]), _key(" ".join(sung_rows))}:
            if len(key) >= 3:
                occurrences[key].setdefault(line.start, index)
    return occurrences


def estimate_timing_correction(lines, reference, *, offset_ms=0, reference_offset_ms=0):
    """Fit and verify one shift and speed adjustment using unique lyric lines."""
    source = _anchors(lines)
    target = _anchors(reference)
    reference_offset = reference_offset_ms / 1000
    matches_by_time = defaultdict(set)
    indices = {}
    for key, occurrences in source.items():
        other = target.get(key)
        if len(occurrences) != 1 or other is None or len(other) != 1:
            continue
        original, index = next(iter(occurrences.items()))
        matched = next(iter(other)) + reference_offset
        if math.isfinite(matched) and matched >= 0:
            matches_by_time[original].add(matched)
            indices.setdefault((original, matched), index)
    matches = tuple(
        TimingMatch(indices[original, matched], original, matched)
        for original, values in sorted(matches_by_time.items()) if len(values) == 1
        for matched in values
    )
    if len(matches) < 4:
        raise ValueError("Too few unique lyric lines match the reference. Check the recording and lyric language.")

    first, last = matches[0], matches[-1]
    source_times = [time for occurrences in source.values() for time in occurrences]
    start, end = min(source_times), max(source_times)
    span = end - start
    matched_span = last.original - first.original
    if (matched_span < 10 or matched_span < .60 * span
            or first.original - start > span * .25 or end - last.original > span * .25):
        raise ValueError("Matches need to cover the beginning and end of the lyrics.")
    if not any(first.original + .25 * matched_span <= match.original <= last.original - .25 * matched_span
               for match in matches[1:-1]):
        raise ValueError("The reference needs a matching line in the middle to verify the drift.")
    if any(left.reference >= right.reference for left, right in zip(matches, matches[1:])):
        raise ValueError("Matching lines occur in a different order. Check the recording version.")

    mean_original = math.fsum(match.original for match in matches) / len(matches)
    mean_reference = math.fsum(match.reference for match in matches) / len(matches)
    scale = (
        math.fsum((match.original - mean_original) * (match.reference - mean_reference) for match in matches)
        / math.fsum((match.original - mean_original) ** 2 for match in matches)
    )
    intercept = mean_reference - scale * mean_original
    max_residual = max(abs(intercept + scale * match.original - match.reference) for match in matches)
    if max_residual > .45:
        raise ValueError("The timing difference varies across the song. Review the lines individually.")
    if max(abs(intercept + (scale - 1) * time - offset_ms / 1000) for time in (start, end)) < .08:
        return None
    return TimingCorrection(scale, intercept, matches, max_residual)


def load_timing_reference(path, *, automatic_captions=None):
    path = Path(path)
    if path.stat().st_size > MAX_TIMING_REFERENCE_BYTES:
        raise ValueError("Use a timed lyric or subtitle file smaller than 2 MiB.")
    if path.name.lower().endswith(".lrcx.bak"):
        return load_lrcx(path, infer_ends=False), get_lrcx_offset(path)
    if path.suffix.lower() in {".srt", ".vtt"}:
        from plugins.community.ytdlp_import.captions import load_caption_cues

        if automatic_captions is None:
            automatic_captions = path.stem.lower().endswith(".auto")
        cues = load_caption_cues(path, automatic=automatic_captions)
        return [LyricLine(cue.start / 1000, cue.text) for cue in cues], 0.0
    if path.suffix.lower() not in {".lrc", ".lrcx"}:
        raise ValueError("Choose a timed LRC, LRCX, SRT, or VTT reference.")
    return load_lyrics(path, infer_ends=False), get_lrcx_offset(path)
