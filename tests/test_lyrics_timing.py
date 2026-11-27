from copy import deepcopy
from threading import Event

import numpy as np
import pytest

from lyrics.audio_timing_drift import estimate_audio_timing, sung_intervals
from lyrics.models import LyricLine, LyricSegment
from lyrics.timing import parse_timestamp, shift_timing, validate_timing
from lyrics.timing_drift import estimate_timing_correction


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


def test_drift_correction_preserves_document_offset_and_scales_karaoke_cues():
    lines = [karaoke_line(), LyricLine(12, "Second verse"), LyricLine(22, "Third verse"), LyricLine(32, "Final verse")]
    lines[0].segments[0].mora_cues = ((0, 0.25),)
    lines[0].text += "\nTranslation"
    lines[0].translation_lines = [1]
    original = deepcopy(lines)
    reference = [LyricLine(time * 1.04 + 0.5, text) for time, text in (
        (2, "Hello, WORLD!"), (12, "Second verse"), (22, "Third verse"), (32, "Final verse"),
    )]
    correction = estimate_timing_correction(lines, reference, offset_ms=200, reference_offset_ms=100)
    updated, offset = correction.apply(lines, offset_ms=200, duration_ms=35000)

    assert correction.scale == pytest.approx(1.04)
    assert offset == 200
    assert [line.start for line in updated] == pytest.approx([2.48, 12.88, 23.28, 33.68])
    assert updated[0].end == pytest.approx(4.56)
    assert updated[0].segments[0].start == pytest.approx(2.688)
    assert updated[0].segments[0].end == pytest.approx(3.312)
    assert updated[0].segments[0].mora_cues == ((0, 0.26),)
    assert updated[0].segments[0].ruby_spans == original[0].segments[0].ruby_spans
    assert updated[0].translation_lines == [1]
    assert lines == original
    with pytest.raises(ValueError, match="outside the recording"):
        correction.apply(lines, duration_ms=30000)


def test_drift_correction_can_replace_an_offset_larger_than_the_first_corrected_time():
    lines = [karaoke_line(), LyricLine(12, "Second verse"), LyricLine(22, "Third verse"), LyricLine(32, "Final verse")]
    correction = estimate_timing_correction(lines, lines, offset_ms=18400)
    corrected, offset = correction.apply(lines, offset_ms=18400, duration_ms=45000)
    assert offset == 0
    assert corrected == lines


def test_drift_rejects_repeated_or_uneven_matches_and_recognizes_existing_sync():
    lines = [LyricLine(time, f"Verse {time}") for time in (5, 15, 25, 35, 45)]
    reference = deepcopy(lines)
    assert estimate_timing_correction(lines, reference) is None
    reference[2].start += 2
    with pytest.raises(ValueError, match="varies across the song"):
        estimate_timing_correction(lines, reference)
    reference.extend(LyricLine(100 + line.start, line.text) for line in lines)
    with pytest.raises(ValueError, match="Too few unique lyric lines"):
        estimate_timing_correction(lines, reference)


def test_vocal_timing_fit_corrects_drift_and_is_stable_when_repeated(tmp_path, monkeypatch):
    from core import audio_timing_scan

    schedule = ((4, 5.45), (6, 11), (19, 22), (24, 29.2), (46, 51.2),
                (52, 55), (57, 64), (77, 78.9), (80, 85.4))
    lines = [LyricLine(start, f"Verse {number}", end=end)
             for number, (start, end) in enumerate(schedule)]
    time = np.arange(0, 125, .02)
    envelope = np.full(len(time), .001)
    for start, end in schedule:
        envelope[(time >= start * 1.21 + 8) & (time < end * 1.21 + 8)] = .75

    correction = estimate_audio_timing(sung_intervals(lines), envelope, offset_ms=6000)
    assert correction.scale == pytest.approx(1.21, abs=.002)
    assert correction.intercept == pytest.approx(8, abs=.04)
    updated, offset = correction.apply(lines, offset_ms=6000, duration_ms=125000)
    assert estimate_audio_timing(sung_intervals(updated), envelope, offset_ms=offset) is None
    audio = tmp_path / "song.wav"
    audio.touch()
    monkeypatch.setattr(audio_timing_scan, "_vocal_envelope",
                        lambda _path, output: np.save(output, envelope, allow_pickle=False))
    assert audio_timing_scan.correct_audio_timing(audio, lines, 6000, 0, Event()) == (updated, offset)
    assert audio_timing_scan.correct_audio_timing(audio, updated, offset, 0, Event()) is None
    with pytest.raises(ValueError, match="enough consistent timing cues"):
        estimate_audio_timing(sung_intervals(lines), np.full(len(time), .75), offset_ms=6000)
    envelope.fill(.75)
    with pytest.raises(ValueError, match="enough consistent timing cues"):
        audio_timing_scan.correct_audio_timing(audio, lines, 6000, 0, Event())


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


@pytest.mark.parametrize("offset_ms,start,end,expected_issue", [
    (500, 9.8, 9.9, "audio duration"),
    (-500, 10.2, 10.4, None),
    (-500, 0.2, 0.4, "before the audio"),
    (500, 0, 9.5, None),
    (-500, 0.5, 10.5, None),
])
def test_validation_checks_playback_bounds_with_offset_without_changing_lyrics(
    offset_ms, start, end, expected_issue,
):
    lines = [LyricLine(start, "Timed", [LyricSegment(start, "Timed", end=end)], end=end)]
    before = deepcopy(lines)
    issues = validate_timing(lines, duration_ms=10000, offset_ms=offset_ms)
    if expected_issue:
        assert any(expected_issue in issue.message for issue in issues)
    else:
        assert issues == []
    assert lines == before
