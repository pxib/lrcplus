"""Exercise viewport allocation with real Qt rows and source lyric indices."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest

from core.fonts import library_font
from lyrics.models import LyricLine, LyricSegment
from lyrics.scrolling_widget import ScrollingLyricsWidget
from lyrics.furigana_widget import FuriganaWidget


@pytest.fixture
def view(qapp, monkeypatch):
    # Deterministic readings: these tests must never start network providers.
    readings = {}
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", readings.get)
    monkeypatch.setattr("lyrics.scrolling_widget.get_cached_furigana_segments", readings.get)
    widget = ScrollingLyricsWidget()
    widget.set_font(library_font(qapp.font()).family(), 24, 12)
    widget.resize(760, 480)
    widget.show()
    qapp.processEvents()
    yield widget, readings
    widget.clear()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def document(count=5000):
    return [LyricLine(index * 3, f"Lyric {index}") for index in range(count)]


def assert_viewport_covered(widget):
    scrollbar = widget._scroll_axis()
    first, last = scrollbar.value(), scrollbar.value() + widget._viewport_extent()
    for index in range(widget.lyric_count):
        start, end = widget._line_start(index), widget._line_start(index) + widget._extents[index]
        if end > first and start < last:
            assert index in widget._mounted_rows


@pytest.mark.parametrize("count", [5000, 37043])
def test_long_document_allocates_only_nearby_rows_and_seeks_by_source_index(view, qapp, count):
    widget, _ = view
    widget.set_lyrics(document(count))
    qapp.processEvents()
    assert widget.lyric_count == count
    assert 0 < len(widget.rows) < 40
    assert_viewport_covered(widget)

    for index in (count // 2, count - 1, 120, 0):
        widget.set_current_line(index, animate=False)
        qapp.processEvents()
        assert widget.current_line == index
        row = widget._mounted_rows[index]
        assert row.lyric == f"Lyric {index}"
        assert row.is_current_line
        assert abs(widget._line_center(index) - widget._scroll_axis().value()
                   - widget._viewport_extent() / 2) <= 2 or widget._scroll_axis().value() in (0, widget._scroll_axis().maximum())
        assert len(widget.rows) < 40
        assert_viewport_covered(widget)


def test_manual_scroll_mounts_rows_without_changing_playback_line(view, qapp):
    widget, _ = view
    widget.set_lyrics(document())
    widget._scroll_axis().setValue(int(widget._line_start(3000)))
    qapp.processEvents()
    assert widget.current_line == 0
    assert 0 not in widget._mounted_rows
    assert 3000 in widget._mounted_rows
    assert not any(row.is_current_line for row in widget.rows)
    assert_viewport_covered(widget)
    assert len(widget.rows) < 40


@pytest.mark.parametrize("alignment", ["left", "right"])
def test_vertical_columns_seek_resize_and_reverse_order(view, qapp, alignment):
    widget, _ = view
    widget.set_direction("vertical")
    widget.set_alignment(alignment)
    lines = [LyricLine(index, "長い歌詞" * (index % 4 + 1)) for index in range(1000)]
    widget.set_lyrics(lines)
    widget.set_current_line(700, animate=False)
    qapp.processEvents()
    assert 700 in widget._mounted_rows
    assert len(widget.rows) < 50
    assert_viewport_covered(widget)
    if alignment == "right":
        assert widget._line_start(699) > widget._line_start(700)
    else:
        assert widget._line_start(699) < widget._line_start(700)
    widget.resize(520, 650)
    qapp.processEvents()
    widget.refresh_layout_now()
    widget.set_current_line(700, animate=False)
    assert widget._mounted_rows[700].height() == widget.scroll_area.viewport().height()
    assert_viewport_covered(widget)
    widget.set_direction("horizontal")
    widget.set_current_line(20, animate=False)
    qapp.processEvents()
    assert widget._mounted_rows[20].width() == widget.scroll_area.viewport().width()
    assert_viewport_covered(widget)


def test_recreated_rows_keep_settings_readings_and_karaoke_after_seek(view, qapp):
    widget, readings = view
    lines = document(1000)
    lines[700] = LyricLine(2100, "歌詞", [LyricSegment(2100, "歌詞", ruby="かし", end=2102)], end=2103, has_karaoke=True)
    widget.set_lyrics(lines)
    widget.set_current_line(700, animate=False)
    widget.set_karaoke_position(2101)
    assert widget._mounted_rows[700].segments[0]["reading"] == "かし"
    widget.set_current_line(0, animate=False)
    widget.set_karaoke_position(0.1)
    assert 700 not in widget._mounted_rows
    widget.set_colors("#aabbcc", "#bbccdd")
    widget.set_font(widget.font_family, 30, 14)
    widget.set_ruby_position("below")
    widget.set_karaoke_properties("glow", softness=12)
    # Prefetch completes while a plain line is unmounted.
    readings["Lyric 701"] = [{"text": "Lyric 701", "reading": "cached reading"}]
    widget.refresh_furigana_for_texts(["Lyric 701"])
    widget.set_current_line(700, animate=False)
    widget.set_karaoke_position(2100.5)
    qapp.processEvents()
    row = widget._mounted_rows[700]
    assert row.karaoke_position == 2100.5
    assert row.segments[0]["start"] == 2100
    assert row.segments[0]["reading"] == "かし"
    assert row.ruby_position == "below"
    assert row.base_font.pointSize() == 30
    assert row.karaoke_sweep_style == "glow"
    assert row.lyrics_color.name() == "#aabbcc"
    assert widget._mounted_rows[701].segments[0]["reading"] == "cached reading"
    assert_viewport_covered(widget)


def test_offscreen_placeholder_and_limited_readings_use_document_indices(view, qapp):
    widget, _ = view
    lines = document(1000)
    lines[600] = LyricLine(1800, "")
    widget.set_lyrics(lines)
    widget.set_placeholder_text(600, "Next in 3")
    assert 600 not in widget._mounted_rows
    widget.set_limited_reading_rendering(True, nearby_range=5, fade_in=False)
    widget.set_current_line(600, animate=False)
    qapp.processEvents()
    assert widget._mounted_rows[600].lyric == "Next in 3"
    assert widget._mounted_rows[600]._reading_render_target_enabled
    widget.clear_placeholder_text(600)
    assert widget._mounted_rows[600].lyric == "♪"
    widget.set_current_line(-1, animate=False)
    widget.set_scrolling_mode("continuous")
    widget._scroll_axis().setValue(int(widget._line_start(800)))
    qapp.processEvents()
    anchor = widget._visible_reading_anchor()
    assert 800 <= anchor <= 810
    for row in widget.rows:
        assert row._reading_render_target_enabled == (abs(row._lyric_index - anchor) <= 5)


def test_clear_and_new_document_release_mounted_rows_and_reset_scroll_state(view, qapp):
    widget, _ = view
    widget.set_lyrics(document())
    widget.set_current_line(4000, animate=False)
    widget.set_karaoke_position(12000)
    widget.clear()
    qapp.processEvents()
    assert widget.lyric_count == 0
    assert widget.rows == []
    assert widget.container.findChildren(FuriganaWidget) == []
    widget.set_lyrics([LyricLine(0, "New song")])
    qapp.processEvents()
    assert widget.lyric_count == 1
    assert len(widget.rows) == 1
    assert widget.rows[0].lyric == "New song"
    assert widget.rows[0].karaoke_position is None
    assert widget._scroll_axis().value() == 0


def test_animated_seek_finishes_at_measured_destination_and_can_be_interrupted(view, qapp):
    widget, _ = view
    widget.set_lyrics(document())
    widget.set_scroll_animation_speed(40)
    widget.set_current_line(4000)
    QTest.qWait(100)
    assert widget.scroll_animation is None
    assert 4000 in widget._mounted_rows
    assert abs(widget._line_center(4000) - widget._scroll_axis().value() - widget._viewport_extent() / 2) <= 2
    widget.set_current_line(2000)
    widget.set_current_line(50, animate=False)
    QTest.qWait(80)
    assert widget.current_line == 50
    assert 50 in widget._mounted_rows
    assert len(widget.rows) < 40
    assert_viewport_covered(widget)


def test_continuous_scroll_without_duration_uses_virtual_line_positions(view, qapp):
    widget, _ = view
    widget.set_lyrics(document())
    widget.set_scrolling_mode("continuous")
    widget.set_continuous_scroll_options(duration_based=False, nudge_current=True)
    widget.nudge_current_line_into_view(3000)
    widget.update_continuous_scroll(0)
    for position in range(100, 5000, 100):
        widget.update_continuous_scroll(position)
    qapp.processEvents()
    assert widget._scroll_axis().value() > 0
    assert len(widget.rows) < 40
    assert_viewport_covered(widget)
    widget.update_continuous_scroll(0)
    assert widget._continuous_nudge_offset == 0


def test_completed_karaoke_rows_reappear_filled_but_backward_seek_resets_them(view, qapp):
    widget, _ = view
    lines = document(1000)
    lines[600] = LyricLine(1800, "Overlapping", [LyricSegment(1800, "Overlapping", end=1808)], end=1810, has_karaoke=True)
    widget.set_lyrics(lines)
    widget.set_current_line(600, animate=False)
    widget.set_karaoke_position(1802.9)
    widget.set_current_line(601, animate=False)
    widget.set_karaoke_position(1803)
    assert widget._mounted_rows[600].karaoke_position > 1810
    widget._scroll_axis().setValue(0)
    widget._scroll_axis().setValue(int(widget._line_start(600)))
    assert widget._mounted_rows[600].karaoke_position > 1810
    widget.set_current_line(600, animate=False)
    widget.set_karaoke_position(1801)
    assert widget._mounted_rows[600].karaoke_position == 1801


def test_pending_reading_job_can_finish_after_its_row_is_evicted(view, qapp, monkeypatch):
    from concurrent.futures import Future

    widget, _ = view
    jobs = []

    class Executor:
        def submit(self, function):
            future = Future()
            future.set_running_or_notify_cancel()
            jobs.append(future)
            return future

    monkeypatch.setattr("lyrics.furigana_widget._FURIGANA_EXECUTOR", Executor())
    lines = document(1000)
    lines[0] = LyricLine(0, "Async", [LyricSegment(0, "Async")], end=3, has_karaoke=True)
    widget.set_lyrics(lines)
    widget.set_current_line(700, animate=False)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert 0 not in widget._mounted_rows
    for future in jobs:
        # Callback must discard the result without emitting through deleted Qt objects.
        future.set_result([{"text": "Async", "reading": None}])
    qapp.processEvents()
