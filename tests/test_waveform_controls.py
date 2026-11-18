"""Visible buttons, keyboard controls, and gestures share one waveform view."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest

from lyrics.waveform import WaveformWidget
from ui.lrc_editor_controls import WaveformControls


@pytest.fixture(params=["lyrics", "karaoke"])
def waveform(qapp, request):
    widget = WaveformWidget(shift_click_playback=request.param == "lyrics")
    widget.resize(700, 100)
    widget.set_duration(20000)
    if request.param == "karaoke":
        widget.set_karaoke_segments([(2, 3, "A"), (3, 4, "B")],
                                    selected_index=0, line_start_ms=2000, line_end_ms=5000)
        widget.set_karaoke_timing(2000, 3000, 0)
    else:
        widget.set_selected_lyric_timing(2000, 4000)
    widget.fit_selection()
    yield widget
    widget.close()
    widget.deleteLater()


def test_visible_navigation_preserves_timing_and_stays_inside_audio(waveform):
    controls = WaveformControls(waveform)
    timing = QSignalSpy(waveform.karaokeTimingRequested)
    lyric_timing = QSignalSpy(waveform.lyricTimingRequested)
    seek = QSignalSpy(waveform.seekRequested)
    before = deepcopy(waveform._karaoke_segments)
    try:
        start, end = waveform._waveform_view()
        controls.zoom_in.click()
        zoom_start, zoom_end = waveform._waveform_view()
        assert zoom_end - zoom_start == pytest.approx((end - start) / 1.2)
        assert (zoom_start + zoom_end) / 2 == pytest.approx((start + end) / 2)
        controls.pan_right.click()
        assert waveform._waveform_view()[0] > zoom_start
        controls.fit_line.click()
        assert waveform._waveform_view() == pytest.approx((start, end))
        controls.all_audio.click()
        assert waveform._waveform_view() == pytest.approx((0, 20000))
        controls.zoom_out.click()
        controls.pan_left.click()
        assert waveform._waveform_view() == pytest.approx((0, 20000))
        controls.zoom_in.click()
        assert waveform._waveform_view() == pytest.approx((20000 / 12, 20000 - 20000 / 12))
        for _ in range(50):
            controls.pan_right.click()
        assert waveform._waveform_view()[1] == pytest.approx(20000)
        assert waveform._karaoke_segments == before
        assert timing.count() == lyric_timing.count() == seek.count() == 0
    finally:
        controls.deleteLater()


def test_keyboard_navigation_uses_the_same_view(waveform):
    before = waveform._waveform_view()
    QTest.keyClick(waveform, Qt.Key.Key_Plus)
    start, end = waveform._waveform_view()
    assert end - start < before[1] - before[0]
    QTest.keyClick(waveform, Qt.Key.Key_Right)
    assert waveform._waveform_view()[0] > start
    QTest.keyClick(waveform, Qt.Key.Key_Home)
    assert waveform._waveform_view() == pytest.approx(before)
    QTest.keyClick(waveform, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
    assert waveform._waveform_view() == pytest.approx((0, 20000))


def test_ctrl_wheel_keeps_audio_position_under_cursor(waveform, qapp):
    ratio = 0.7
    x = 8 + ratio * (waveform.width() - 16)
    start, end = waveform._waveform_view()
    anchor = start + ratio * (end - start)
    event = QWheelEvent(QPointF(x, 50), QPointF(x, 50), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(waveform, event)
    new_start, new_end = waveform._waveform_view()
    assert new_end - new_start < end - start
    assert new_start + ratio * (new_end - new_start) == pytest.approx(anchor)


def test_ctrl_click_seeks_without_moving_a_boundary(waveform):
    seek = QSignalSpy(waveform.seekRequested)
    timing = QSignalSpy(waveform.karaokeTimingRequested)
    lyric_timing = QSignalSpy(waveform.lyricTimingRequested)
    preview = QSignalSpy(waveform.segmentPlayRequested)
    x = waveform.width() // 2
    expected = waveform._ms_for_x(x)
    before = deepcopy(waveform._karaoke_segments)
    QTest.mouseClick(waveform, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier, QPoint(x, 50))
    assert seek.count() == 1 and seek.at(0) == [expected]
    assert timing.count() == lyric_timing.count() == preview.count() == 0
    assert waveform._karaoke_segments == before


def test_ctrl_right_click_previews_clicked_segment_without_editing(qapp):
    waveform = WaveformWidget()
    waveform.resize(700, 100)
    waveform.set_duration(20000)
    waveform.set_karaoke_segments([(2, 3, "A"), (3, 4, "B")],
                                  selected_index=0, line_start_ms=2000, line_end_ms=5000)
    waveform.fit_selection()
    preview = QSignalSpy(waveform.segmentPlayRequested)
    timing = QSignalSpy(waveform.karaokeTimingRequested)
    try:
        QTest.mouseClick(waveform, Qt.MouseButton.RightButton,
                         Qt.KeyboardModifier.ControlModifier, QPoint(round(waveform._x_for_ms(3500)), 50))
        assert preview.count() == 1 and preview.at(0) == [3000, 4000]
        assert timing.count() == 0
    finally:
        waveform.deleteLater()


@pytest.mark.parametrize("karaoke", [False, True])
def test_editor_exposes_waveform_toolbar_only_in_waveform_mode(make_lyrics_editor, karaoke):
    editor = make_lyrics_editor(karaoke=karaoke)
    controls = editor.lyrics_waveform_controls
    assert controls.isHidden()
    editor.lyrics_transport_toggle.setChecked(True)
    assert not controls.isHidden()
    assert editor.lyrics_transport_toggle.text() == "Waveform"
    editor.lyrics_transport_toggle.setChecked(False)
    assert controls.isHidden()
    if karaoke:
        assert editor.karaoke_waveform_controls.isHidden()
        editor.karaoke_transport_toggle.setChecked(True)
        assert not editor.karaoke_waveform_controls.isHidden()
        assert "preview segment" in editor.karaoke_waveform_controls.hint.text()


def test_space_activates_focused_navigation_button_without_toggling_playback(editor, qapp, monkeypatch):
    editor.lyrics_transport_toggle.setChecked(True)
    editor.lyrics_waveform.set_duration(20000)
    editor.lyrics_waveform.show_full_audio()
    calls = []
    monkeypatch.setattr(editor, "toggle_preview", lambda: calls.append("play"))
    editor.show()
    editor.activateWindow()
    button = editor.lyrics_waveform_controls.zoom_in
    button.setFocus()
    qapp.processEvents()
    QTest.keyClick(button, Qt.Key.Key_Space)
    start, end = editor.lyrics_waveform._waveform_view()
    assert end - start < 20000
    assert calls == []
    assert editor.lyrics_waveform.hasFocus()
