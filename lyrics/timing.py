"""Timing operations independent of the editor and playback widgets."""

from copy import deepcopy
from dataclasses import dataclass
import math
import re


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def parse_timestamp(value):
    """Parse seconds, MM:SS.fraction, or HH:MM:SS.fraction for table edits."""
    text = str(value).strip()
    if not re.fullmatch(r"(?:\d+:){0,2}\d+(?:\.\d+)?", text):
        raise ValueError("Use seconds, MM:SS.mmm, or HH:MM:SS.mmm.")
    parts = text.split(":")
    numbers = [float(part) for part in parts]
    if len(parts) > 1 and numbers[-1] >= 60:
        raise ValueError("Seconds must be less than 60 in a timestamp.")
    if len(parts) == 3 and numbers[-2] >= 60:
        raise ValueError("Minutes must be less than 60 in an hour timestamp.")
    result = 0.0
    for number in numbers:
        result = result * 60 + number
    if not math.isfinite(result):
        raise ValueError("Timestamp must be finite.")
    return result


def shift_timing(lines, delta_ms, *, first=0, last=None):
    """Return a shifted copy of an inclusive range, preserving all annotations.

    Reject the whole operation if any affected timestamp would be invalid.
    The original document is never mutated, including on failure.
    """
    if not _finite(delta_ms):
        raise ValueError("The timing shift must be a finite number of milliseconds.")
    last = len(lines) - 1 if last is None else last
    if not 0 <= first <= last < len(lines):
        raise ValueError("Choose a valid range of lyric lines.")
    shifted = deepcopy(lines)
    delta = delta_ms / 1000.0

    def move(value):
        if not _finite(value):
            raise ValueError("Fix invalid timestamps before shifting these lines.")
        result = round(value + delta, 9)
        if result < 0:
            raise ValueError("This shift would move a timestamp before 00:00.000.")
        return result

    for line in shifted[first:last + 1]:
        line.start = move(line.start)
        if line.end is not None:
            line.end = move(line.end)
        for segment in line.segments:
            segment.start = move(segment.start)
            if segment.end is not None:
                segment.end = move(segment.end)
    return shifted


@dataclass(frozen=True)
class TimingIssue:
    line_index: int
    message: str


def validate_timing(lines, *, duration_ms=None):
    """Report suspicious timing without changing or sorting the document.

    Simultaneous lyric lines and separate visual karaoke rows are supported.
    Overlaps are reported for review, since they can be intentional.
    """
    issues = []
    duration = duration_ms / 1000.0 if _finite(duration_ms) and duration_ms > 0 else None

    for index, line in enumerate(lines):
        def report(message):
            issues.append(TimingIssue(index, message))

        def valid(value, label):
            if not _finite(value) or value < 0:
                report(f"{label} must be a finite, non-negative timestamp.")
                return False
            if duration is not None and value > duration + 0.001:
                report(f"{label} is beyond the audio duration.")
            return True

        start_ok = valid(line.start, "Line start")
        end_ok = line.end is not None and valid(line.end, "Line end")
        if start_ok and end_ok and line.end < line.start:
            report("Line end is before its start.")
        if index and start_ok and _finite(lines[index - 1].start) and line.start < lines[index - 1].start:
            report("Line starts before the previous line in source order.")
        if end_ok and index + 1 < len(lines) and _finite(lines[index + 1].start):
            if line.end > lines[index + 1].start:
                report("Line overlaps the next line; check whether this is intentional.")

        previous_by_row = {}
        for number, segment in enumerate(line.segments, 1):
            label = f"Segment {number}"
            segment_start_ok = valid(segment.start, f"{label} start")
            segment_end_ok = segment.end is not None and valid(segment.end, f"{label} end")
            if segment_start_ok and start_ok and segment.start < line.start:
                report(f"{label} starts before the lyric line.")
            if segment_end_ok and segment_start_ok and segment.end < segment.start:
                report(f"{label} end is before its start.")
            if end_ok:
                if segment_start_ok and segment.start > line.end:
                    report(f"{label} starts after the lyric line ends.")
                if segment_end_ok and segment.end > line.end:
                    report(f"{label} ends after the lyric line ends.")
            previous = previous_by_row.get(segment.visual_line)
            if previous is not None and segment_start_ok:
                if _finite(previous.start) and segment.start < previous.start:
                    report(f"{label} starts before the previous segment on its visual row.")
                elif _finite(previous.end) and previous.end > segment.start:
                    report(f"{label} overlaps the previous segment on its visual row.")
            previous_by_row[segment.visual_line] = segment
        if line.has_karaoke and "".join(segment.text for segment in line.segments) != line.text:
            report("Karaoke segment text does not match the lyric text.")
    return issues
