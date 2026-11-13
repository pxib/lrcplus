"""Reading boundaries and karaoke wipes stay aligned with their base text."""

import pytest
from PySide6.QtGui import QImage, QLinearGradient, QPainter

from core.settings import KARAOKE_DEFAULTS, apply_karaoke_settings
from lyrics.furigana import split_reading_by_text_ranges
from lyrics.furigana_widget import FuriganaWidget
from lyrics.loader import load_lyrics
from lyrics.models import LyricLine, LyricSegment


@pytest.mark.parametrize("text,reading,ranges,expected", [
    ("女子力", "じょしりょく", [(0, 1), (1, 2), (2, 3)], ["じょ", "し", "りょく"]),
    ("女子", "ジョシ", [(0, 1), (1, 2)], ["じょ", "し"]),
    ("高く", "たかく", [(0, 1), (1, 2)], ["たか", "く"]),
    ("申し込む", "もうしこむ", [(0, 1), (1, 2), (2, 3), (3, 4)], ["もう", "し", "こ", "む"]),
    ("今日", "きょう", [(0, 1), (1, 2)], ["きょ", "う"]),
])
def test_reading_split_keeps_small_kana_and_visible_kana(text, reading, ranges, expected):
    assert split_reading_by_text_ranges(text, reading, ranges) == expected
    # Single-range callers (the separation editor) must agree with batch callers.
    assert [split_reading_by_text_ranges(text, reading, [part])[0] for part in ranges] == expected


def test_separation_readings_survive_save_and_undo(editor):
    line = LyricLine(2, "女子力", [LyricSegment(2, "女子力", "じょしりょく", end=5,
                                                   ruby_source="manual")], end=5)
    editor.lines = [line]
    editor._populate_lines(preserve_line_index=0)
    editor._reset_history_baseline()
    editor.separator_widget.set_content(line.text, {1, 2})
    editor.apply_visual_separation()

    assert [segment.ruby for segment in line.segments] == ["じょ", "し", "りょく"]
    assert [(segment.start, segment.end) for segment in line.segments] == [(2, 3), (3, 4), (4, 5)]
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert [segment.ruby_spans for segment in restored.segments] == [
        [(0, 1, "じょ")], [(0, 1, "し")], [(0, 1, "りょく")],
    ]
    editor.undo()
    assert len(editor.lines[0].segments) == 1
    assert editor.lines[0].segments[0].ruby == "じょしりょく"


def test_separation_does_not_turn_automatic_readings_into_manual_ruby(editor, monkeypatch):
    tokens = [{"text": "女子力", "reading": "じょしりょく"}]
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: tokens)
    line = LyricLine(2, "女子力", [LyricSegment(2, "女子力", "じょしりょく", end=5,
                                                   ruby_source="generated")], end=5)
    editor.lines = [line]
    editor._populate_lines(preserve_line_index=0)
    editor.separator_widget.set_content(line.text, {1, 2})
    editor.apply_visual_separation()

    assert all(segment.ruby is None and not segment.ruby_spans for segment in line.segments)
    row = editor.karaoke_renderer_preview
    row.set_segments(line.text, line.segments, line_end=line.end)
    assert [segment["reading"] for segment in row.segments] == ["じょしりょく"]
    assert all(segment["ruby_source"] == "generated" for segment in row.segments)


def test_automatic_reading_ignores_separation_but_retains_each_clock(qapp, monkeypatch):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: [
        {"text": "大好", "reading": "だいす"},
        {"text": "きな", "reading": None},
        {"text": "洋服", "reading": "ようふく"},
    ])
    row = FuriganaWidget()
    try:
        row.resize(700, 200)
        apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_delay_percent=0))
        chunks = [LyricSegment(0, "大", end=1), LyricSegment(3, "好", end=4),
                  LyricSegment(4, "きな", end=5), LyricSegment(5, "洋", end=6),
                  LyricSegment(6, "服", end=7)]
        row.set_segments("大好きな洋服", chunks, line_end=7)
        assert [(piece["text"], piece["reading"]) for piece in row.segments] == [
            ("大好", "だいす"), ("きな", None), ("洋服", "ようふく"),
        ]
        piece = row._prepare_segments()[0]
        for position, expected in [(-1, 0), (0.5, .25), (1, .5), (2, .5), (3.5, .75), (4, 1)]:
            row.set_karaoke_position(position)
            assert row._segment_progress(piece, row.segments) == pytest.approx(expected)
        assert [(chunk.start, chunk.end) for chunk in chunks] == [(0, 1), (3, 4), (4, 5), (5, 6), (6, 7)]
    finally:
        row.deleteLater()


def test_one_timing_chunk_still_sweeps_across_several_automatic_words(qapp, monkeypatch):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: [
        {"text": "女子", "reading": "じょし"}, {"text": "力", "reading": "りょく"},
    ])
    row = FuriganaWidget()
    try:
        apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_delay_percent=0))
        row.set_segments("女子力", [LyricSegment(0, "女子力", end=3)], line_end=3)
        for position, expected in [(1, [.5, 0]), (2, [1, 0]), (2.5, [1, .5]), (3, [1, 1])]:
            row.set_karaoke_position(position)
            assert [row._segment_progress(piece, row.segments) for piece in row.segments] == pytest.approx(expected)
    finally:
        row.deleteLater()


def test_bare_kana_keeps_its_portion_of_the_clock_after_automatic_ruby(qapp, monkeypatch):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: [
        {"text": "歌", "reading": "うた"}, {"text": "う", "reading": None},
    ])
    row = FuriganaWidget()
    try:
        apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_delay_percent=0))
        row.set_segments("歌う", [LyricSegment(0, "歌う", end=2)], line_end=2)
        row.set_karaoke_position(1)
        assert [row._segment_progress(piece, row.segments) for piece in row.segments] == [1, 0]
    finally:
        row.deleteLater()


@pytest.mark.parametrize("reading", ["じょ", "じょしりょく"])
@pytest.mark.parametrize("romaji", [False, True])
def test_reading_wipe_tracks_base_fraction(qapp, monkeypatch, reading, romaji):
    row = FuriganaWidget()
    row.resize(700, 200)
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": None}])
    row.set_romaji_visible(romaji)
    row.set_segments("女子力", [LyricSegment(0, "女子力", reading, end=4)])
    apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_style="classic",
                                    sweep_delay_ms=0, sweep_delay_percent=0,
                                    word_lift=False, line_entrance=False))
    draws = []
    original = QPainter.drawText

    def draw(painter, *args):
        brush = painter.pen().brush()
        gradient = brush.gradient()
        if gradient is not None:
            # Copy the gradient while the painter still owns the brush.
            gradient = QLinearGradient(gradient)
        draws.append((args[-1], gradient, brush.color()))
        return original(painter, *args)

    monkeypatch.setattr(QPainter, "drawText", draw)
    try:
        for progress in (0.1, 0.5, 0.9):
            draws.clear()
            row.set_karaoke_position(4 * progress)
            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            row.render(image)
            piece = row._prepare_segments()[0]
            assert len(draws) == 2
            base_gradient, ruby_gradient = draws[0][1], draws[1][1]
            # Classic gradients put their hard color boundary at the current
            # sweep position. Compare it in each text band's own coordinates.
            def edge(gradient):
                return gradient.start().x() + (gradient.finalStop().x() - gradient.start().x()) * gradient.stops()[0][0]

            base_left = piece["x"] + (piece["width"] - piece["base_width"]) / 2
            ruby_left = piece["x"] + (piece["width"] - piece["_display_reading_width"]) / 2
            def solid_color(gradient):
                colors = [color for _, color in gradient.stops()]
                return colors[0] if all(color == colors[0] for color in colors) else None

            if solid_color(base_gradient) is not None:
                assert solid_color(ruby_gradient) == solid_color(base_gradient)
                continue
            fraction = (edge(base_gradient) - base_left) / piece["base_width"]
            if fraction <= 0:
                assert solid_color(ruby_gradient) == row.lyrics_color
            elif fraction >= 1:
                assert solid_color(ruby_gradient) == row.highlight_color
            else:
                assert ruby_gradient is not None
                assert fraction == pytest.approx(
                    (edge(ruby_gradient) - ruby_left) / piece["_display_reading_width"], abs=0.001)
    finally:
        row.deleteLater()
