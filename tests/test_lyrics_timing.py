from copy import deepcopy

import pytest

from lyrics.models import LyricLine, LyricSegment
from lyrics.timing import parse_timestamp, shift_timing, validate_timing


@pytest.mark.parametrize("value,expected", [("2.600", 2.6), ("00:02.600", 2.6), ("01:02:03.500", 3723.5), ("120:01.250", 7201.25)])
def test_editable_timestamp_formats(value, expected):
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "1:60", "1:60:00", "abc", "", "1:2:3:4"])
def test_invalid_timestamp_formats_are_rejected(value):
    with pytest.raises(ValueError):
        parse_timestamp(value)


def karaoke_line():
    return LyricLine(2.0, "Hello world", [
        LyricSegment(2.2, "Hello ", "reading", end=2.8, ruby_source="manual", ruby_spans=[(0, 5, "reading")]),
        LyricSegment(3.0, "world", instant=True, end=3.7),
    ], end=4.0, has_karaoke=True)


def test_shift_moves_line_and_segment_boundaries_preserving_pauses_and_ruby():
    original = [karaoke_line()]
    before = deepcopy(original)
    shifted = shift_timing(original, -1200)
    line = shifted[0]
    assert (line.start, line.end) == (0.8, 2.8)
    assert [(s.start, s.end) for s in line.segments] == [(1.0, 1.6), (1.8, 2.5)]
    assert line.segments[0].ruby_spans == [(0, 5, "reading")]
    assert line.segments[0].ruby_source == "manual"
    assert line.segments[1].instant
    assert line.text == original[0].text and line.has_karaoke
    assert original == before


def test_range_shift_changes_only_requested_source_lines():
    lines = [LyricLine(5, "source first"), LyricLine(1, "source second"), LyricLine(10, "source third")]
    result = shift_timing(lines, 500, first=1, last=1)
    assert [line.start for line in result] == [5, 1.5, 10]


@pytest.mark.parametrize("delta", [-2201, float("nan"), float("inf")])
def test_invalid_shift_leaves_original_untouched(delta):
    lines = [karaoke_line()]
    before = deepcopy(lines)
    with pytest.raises(ValueError):
        shift_timing(lines, delta)
    assert lines == before


def test_negative_segment_timestamp_rejects_entire_range_even_when_line_stays_positive():
    lines = [LyricLine(3, "early segment", [LyricSegment(0.1, "early segment")])]
    with pytest.raises(ValueError, match="before 00:00"):
        shift_timing(lines, -200)
    assert lines[0].start == 3


@pytest.mark.parametrize("first,last", [(2, 1), (-1, 0), (0, 5)])
def test_invalid_range_is_rejected(first, last):
    with pytest.raises(ValueError, match="range"):
        shift_timing([karaoke_line()], 100, first=first, last=last)


def test_valid_pauses_instant_segments_and_simultaneous_visual_rows_are_allowed():
    line = karaoke_line()
    line.segments.append(LyricSegment(2.2, "", end=2.8, visual_line=1))
    assert validate_timing([line], duration_ms=5000) == []


def test_validation_reports_source_order_and_end_before_start_without_mutating():
    lines = [LyricLine(4, "first"), LyricLine(2, "second", end=1)]
    before = deepcopy(lines)
    issues = validate_timing(lines)
    assert all(issue.line_index == 1 for issue in issues)
    assert any("source order" in issue.message for issue in issues)
    assert any("end is before" in issue.message for issue in issues)
    assert lines == before


def test_validation_finds_invalid_timestamps_audio_overrun_and_segment_overlap():
    line = karaoke_line()
    line.segments[1].start = 2.7
    issues = validate_timing([line, LyricLine(float("nan"), "invalid")], duration_ms=3500)
    messages = [issue.message for issue in issues]
    assert any("overlaps the previous segment" in message for message in messages)
    assert any("audio duration" in message for message in messages)
    assert any("finite, non-negative" in message for message in messages)


def test_validation_detects_segment_bounds_and_mismatched_karaoke_text():
    line = LyricLine(3, "missing", [LyricSegment(2, "different", end=6)], end=5, has_karaoke=True)
    messages = [issue.message for issue in validate_timing([line])]
    assert any("before the lyric line" in message for message in messages)
    assert any("after the lyric line" in message for message in messages)
    assert any("does not match" in message for message in messages)
