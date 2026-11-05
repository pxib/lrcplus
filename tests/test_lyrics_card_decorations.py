import math

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from ui.lyrics_card_decorations import decoration_spans
from ui.lyrics_card_renderer import CardStyle, _document, _paint_document
from ui.lyrics_card_text import CardLyrics


def source_with_ruby(styles=()):
    return CardLyrics("東京 の 夜", styles=styles, readings=((0, 2, "とうきょう"), (5, 6, "よる")))


def rendered_alpha(document):
    image = QImage(math.ceil(document.size().width()) + 40, math.ceil(document.size().height()) + 40,
                   QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _paint_document(painter, document, 20, 20, QColor("#ffffff"))
    painter.end()
    return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)[:, :, 3].copy()


@pytest.mark.parametrize("kind", ["underline", "strike"])
@pytest.mark.parametrize("alignment", ["left", "center", "right"])
@pytest.mark.parametrize("card_wide", [False, True])
def test_rules_are_continuous_across_ruby_padding_spaces_and_font_changes(qapp, kind, alignment, card_wide):
    flags = (False, False, kind == "strike", kind == "underline")
    styles = ((0, 2, True, False, False, False),)
    if not card_wide:
        styles += ((0, 6, *flags),)
    source = source_with_ruby(styles)
    style = CardStyle(alignment=alignment, underline=card_wide and kind == "underline",
                      strikeout=card_wide and kind == "strike")
    document = _document(source.text, "", 56, 700, lyric_style=style, source=source)
    spans = [span for span in decoration_spans(document) if span.kind == kind]
    assert [span.band for span in spans].count("base") == 1
    assert [span.band for span in spans].count("reading") == 1
    alpha = rendered_alpha(document)
    for span in spans:
        left, right = math.ceil(span.left + 20) + 2, math.floor(span.right + 20) - 2
        y = round(span.y + 20)
        # Every column across the full run is inked, including the furigana
        # expansion and styled spaces where native token rules leave gaps.
        assert np.all(alpha[y - 2:y + 3, left:right].max(axis=0) > 200)


def test_rules_stop_at_unstyled_text_in_both_base_and_reading_bands(qapp):
    source = source_with_ruby(((0, 2, False, False, False, True), (5, 6, False, False, False, True)))
    document = _document(source.text, "", 56, 700, lyric_style=CardStyle(), source=source)
    spans = [span for span in decoration_spans(document) if span.kind == "underline"]
    assert len([span for span in spans if span.band == "base"]) == 2
    assert len([span for span in spans if span.band == "reading"]) == 2
    alpha = rendered_alpha(document)
    base = [span for span in spans if span.band == "base"]
    middle = round((base[0].right + base[1].left) / 2) + 20
    y = round(base[0].y) + 20
    assert alpha[y - 1:y + 2, middle].max() < 50


def test_partial_word_emphasis_does_not_decorate_the_rest_of_a_ruby_word(qapp):
    source = source_with_ruby(((0, 1, False, False, False, True),))
    document = _document(source.text, "", 56, 700, lyric_style=CardStyle(), source=source)
    spans = decoration_spans(document)
    assert len(spans) == 2
    base = next(span for span in spans if span.band == "base")
    reading = next(span for span in spans if span.band == "reading")
    object_width = document._card_ruby_handler.pieces[0][2].width()
    assert base.right - base.left < object_width * .6
    assert reading.right - reading.left < object_width * .6


def test_rules_restart_on_each_wrapped_row_and_visual_newline(qapp):
    source = CardLyrics("東京 の 夜 東京 の 夜\n東京", readings=((0, 2, "とうきょう"), (5, 6, "よる"),
                        (7, 9, "とうきょう"), (12, 13, "よる"), (14, 16, "とうきょう")))
    document = _document(source.text, "", 56, 220, lyric_style=CardStyle(underline=True, strikeout=True), source=source)
    document.size()
    rows = 0
    block = document.begin()
    while block.isValid():
        rows += block.layout().lineCount()
        block = block.next()
    assert rows > 2
    spans = decoration_spans(document)
    for kind in ("underline", "strike"):
        base = [span for span in spans if span.band == "base" and span.kind == kind]
        assert len(base) == rows
        assert len({round(span.y, 2) for span in base}) == rows
        assert all(0 <= span.left <= span.right <= 220.5 for span in base)
