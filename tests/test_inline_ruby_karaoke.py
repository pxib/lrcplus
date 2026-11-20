from types import SimpleNamespace

import pytest

from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx
from lyrics.lrcx_parser import _parse_content, parse_lrcx
from ui.lrc_editor import LrcEditorDialog


def test_inline_ruby_does_not_split_karaoke_chunk():
    line = LyricLine(
        start=41.341,
        text='描き',
        segments=[LyricSegment(41.922, '描き')],
        has_karaoke=True,
    )
    line.segments[0].ruby_spans = [(0, 1, 'が')]
    saved = serialize_lrcx([line])
    assert '<00:41.922>{描|が}き' in saved
    assert '<00:41.922>{描|が}<00:41.922>き' not in saved


def test_inline_ruby_load_keeps_one_chunk():
    segments = _parse_content('{描|が}き', 41.922)
    assert len(segments) == 1
    assert segments[0].text == '描き'
    assert segments[0].ruby_spans == [(0, 1, 'が')]


def test_authored_inline_ruby_requires_lrcx_but_generated_ruby_does_not():
    line = LyricLine(
        start=0,
        text='描き',
        segments=[LyricSegment(0, '描き')],
        has_karaoke=True,
    )
    dialog = SimpleNamespace(lyrics_offset_ms=0, lines=[line])

    line.segments[0].ruby_spans = [(0, 1, 'が')]
    assert 'Furigana' in LrcEditorDialog._lrcx_only_features(dialog)

    line.segments[0].ruby_spans = []
    line.segments[0].ruby = 'えが'
    line.segments[0].ruby_source = 'generated'
    assert 'Furigana' not in LrcEditorDialog._lrcx_only_features(dialog)


def test_mora_timing_round_trip_preserves_karaoke_chunks():
    line, = parse_lrcx("[00:12.000]**{穴|あ<00:12.650>な}**<00:12.900>へ<00:13.100>")
    timed, following = line.segments
    assert (timed.text, timed.ruby, following.start) == ("穴", "あな", 12.9)
    assert timed.ruby_spans == [(0, 1, "あな")]
    offset, elapsed = timed.mora_cues[0]
    assert offset == 1
    assert elapsed == pytest.approx(0.65)
    assert parse_lrcx(serialize_lrcx([line])) == [line]


@pytest.mark.parametrize("reading", [
    "あ<00:12.650>な",
    "<00:12.000>あ<00:12.650>な",
    "<00:12.200>あ<00:12.650>な",
    "<00:12.000>あ<00:12.350><00:12.650>な",
])
def test_enter_mora_timing_in_karaoke_ruby_column(editor, reading):
    line, = parse_lrcx("[00:12.000]{穴|あな}<00:12.900>", infer_ends=False)
    editor.lines = [line]
    editor._populate_lines(preserve_line_index=0)
    item = editor.segment_table.item(0, 3)
    item.setText(reading)
    assert line.segments[0].ruby_spans == [(0, 1, "あな")]
    serialized = serialize_lrcx([line])
    assert f"{{穴|{reading}}}" in serialized
    assert parse_lrcx(serialized, infer_ends=False) == [line]
    assert editor.save_document()
    restored, = parse_lrcx(editor.lrc_path.read_text(encoding="utf-8"), infer_ends=False)
    assert restored.segments[0].mora_cues == line.segments[0].mora_cues
