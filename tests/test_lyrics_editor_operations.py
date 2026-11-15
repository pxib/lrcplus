"""Model-level guarantees for corrections, splitting, merging and bulk styles."""

from copy import deepcopy

import pytest

from lyrics.editor_operations import correct_line_text, merge_lyric_lines, set_line_format, split_lyric_line
from lyrics.formatting import style_at
from lyrics.karaoke_timing import segment_end
from lyrics.lrcx_parser import parse_lrcx
from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx


def timed_line():
    return LyricLine(1, "Hello world", [
        LyricSegment(1.2, "Hello ", end=2, ruby_spans=[(0, 5, "first")]),
        LyricSegment(2.5, "world", end=3.5, ruby="second", ruby_source="manual"),
    ], end=4, has_karaoke=True, style_spans=[(0, 5, True, False, False, False)])


@pytest.mark.parametrize("text", ["Hello, world!", "Hello WORLD", "Hello  world", "Hello\nworld"])
def test_corrections_keep_chunk_times_and_unaffected_readings(text):
    line = timed_line()
    before = deepcopy(line)
    chunks = correct_line_text(line, text)
    assert "".join(segment.text for segment in chunks) == text
    assert [(s.start, s.end) for s in chunks] == [(1.2, 2), (2.5, 3.5)]
    assert chunks[0].ruby_spans == [(0, 5, "first")]
    assert not any(s.needs_timing_review for s in chunks)
    assert line == before


def test_word_correction_flags_only_changed_chunk_and_keeps_other_ruby():
    line = timed_line()
    chunks = correct_line_text(line, "Hello worlds")
    assert chunks[0] == line.segments[0]
    assert not chunks[0].needs_timing_review
    assert chunks[1].needs_timing_review
    assert (chunks[1].start, chunks[1].end) == (2.5, 3.5)
    assert chunks[1].ruby_spans == [(0, 5, "second")]


def test_changing_ruby_base_drops_only_that_reading():
    line = LyricLine(1, "AB CD", [LyricSegment(1, "AB CD", ruby_spans=[(0, 2, "first"), (3, 5, "second")])],
                     has_karaoke=True)
    chunks = correct_line_text(line, "AX CD")
    assert chunks[0].ruby_spans == [(3, 5, "second")]
    assert chunks[0].needs_timing_review


@pytest.mark.parametrize("text", ["New whole sentence", "world", "", "🎵Hello 世界", "Hello brave world"])
def test_replacement_insertion_and_deletion_always_reconstruct_exact_text(text):
    line = timed_line()
    chunks = correct_line_text(line, text)
    assert "".join(segment.text for segment in chunks) == text
    assert all(segment.start in (1, 1.2, 2.5) for segment in chunks)


def test_split_at_existing_chunk_boundary_keeps_pauses_ruby_and_styles():
    line = timed_line()
    before = deepcopy(line)
    left, right = split_lyric_line(line, 6)
    assert (left.text, right.text) == ("Hello ", "world")
    assert (left.start, left.end, right.start, right.end) == (1, 2, 2.5, 4)
    assert left.segments == [line.segments[0]] and right.segments == [line.segments[1]]
    assert left.style_spans == line.style_spans and right.style_spans == []
    assert line == before
    reopened = parse_lrcx(serialize_lrcx([left, right]), infer_ends=False)
    assert [(item.start, item.text, item.end) for item in reopened] == [(1, "Hello ", 2), (2.5, "world", 4)]
    assert reopened[1].segments[0].ruby == "second"


def test_split_inside_chunk_partitions_timing_and_keeps_both_ruby_slices():
    line = LyricLine(1, "ABCD", [LyricSegment(1, "ABCD", end=5, ruby="abcd", ruby_source="manual")],
                     end=6, has_karaoke=True, style_spans=[(1, 4, True, False, False, False)])
    left, right = split_lyric_line(line, 2)
    assert (left.segments[0].start, left.segments[0].end) == (1, 3)
    assert (right.start, right.segments[0].start, right.segments[0].end) == (3, 3, 5)
    assert left.segments[0].ruby == "ab" and right.segments[0].ruby == "cd"
    assert left.segments[0].needs_timing_review and right.segments[0].needs_timing_review
    assert left.style_spans == [(1, 2, True, False, False, False)]
    assert right.style_spans == [(0, 2, True, False, False, False)]


def test_plain_line_split_uses_next_timestamp_without_enabling_karaoke():
    left, right = split_lyric_line(LyricLine(2, "ABCD"), 2, fallback_end=6)
    assert (left.start, right.start) == (2, 4)
    assert not left.has_karaoke and not right.has_karaoke
    assert right.segments[0].start == 4


@pytest.mark.parametrize("position", [0, 11, -1])
def test_invalid_split_does_not_change_model(position):
    line = timed_line()
    before = deepcopy(line)
    with pytest.raises(ValueError):
        split_lyric_line(line, position)
    assert line == before


def test_split_inside_translation_retains_translation_marker_on_save():
    line = LyricLine(1, "AB\nCD", [LyricSegment(1, "AB\nCD", end=5)],
                     end=5, has_karaoke=True, translation_lines=[1])
    left, right = split_lyric_line(line, 4)
    assert right.text == "\nD" and right.translation_lines == [1]
    reopened = parse_lrcx(serialize_lrcx([left, right]), infer_ends=False)
    assert reopened[0].translation_lines == [1]
    assert reopened[1].translation_lines == [1]
    assert reopened[1].text == "\nD"


@pytest.mark.parametrize("separator", [" ", "\n", ""])
def test_merge_keeps_absolute_word_times_ruby_and_formatting(separator):
    first = timed_line()
    second = LyricLine(6, "End", [LyricSegment(6.2, "End", end=7, instant=True)],
                       end=8, has_karaoke=True, style_spans=[(0, 3, False, True, False, False)])
    before = deepcopy([first, second])
    merged = merge_lyric_lines([first, second], separator)
    assert merged.text == "Hello world" + separator + "End"
    assert (merged.start, merged.end) == (1, 8)
    visible = [s for s in merged.segments if s.text.strip()]
    assert [(s.start, s.end) for s in visible] == [(1.2, 2), (2.5, 3.5), (6.2, 7)]
    assert visible[0].ruby_spans == [(0, 5, "first")]
    assert visible[1].ruby == "second" and visible[-1].instant
    assert style_at(merged.style_spans, len(merged.text) - 1)[1]
    reopened = parse_lrcx(serialize_lrcx([merged]), infer_ends=False)[0]
    assert reopened.text == merged.text
    assert [(s.start, segment_end(reopened, row)) for row, s in enumerate(reopened.segments) if s.text.strip()] == [
        (s.start, segment_end(merged, row)) for row, s in enumerate(merged.segments) if s.text.strip()
    ]
    assert [first, second] == before


def test_merging_plain_lines_makes_their_original_times_karaoke_boundaries():
    merged = merge_lyric_lines([LyricLine(1, "First"), LyricLine(4, "Second")])
    assert merged.has_karaoke
    reopened = parse_lrcx(serialize_lrcx([merged]), infer_ends=False)[0]
    assert reopened.text == "First Second"
    assert next(s for s in reopened.segments if s.text == "Second").start == 4


def test_merge_translation_rows_keeps_them_separate():
    first = LyricLine(1, "AB\nCD", translation_lines=[1])
    second = LyricLine(4, "EF\nGH", translation_lines=[1])
    merged = merge_lyric_lines([first, second], "\n")
    assert merged.translation_lines == [1, 3]
    assert parse_lrcx(serialize_lrcx([merged]))[0].translation_lines == [1, 3]
    with pytest.raises(ValueError, match="Newline"):
        merge_lyric_lines([first, second], " ")


def test_bulk_format_removal_keeps_unrelated_styles_and_timing():
    line = timed_line()
    before = deepcopy(line.segments)
    set_line_format(line, ["italic", "quote"], True)
    assert style_at(line.style_spans, 1)[:2] == (True, True)
    assert line.is_blockquote
    set_line_format(line, ["bold"], False)
    assert style_at(line.style_spans, 1)[:2] == (False, True)
    assert line.segments == before
