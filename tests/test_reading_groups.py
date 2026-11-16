import json

from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx
from lyrics.lrcx_parser import parse_lrcx
from lyrics.editor_source import parse_editor_source
from lyrics.editor_operations import split_lyric_line, merge_lyric_lines


def test_groups_roundtrip_by_line_occurrence_and_source_metadata():
    lines = [LyricLine(1, "連帯", reading_groups=[(0, 2)]), LyricLine(1, "連帯")]
    contents = serialize_lrcx(lines)
    result = parse_editor_source(contents, ".lrcx")
    assert result.lines[0].reading_groups == [(0, 2)]
    assert not result.lines[1].reading_groups
    assert not result.metadata
    assert serialize_lrcx(result.lines).count("[reading_groups:") == 1


def test_invalid_or_stale_group_metadata_is_ignored():
    entries = [[0, 2, "different"], [-1, 2, ""], [0, 99, "連帯"], [True, 2, "帯"], "bad"]
    contents = "[reading_groups:" + json.dumps({"0": entries}) + "]\n[00:01.000]連帯\n"
    assert not parse_lrcx(contents)[0].reading_groups
    assert not parse_lrcx("[reading_groups:bad]\n[00:01.000]連帯\n")[0].reading_groups


def test_split_and_merge_move_word_groups_with_their_text():
    line = LyricLine(1, "前連帯後", [LyricSegment(1, "前連帯後", end=5)], end=5,
                    reading_groups=[(1, 3)])
    left, right = split_lyric_line(line, 1)
    assert not left.reading_groups
    assert right.reading_groups == [(0, 2)]
    merged = merge_lyric_lines([left, right], separator="")
    assert merged.text == line.text
    assert merged.reading_groups == [(1, 3)]
    left, right = split_lyric_line(line, 2)
    assert not left.reading_groups and not right.reading_groups
