from types import SimpleNamespace

from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx
from lyrics.lrcx_parser import _parse_content
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
