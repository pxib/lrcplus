"""Exercise visual line breaks through loading, reading generation and Qt layout."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from core.fonts import library_font
from lyrics.furigana_widget import FuriganaWidget
from lyrics.loader import load_lyrics
from lyrics.scrolling_widget import ScrollingLyricsWidget


@pytest.mark.parametrize("suffix", [".lrcx", ".lrc"])
@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("wrap", [False, True])
def test_loaded_newline_puts_lol_on_its_own_rendered_row(qapp, tmp_path, monkeypatch, suffix, timed, wrap):
    path = tmp_path / ("song" + suffix)
    boundary = "<00:01.500>" if timed else ""
    path.write_text(
        "[format:LRCX]\n[version:1]\n[g:auto]\n"
        "[00:00.650]ワタシ 超最強 超最強{\\n}" + boundary + "LOL!\n"
        "[00:03.050]超最強 な～んです\n", encoding="utf-8",
    )
    line = load_lyrics(path)[0]
    assert line.text == "ワタシ 超最強 超最強\nLOL!"
    tokens = [
        {"text": "ワタシ ", "reading": None},
        {"text": "超最強", "reading": "ちょうさいきょう"},
        {"text": " ", "reading": None},
        {"text": "超最強", "reading": "ちょうさいきょう"},
        {"text": "\nLOL!", "reading": None},
    ]
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: tokens)
    widget = FuriganaWidget()
    try:
        widget.resize(1200, 300)
        widget.set_font(library_font(qapp.font()).family(), size=24, ruby_size=12)
        widget.setStyleSheet("background-color: #202020;")
        widget.set_alignment("left")
        widget.set_text_wrapping(wrap, 1)
        widget.set_segments(line.text, line.segments, line.end)
        pieces = widget._prepare_segments()
        lol = next(piece for piece in pieces if piece["text"] == "LOL!")
        assert lol["base_y"] > pieces[0]["base_y"]
        assert lol["x"] == 0
        assert all("\n" not in piece["text"] for piece in pieces)
        assert "".join(piece["text"] for piece in pieces) == line.text.replace("\n", "")
        if timed:
            assert lol["start"] == 1.5
            assert lol["timing_group"] != pieces[0]["timing_group"]
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        assert image.save(str(tmp_path / "newline-render.png"))
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("text", ["First\nSecond", "First\n\nSecond"])
def test_plain_lyric_breaks_work_without_readings_or_wrapping(qapp, text):
    widget = FuriganaWidget()
    try:
        widget.resize(900, 300)
        widget.set_lyric(text, generate=False)
        pieces = widget._prepare_segments()
        assert [piece["text"] for piece in pieces] == ["First", "Second"]
        stride = widget._content_metrics()[3]
        assert pieces[1]["base_y"] - pieces[0]["base_y"] == stride * text.count("\n")
        assert widget.minimumSizeHint().height() == stride * (text.count("\n") + 1)
    finally:
        widget.deleteLater()


def test_reading_batches_keep_multiline_lyrics_and_following_lyrics_separate(monkeypatch):
    import lyrics.furigana as readings

    monkeypatch.setattr(readings, "get_reading_mode", lambda: "furigana")
    monkeypatch.setattr(readings, "get_active_parser", lambda: "test")
    monkeypatch.setattr(readings, "get_cached_furigana_segments", lambda text: None)
    monkeypatch.setattr(readings, "_analyze", lambda text: [
        {"surface": part, "reading": ""} for part in text.split("\n") if part
    ])
    cached = {}
    monkeypatch.setattr(readings, "_cache_furigana_segments", lambda text, segments: cached.update({text: segments}))
    lines = ["ワタシ 超最強\n\nLOL!", "超最強 な～んです"]
    assert readings.prefetch_furigana_batches(lines, force=True) == (2, 2)
    for text in lines:
        assert "".join(segment["text"] for segment in cached[text]) == text


def test_vertical_newline_starts_a_new_column(qapp):
    widget = FuriganaWidget()
    try:
        widget.resize(500, 500)
        widget.set_direction("vertical")
        widget.set_lyric("First\nSecond", generate=False)
        pieces = widget._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == "FirstSecond"
        assert pieces[-1]["_vertical_column"] > pieces[0]["_vertical_column"]
        assert len(widget._vertical_column_ruby_flags()) == 2
    finally:
        widget.deleteLater()


def test_multiline_karaoke_uses_each_rows_visual_span(qapp, monkeypatch):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": None}])
    widget = FuriganaWidget()
    try:
        widget.resize(900, 300)
        widget.set_alignment("left")
        widget.set_segments("A long first row\nLOL!", [
            {"text": "A long first row\nLOL!", "start": 0.65, "end": 3.05},
        ], 3.05)
        pieces = widget._prepare_segments()
        assert len({piece["timing_group"] for piece in pieces}) == 1
        assert pieces[0]["karaoke_width"] == pieces[0]["width"]
        assert pieces[1]["karaoke_width"] == pieces[1]["width"]
    finally:
        widget.deleteLater()


def test_scrolling_rows_reserve_height_for_all_visual_lines(qapp, monkeypatch):
    from lyrics.models import LyricLine

    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": None}])
    widget = ScrollingLyricsWidget()
    try:
        widget.resize(900, 500)
        widget.show()
        widget.set_lyrics([LyricLine(0.65, "First\nLOL!"), LyricLine(3.05, "Next lyric")])
        qapp.processEvents()
        first = widget._mounted_rows[0]
        second = widget._mounted_rows[1]
        assert first.height() > second.height()
        assert widget._line_start(1) >= first.height()
        assert first._prepare_segments()[-1]["base_y"] < first.height()
    finally:
        widget.clear()
        widget.close()
        widget.deleteLater()
