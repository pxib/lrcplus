from copy import deepcopy
from itertools import product

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QImage
from PySide6.QtTest import QTest

from core.fonts import library_font
from lyrics.formatting import format_emphasis, parse_emphasis
from lyrics.furigana_widget import FuriganaWidget
from lyrics.loader import load_lyrics
from lyrics.writer import serialize_lrc, serialize_lrcx


def full_spans(spans):
    return [(start, end, *flags, *([False] * (4 - len(flags)))) for start, end, *flags in spans]


@pytest.mark.parametrize("source,text,spans", [
    ("**bold** and *italic*", "bold and italic", [(0, 4, True, False), (9, 15, False, True)]),
    ("***both***", "both", [(0, 4, True, True)]),
    ("**a *b* c**", "a b c", [(0, 2, True, False), (2, 3, True, True), (3, 5, True, False)]),
    ("*a **b** c*", "a b c", [(0, 2, False, True), (2, 3, True, True), (3, 5, False, True)]),
    ("**強い\n気持ち**", "強い\n気持ち", [(0, 6, True, False)]),
    (r"\*literal\*", "*literal*", []),
    ("**unclosed", "**unclosed", []),
    ("a * b", "a * b", []),
    ("** blank **", "** blank **", []),
    ("~~strike~~", "strike", [(0, 6, False, False, True, False)]),
    ("<u>underline</u>", "underline", [(0, 9, False, False, False, True)]),
    ("~~<u>***all***</u>~~", "all", [(0, 3, True, True, True, True)]),
    (r"\~~literal\~~", "~~literal~~", []),
    (r"\<u>literal\</u>", "<u>literal</u>", []),
    ("<u>unclosed ~~strike", "<u>unclosed ~~strike", []),
    ("<b>literal</b>", "<b>literal</b>", []),
])
def test_emphasis_parser(source, text, spans):
    actual_text, actual_spans, offsets = parse_emphasis(source)
    assert (actual_text, actual_spans) == (text, full_spans(spans))
    assert len(offsets) == len(source) + 1
    assert offsets[-1] == len(text)


def test_all_adjacent_style_transitions_roundtrip():
    for states in product(tuple(product((False, True), repeat=4)), repeat=3):
        spans = [(index, index + 1, *state) for index, state in enumerate(states) if any(state)]
        source = format_emphasis("a*b", spans)
        text, restored, _ = parse_emphasis(source)
        assert text == "a*b", source
        assert format_emphasis(text, restored) == source


def test_ruby_and_timing_survive_formatting_across_timestamp_boundaries(tmp_path):
    path = tmp_path / "formatted.lrcx"
    path.write_text(
        "[format:LRCX]\n[00:01.000]**{君|きみ}<00:02.000>の声** *響く*<00:03.000>\n",
        encoding="utf-8",
    )
    line = load_lyrics(path)[0]
    assert line.text == "君の声 響く"
    assert line.style_spans == full_spans([(0, 3, True, False), (4, 6, False, True)])
    assert len(line.segments) == 2
    assert [segment.start for segment in line.segments] == [1, 2]
    assert line.segments[0].ruby_spans == [(0, 1, "きみ")]
    assert line.segments[-1].end == 3
    assert line.has_karaoke
    assert serialize_lrc([line]) == "[00:01.000]君の声 響く\n"
    for _ in range(3):
        contents = serialize_lrcx([line])
        assert "**{君|きみ}<00:02.000>の声**" in contents
        path.write_text(contents, encoding="utf-8")
        assert load_lyrics(path)[0] == line


def test_styles_roundtrip_with_newlines_and_literal_asterisks(tmp_path):
    path = tmp_path / "multiline.lrcx"
    path.write_text(
        "[format:LRCX]\n[00:01.000]~~<u>***First{\\n}Second***</u>~~ \\*literal\\*\n",
        encoding="utf-8",
    )
    lines = load_lyrics(path)
    assert lines[0].text == "First\nSecond *literal*"
    path.write_text(serialize_lrcx(lines), encoding="utf-8")
    assert load_lyrics(path) == lines


@pytest.mark.parametrize("source,ends", [
    ("*First*<00:02.000>*Second*<00:03.000>", [None, 3]),
    ("**First<00:02.000>**", [2]),
])
def test_emphasis_at_timing_boundaries_does_not_merge_delimiters_or_lose_ends(tmp_path, source, ends):
    path = tmp_path / "boundaries.lrcx"
    path.write_text("[format:LRCX]\n[00:01.000]" + source + "\n", encoding="utf-8")
    line = load_lyrics(path)[0]
    assert "*" not in line.text
    assert [segment.end for segment in line.segments] == ends
    assert line.end == ends[-1]
    path.write_text(serialize_lrcx([line]), encoding="utf-8")
    assert load_lyrics(path)[0] == line


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_renderer_uses_styled_fonts_and_keeps_reading_and_timing(qapp, tmp_path, monkeypatch, direction):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": "君", "reading": "きみ"}, {"text": "の声", "reading": None}])
    widget = FuriganaWidget()
    try:
        widget.resize(700, 350)
        widget.set_font(library_font(qapp.font()).family(), 32, 18)
        widget.setStyleSheet("background-color: #202020;")
        widget.set_direction(direction)
        widget.set_segments("君の声", [{"text": "君の声", "start": 1, "end": 3}], 3,
                            style_spans=[(0, 1, True, False), (1, 3, False, True)])
        pieces = widget._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == "君の声"
        assert pieces[0]["reading"] == "きみ"
        assert {piece["timing_group"] for piece in pieces} == {0}
        assert widget._styled_font(widget.base_font, pieces[0]).bold()
        assert widget._styled_font(widget.reading_font, pieces[0]).bold()
        assert widget._styled_font(widget.base_font, pieces[-1]).italic()
        for piece in pieces:
            font = widget._styled_font(widget.base_font, piece)
            assert piece["base_width"] == QFontMetrics(font).horizontalAdvance(piece["text"])
        widget.set_karaoke_position(2)
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        assert image.save(str(tmp_path / (direction + "-emphasis.png")))
        # A later reading result must retain the line's formatting.
        widget.furigana_ready("君の声", [{"text": "君の声", "reading": None}])
        assert widget.style_spans == [(0, 1, True, False), (1, 3, False, True)]
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("marker,closing,flags", [
    ("**", "**", (True, False, False, False)),
    ("*", "*", (False, True, False, False)),
    ("~~", "~~", (False, False, True, False)),
    ("<u>", "</u>", (False, False, False, True)),
])
def test_editor_formatting_preserves_karaoke_and_undo(editor, marker, closing, flags):
    before = deepcopy(editor.lines[0].segments)
    editor.line_text.selectAll()
    editor.insert_lyric_emphasis(marker, closing)
    assert editor.line_text.toPlainText() == marker + "Hello" + closing
    assert editor.lines[0].text == "Hello"
    assert editor.lines[0].segments == before
    assert editor.lines[0].has_karaoke
    assert editor.lines[0].style_spans == [(0, 5, *flags)]
    assert editor.karaoke_renderer_preview.style_spans == editor.lines[0].style_spans
    assert "Text formatting" in editor._lrcx_only_features()
    editor.undo()
    assert not editor.lines[0].style_spans
    editor.redo()
    assert editor.line_text.toPlainText() == marker + "Hello" + closing
    assert editor.save_document()
    assert load_lyrics(editor.lrc_path)[0].style_spans == editor.lines[0].style_spans


@pytest.mark.parametrize("key,marker,closing", [(Qt.Key.Key_B, "**", "**"), (Qt.Key.Key_I, "*", "*"), (Qt.Key.Key_U, "<u>", "</u>")])
def test_editor_formatting_shortcuts(qapp, editor, key, marker, closing):
    editor.show()
    editor.activateWindow()
    editor.line_text.setFocus()
    editor.line_text.selectAll()
    qapp.processEvents()
    QTest.keyClick(editor.line_text, key, Qt.KeyboardModifier.ControlModifier)
    assert editor.line_text.toPlainText() == marker + "Hello" + closing


def test_editor_inserts_empty_markers_with_cursor_inside(editor):
    cursor = editor.line_text.textCursor()
    cursor.setPosition(5)
    editor.line_text.setTextCursor(cursor)
    editor.insert_lyric_emphasis("**")
    assert editor.line_text.toPlainText() == "Hello****"
    QTest.keyClicks(editor.line_text, "world")
    assert editor.line_text.toPlainText() == "Hello**world**"
    assert editor.lines[0].text == "Helloworld"
    assert editor.lines[0].style_spans == full_spans([(5, 10, True, False)])


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_strike_and_underline_render_together_with_bold_and_italic(qapp, tmp_path, direction):
    widget = FuriganaWidget()
    try:
        widget.resize(700, 350)
        widget.set_font(library_font(qapp.font()).family(), 32, 18)
        widget.setStyleSheet("background-color: #202020;")
        widget.set_direction(direction)
        widget.set_lyric("全部", generate=False, style_spans=[(0, 2, True, True, True, True)])
        pieces = widget._prepare_segments()
        for piece in pieces:
            font = widget._styled_font(widget.base_font, piece)
            assert font.bold() and font.italic() and font.strikeOut() and font.underline()
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        assert image.save(str(tmp_path / (direction + "-all-styles.png")))
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("style", ["underline", "strike"])
@pytest.mark.parametrize("position", [None, 1.5])
def test_decoration_pixels_continue_across_spaces_and_furigana_padding(qapp, tmp_path, monkeypatch, style, position):
    text = "ワタシ 超最強 超最強"
    tokens = [
        {"text": "ワタシ ", "reading": None},
        {"text": "超", "reading": "ちょう"},
        {"text": "最強", "reading": "さいきょう"},
        {"text": " ", "reading": None},
        {"text": "超", "reading": "ちょう"},
        {"text": "最強", "reading": "さいきょう"},
    ]
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: tokens)
    widget = FuriganaWidget()
    try:
        widget.resize(900, 180)
        widget.set_font(library_font(qapp.font()).family(), 32, 18)
        widget.set_alignment("left")
        widget.setStyleSheet("background-color: #202020;")
        widget.set_segments(text, [{"text": text, "start": 1, "end": 2}], 2,
                            style_spans=[(0, len(text), False, False, style == "strike", style == "underline")])
        widget.set_karaoke_position(position)
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        spans = [span for piece in widget._prepare_segments() for span in piece.get("_decorations", ())]
        for band in ("base", "reading"):
            band_spans = [span for span in spans if span["band"] == band]
            left = min(span["left"] for span in band_spans)
            right = max(span["right"] for span in band_spans)
            y = band_spans[0]["y"]
            assert all(span["y"] == y for span in band_spans)
            # Every pixel between the ends must be decorated, including the
            # whitespace token and layout padding introduced by wide readings.
            allowed = {widget.lyrics_color.rgb(), widget.highlight_color.rgb()}
            for x in range(int(left) + 2, int(right) - 1):
                assert image.pixelColor(x, y).rgb() in allowed, (band, style, position, x, y)
        assert image.save(str(tmp_path / (style + "-continuous.png")))
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("style", ["underline", "strike"])
def test_decoration_stops_at_unstyled_spaces_and_starts_fresh_after_newline(qapp, style):
    widget = FuriganaWidget()
    try:
        widget.resize(900, 300)
        widget.set_font(library_font(qapp.font()).family(), 32, 18)
        widget.set_alignment("left")
        widget.setStyleSheet("background-color: #202020;")
        text = "First   Second\nThird"
        flags = (False, False, style == "strike", style == "underline")
        widget.set_lyric(text, generate=False, style_spans=[(0, 5, *flags), (8, len(text), *flags)])
        pieces = widget._prepare_segments()
        spans = [span for piece in pieces for span in piece.get("_decorations", ())]
        assert len(spans) == 3
        assert spans[0]["right"] < spans[1]["left"]
        assert spans[2]["y"] > spans[1]["y"]
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        gap_x = int((spans[0]["right"] + spans[1]["left"]) / 2)
        assert image.pixelColor(gap_x, spans[0]["y"]).name() == "#202020"
    finally:
        widget.deleteLater()
