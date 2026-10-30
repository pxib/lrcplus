"""Bound repeated layout/timing work for dense pinyin and karaoke lines."""

import random

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter, QTransform

from lyrics.furigana_widget import FuriganaWidget
from lyrics.karaoke_layout import annotate_next_timed_starts


@pytest.fixture
def row(qapp):
    widget = FuriganaWidget()
    widget.resize(900, 140)
    widget.lyric = "你" * 1200
    widget.segments = [
        {"text": "你", "reading": "nǐ", "start": 0, "timing_group": 0}
        for _ in range(1200)
    ]
    widget.karaoke_line_end = 15
    yield widget
    widget.close()
    widget.deleteLater()


def test_dense_pinyin_reuses_layout_and_resolves_group_progress_once(row, monkeypatch):
    builds = []
    progress_calls = []
    original_build = row._build_prepared_segments
    original_progress = row._segment_progress

    def build():
        builds.append(True)
        return original_build()

    def progress(piece, pieces):
        progress_calls.append(True)
        return original_progress(piece, pieces)

    monkeypatch.setattr(row, "_build_prepared_segments", build)
    monkeypatch.setattr(row, "_segment_progress", progress)
    image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
    row.set_karaoke_position(1)
    row.render(image)
    for position in (2, 3, 4):
        progress_calls.clear()
        row.set_karaoke_position(position)
        row.render(image)
        assert len(progress_calls) == 1
    assert len(builds) == 1


@pytest.mark.parametrize("change", [
    lambda row: row.resize(700, 180),
    lambda row: row.set_font(size=40, ruby_size=22),
    lambda row: row.set_direction("vertical"),
    lambda row: row.set_alignment("right"),
    lambda row: row.set_ruby_visible(False),
    lambda row: row.set_romaji_visible(True),
    lambda row: row.set_all_romaji_visible(True),
    lambda row: row.set_ruby_position("below"),
    lambda row: row.set_ruby_padding(8),
    lambda row: row.set_text_wrapping(True, 2),
    lambda row: row.set_vertical_padding(12),
    lambda row: row.segments[0].update(reading="hǎo", start=2),
    lambda row: row.segments.append({"text": "好", "reading": "hǎo"}),
])
def test_content_and_geometry_changes_refresh_layout(row, monkeypatch, change):
    row._prepare_segments()
    original_build = row._build_prepared_segments
    builds = []

    def build():
        builds.append(True)
        return original_build()

    monkeypatch.setattr(row, "_build_prepared_segments", build)
    change(row)
    row._prepare_segments()
    row._prepare_segments()
    assert len(builds) == 1


def test_callers_cannot_accumulate_offsets_in_cached_layout(row):
    prepared = row._prepare_segments()
    original_x = prepared[0]["x"]
    prepared[0]["x"] += 500
    prepared.pop()
    fresh = row._prepare_segments()
    assert fresh[0]["x"] == original_x
    assert len(fresh) == len(row.segments)


def test_cached_timing_matches_source_order_for_unsorted_and_repeated_starts():
    rng = random.Random(42)
    pieces = [
        {"start": rng.choice([None, 0, 1, 2, 3, 4]),
         "timing_group": rng.choice([None, 0, 1, 2])}
        for _ in range(500)
    ]
    annotate_next_timed_starts(pieces)
    for piece in pieces:
        expected = None
        if piece["start"] is not None:
            expected = next((
                other["start"] for other in pieces
                if other["timing_group"] != piece["timing_group"]
                and other["start"] is not None
                and other["start"] > piece["start"]
            ), None)
        assert piece["_next_timed_start"] == expected


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_cached_timing_preserves_progress_and_line_end_changes(row, direction):
    row.set_direction(direction)
    row.segments = [
        {"text": "你", "reading": "nǐ", "start": 0, "timing_group": 0},
        {"text": "好", "reading": "hǎo", "start": 2, "timing_group": 1},
        {"text": "你", "reading": "nǐ", "start": 1, "timing_group": 0},
        {"text": "好", "reading": "hǎo", "start": 4, "end": 5,
         "timing_group": 2},
        {"text": "你", "reading": "nǐ", "start": 6, "instant": True,
         "timing_group": 3},
    ]
    for line_end in (10, 20):
        row.karaoke_line_end = line_end
        for position in (0, 1.5, 3, 4.5, 7, 15):
            row.set_karaoke_position(position)
            prepared = row._prepare_segments()
            reference = [dict(piece) for piece in prepared]
            for piece in reference:
                piece.pop("_next_timed_start")
            assert [row._segment_progress(piece, prepared) for piece in prepared] == [
                row._segment_progress(piece, reference) for piece in reference
            ]


def test_offscreen_pinyin_does_not_issue_draw_calls(row, monkeypatch):
    calls = []
    original = QPainter.drawText

    def draw(painter, *args):
        calls.append(args)
        return original(painter, *args)

    monkeypatch.setattr(QPainter, "drawText", draw)
    image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
    row.render(image)
    assert 0 < len(calls) < 200


@pytest.mark.parametrize("animation,direction,romaji,sweep,dpr", [
    ("none", "horizontal", False, "classic", 1),
    ("scale", "horizontal", False, "soft", 1),
    ("pop", "horizontal", False, "glow", 1),
    ("slide", "horizontal", False, "shimmer", 1),
    ("fade", "horizontal", True, "classic", 1),
    ("none", "vertical", False, "classic", 1),
    ("slide", "vertical", False, "glow", 1),
    ("none", "horizontal", False, "classic", 2),
])
def test_culling_preserves_pixels_with_transforms_and_karaoke(
        row, monkeypatch, animation, direction, romaji, sweep, dpr):
    row.set_direction(direction)
    row.set_all_romaji_visible(romaji)
    row.highlight_animation = animation
    row.highlight_progress = 0.5
    row.is_current_line = True
    row.set_karaoke_sweep_style(sweep)
    row.set_karaoke_position(6)

    def render():
        image = QImage(row.width() * dpr, row.height() * dpr,
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(0)
        row.render(image)
        return image

    actual = render()
    # Expand only the Python paint pass's viewport calculation so that every
    # piece is submitted to Qt's ordinary pixel clipping for the reference.
    monkeypatch.setattr(QTransform, "mapRect", lambda *_: QRectF(-1e9, -1e9, 2e9, 2e9))
    assert actual == render()
