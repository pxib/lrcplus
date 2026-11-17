"""Automatic reading cache must not split manual ruby from its timing owner."""

import pytest
from PySide6.QtGui import QColor, QImage

from core.fonts import library_font
from lyrics.furigana_widget import FuriganaWidget
from lyrics.lrcx_parser import parse_lrcx


@pytest.mark.parametrize("position", [0.8, 1.2, 1.6, 2.8, 3.2, 3.6])
@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_cached_readings_do_not_advance_neighboring_kana(qapp, monkeypatch, position, direction):
    line = parse_lrcx("[00:00.000]{君|きみ}の<00:02.000>{声|こえ}が<00:04.000>{届|とど}く<00:06.000>")[0]
    plain_tokens = [{"text": c, "reading": None} for c in line.text]
    cached_tokens = [{"text": c, "reading": r} for c, r in
                     zip(line.text, ["きみ", None, "こえ", None, "とど", None])]

    def render(tokens):
        monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: tokens)
        row = FuriganaWidget()
        try:
            row.resize(800, 400)
            row.set_font(library_font(qapp.font()).family(), 36, 20)
            row.set_focus_transition(False)
            row.set_direction(direction)
            row.set_segments(line.text, line.segments, line_end=line.end)
            row.set_karaoke_position(position)
            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor("#18202c"))
            row.render(image)
            return image
        finally:
            row.close()
            row.deleteLater()

    assert render(cached_tokens) == render(plain_tokens)
