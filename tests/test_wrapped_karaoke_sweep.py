"""Wrapped timed chunks must reveal in reading order, rather than in parallel."""

import pytest
from PySide6.QtGui import QColor, QFontMetrics, QImage

from core.fonts import library_font
from lyrics.furigana_widget import FuriganaWidget
from lyrics.lrcx_parser import parse_lrcx


@pytest.mark.parametrize("alignment", ["left", "center", "right"])
@pytest.mark.parametrize("separate_timing", [False, True])
def test_wrapped_chunk_finishes_first_row_before_second(qapp, monkeypatch, alignment, separate_timing):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text.replace("\n", ""), "reading": None}])
    row = FuriganaWidget()
    try:
        row.set_font(library_font(qapp.font()).family(), 32, 16)
        row.set_ruby_visible(False)
        row.set_focus_transition(False)
        row.set_colors("#ffffff", "#ff0000")
        row.setStyleSheet("background-color: #000000;")
        row.set_alignment(alignment)
        row.set_text_wrapping(True, -1)
        row.set_karaoke_properties(sweep_delay_percent=0)
        metrics = QFontMetrics(row.base_font)
        row.resize(metrics.horizontalAdvance("W" * 10) + 22, 200)
        boundary = "<00:05.000>" if separate_timing else ""
        line = parse_lrcx("[00:00.000]" + "W" * 10 + boundary + "W" * 10 + "<00:10.000>")[0]
        row.set_segments(line.text, line.segments, line_end=line.end)
        pieces = row._prepare_segments()
        baselines = sorted({piece["base_y"] for piece in pieces})
        assert len(baselines) == 2

        def colors_at(position, baseline):
            row.set_karaoke_position(position)
            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor("#000000"))
            row.render(image)
            pixels = [image.pixelColor(x, y)
                      for y in range(int(baseline - metrics.ascent()), int(baseline))
                      for x in range(image.width())]
            red = sum(color.red() > 100 and color.green() < 40 for color in pixels)
            white = sum(color.red() > 100 and color.green() > 100 for color in pixels)
            return red, white

        first_red, first_white = colors_at(2, baselines[0])
        second_red, second_white = colors_at(2, baselines[1])
        assert first_red > 0 and first_white > 0
        assert second_red == 0 and second_white > 0
        first_red, first_white = colors_at(8, baselines[0])
        second_red, second_white = colors_at(8, baselines[1])
        assert first_red > 0 and first_white == 0
        assert second_red > 0 and second_white > 0
    finally:
        row.close()
        row.deleteLater()
