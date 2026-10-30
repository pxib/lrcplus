from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from core.fonts import library_font
from lyrics.furigana_widget import FuriganaWidget
from lyrics.loader import load_lyrics
from lyrics.writer import serialize_lrc, serialize_lrcx


@pytest.mark.parametrize("suffix", [".lrcx", ".lrc"])
@pytest.mark.parametrize("timed", [False, True])
def test_quote_roundtrip_preserves_visible_text_styles_ruby_and_timing(tmp_path, suffix, timed):
    path = tmp_path / ("quote" + suffix)
    boundary = "<00:02.000>" if timed else ""
    terminal = "<00:03.000>" if timed else ""
    path.write_text(
        "[format:LRCX]\n[00:01.000]> **{君|きみ}**{\\n}" + boundary + "*の声*" + terminal + "\n"
        "[00:03.000]普通の歌詞\n", encoding="utf-8",
    )
    lines = load_lyrics(path)
    line = lines[0]
    assert line.is_blockquote
    assert line.text == "君\nの声"
    assert line.style_spans
    assert line.segments[0].ruby_spans == [(0, 1, "きみ")]
    assert line.has_karaoke == timed
    assert not lines[1].is_blockquote
    if timed:
        assert line.segments[-1].start == 2
    assert "> " not in serialize_lrc(lines)
    for _ in range(3):
        source = serialize_lrcx(lines)
        assert "[00:01.000]> **{君|きみ}**" in source
        path.write_text(source, encoding="utf-8")
        assert load_lyrics(path) == lines


@pytest.mark.parametrize("source,text,quoted", [
    ("> Quoted", "Quoted", True),
    (r"\> Literal", "> Literal", False),
    ("a > b", "a > b", False),
    (">nospace", ">nospace", False),
    (">> nested", ">> nested", False),
    ("> > second marker", "> second marker", True),
    ("> ", "", True),
])
def test_quote_prefix_is_single_level_and_can_be_escaped(tmp_path, source, text, quoted):
    path = tmp_path / "prefix.lrcx"
    path.write_text("[format:LRCX]\n[00:01.000]" + source + "\n", encoding="utf-8")
    line = load_lyrics(path)[0]
    assert line.text == text
    assert line.is_blockquote == quoted
    path.write_text(serialize_lrcx([line]), encoding="utf-8")
    assert load_lyrics(path)[0] == line


@pytest.mark.parametrize("karaoke", [True, False])
def test_quote_button_preserves_selection_readings_and_karaoke(make_lyrics_editor, karaoke):
    editor = make_lyrics_editor(karaoke=karaoke)
    line = editor.lines[0]
    line.segments[0].ruby = "reading"
    line.segments[0].ruby_source = "manual"
    before = deepcopy(line.segments)
    editor.line_text.selectAll()
    editor.quote_button.click()
    assert editor.line_text.toPlainText() == "> Hello"
    assert editor.line_text.textCursor().selectedText() == "Hello"
    assert line.text == "Hello"
    assert line.is_blockquote and editor.quote_button.isChecked()
    assert line.segments == before
    assert "Blockquotes" in editor._lrcx_only_features()
    if karaoke:
        assert editor.karaoke_renderer_preview.is_blockquote
    editor.undo()
    assert not editor.lines[0].is_blockquote
    assert not editor.quote_button.isChecked()
    editor.redo()
    assert editor.lines[0].is_blockquote
    assert editor.quote_button.isChecked()
    assert editor.save_document()
    assert load_lyrics(editor.lrc_path)[0].is_blockquote
    editor.quote_button.click()
    assert editor.line_text.toPlainText() == "Hello"
    assert not editor.lines[0].is_blockquote
    assert editor.lines[0].segments == before


@pytest.mark.parametrize("alignment", ["left", "center", "right"])
@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_quote_bar_spans_multiline_lyric_and_readings(qapp, tmp_path, monkeypatch, alignment, direction):
    path = tmp_path / "render.lrcx"
    path.write_text(
        "[format:LRCX]\n[00:01.000]> **Quoted {君|きみ}**{\\n}Second row\n",
        encoding="utf-8",
    )
    line = load_lyrics(path)[0]
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": None}])
    widget = FuriganaWidget()
    try:
        widget.resize(700, 280)
        widget.set_font(library_font(qapp.font()).family(), 24, 12)
        widget.set_alignment(alignment)
        widget.set_direction(direction)
        widget.setStyleSheet("background-color: #202020;")
        widget.set_segments(line.text, line.segments, 3,
                            style_spans=line.style_spans, is_blockquote=True)
        pieces = widget._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == line.text.replace("\n", "")
        bar = widget._quote_bar_rect(pieces)
        assert bar.width() >= 2
        assert bar.right() < min(piece["x"] for piece in pieces)
        assert bar.bottom() > max(piece["base_y"] for piece in pieces)
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        x = int(bar.center().x())
        for y in range(int(bar.top()) + 2, int(bar.bottom()) - 1):
            assert 50 < image.pixelColor(x, y).red() < 220
        assert image.save(str(tmp_path / "quote-render.png"))
        widget.furigana_ready(line.text, {"karaoke": line.segments, "segments": [], "line_end": 3})
        assert widget.is_blockquote
        widget.set_lyric("Ordinary", generate=False)
        assert widget._quote_bar_rect(widget._prepare_segments()).isEmpty()
    finally:
        widget.deleteLater()
