"""Paint effects must not change the fitted text or the card's layout."""

from dataclasses import replace

import pytest
from PySide6.QtGui import QImage

import ui.lyrics_card_renderer as renderer
from ui.lyrics_card_renderer import CARD_TEMPLATES, CardStyle, render_lyric_card
from ui.lyrics_card_text import CardLyrics


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("placement", ["below_title", "beside_cover"])
@pytest.mark.parametrize("with_readings", [False, True])
def test_effect_settings_preserve_font_sizes_wrapping_and_positions(qapp, monkeypatch, template, placement, with_readings):
    measured = []

    def measure(painter, document, x, y, color, style):
        rows = []
        block = document.begin()
        while block.isValid():
            layout = block.layout()
            rows.append(tuple((layout.lineAt(i).textStart(), layout.lineAt(i).textLength(),
                               layout.lineAt(i).naturalTextWidth(), layout.lineAt(i).height())
                              for i in range(layout.lineCount())))
            block = block.next()
        measured.append((document.defaultFont().pixelSize(), document.textWidth(),
                         document.size().height(), x, y, tuple(rows), painter.clipBoundingRect()))

    monkeypatch.setattr(renderer, "_paint_lyrics", measure)
    style = CardStyle(template=template, details_placement=placement, font_size=96,
                      title="Song title — 夜の歌", credit="Artist name", album="Album name",
                      show_album_cover=True, bold=True, italic=True, alignment="right")
    cover = QImage(160, 160, QImage.Format.Format_RGB32)
    cover.fill(0xff234567)
    line = "君の声を探して Under the stars tonight"
    text = "\n".join([line] * 8)
    contents = CardLyrics(text, readings=tuple((i * (len(line) + 1), i * (len(line) + 1) + 1, "きみ")
                                              for i in range(8))) if with_readings else text
    render_lyric_card(contents, style, cover)
    baseline = measured[:]
    assert len(baseline) == 3
    assert baseline[-1][0] < style.font_size  # Exercise actual automatic shrinking.

    variants = [
        replace(style, stroke_enabled=True, stroke_width=1),
        replace(style, stroke_enabled=True, stroke_width=12),
        replace(style, shadow_enabled=True, shadow_blur=30, shadow_offset_x=-40, shadow_offset_y=40),
        replace(style, stroke_enabled=True, stroke_width=12, shadow_enabled=True,
                shadow_blur=30, shadow_offset_x=40, shadow_offset_y=-40),
    ]
    for variant in variants:
        measured.clear()
        render_lyric_card(contents, variant, cover)
        assert measured == baseline
