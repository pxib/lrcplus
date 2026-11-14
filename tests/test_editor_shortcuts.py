from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox

from lyrics.models import LyricLine


def focus_editor(editor, qapp, widget):
    editor.show()
    editor.activateWindow()
    widget.setFocus()
    qapp.processEvents()


@pytest.mark.parametrize("karaoke", [False, True])
def test_lyrics_timing_keys_seek_from_list_and_buttons(make_lyrics_editor, qapp, karaoke):
    editor = make_lyrics_editor(karaoke=karaoke)
    seeks = []
    editor.player = SimpleNamespace(media_player=SimpleNamespace(
        position=lambda: 3000, duration=lambda: 10000,
    ))
    editor.seek_preview = seeks.append
    for widget in (editor.line_list, editor.lyrics_play_button):
        focus_editor(editor, qapp, widget)
        for key in (Qt.Key.Key_Q, Qt.Key.Key_E, Qt.Key.Key_A, Qt.Key.Key_D):
            QTest.keyClick(widget, key)
    assert seeks == [2900, 3100, 2000, 4000] * 2


def test_space_on_play_button_toggles_playback_once(make_lyrics_editor, qapp, monkeypatch):
    editor = make_lyrics_editor(karaoke=False)
    calls = []
    monkeypatch.setattr(editor, "toggle_preview", lambda: calls.append(True))
    # The button was already connected before monkeypatching; a second call
    # through its clicked signal would be visible through preview_wants_playing.
    media_calls = []
    editor.player = SimpleNamespace(media_player=SimpleNamespace(
        play=lambda: media_calls.append("play"), pause=lambda: media_calls.append("pause"),
    ))
    focus_editor(editor, qapp, editor.lyrics_play_button)
    QTest.keyClick(editor.lyrics_play_button, Qt.Key.Key_Space)
    assert calls == [True]
    assert media_calls == []


@pytest.mark.parametrize("key,modifiers", [
    (Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier),
    (Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier),
    (Qt.Key.Key_Enter, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier),
])
def test_mark_shortcut_respects_offset_advances_and_is_undoable(
    make_lyrics_editor, qapp, key, modifiers,
):
    editor = make_lyrics_editor(karaoke=False)
    editor.player = SimpleNamespace(media_player=SimpleNamespace(position=lambda: 3500))
    editor.lyrics_offset.setValue(500)
    editor._reset_history_baseline()
    before = deepcopy(editor.lines)
    editor.sidebar_sort_combo.setCurrentIndex(2)
    widget = editor.line_list if key == Qt.Key.Key_F else editor.line_text
    focus_editor(editor, qapp, widget)
    QTest.keyClick(widget, key, modifiers)
    assert editor.lines[0].start == 3
    assert editor._current_line_index() == 1
    assert editor.document_dirty
    assert len(editor._undo_stack) == 1
    editor.undo()
    assert editor.lines == before
    assert editor._current_line_index() == 0


@pytest.mark.parametrize("widget_name", ["line_text", "lyric_search"])
def test_timing_letters_remain_normal_text(make_lyrics_editor, qapp, monkeypatch, widget_name):
    editor = make_lyrics_editor(karaoke=False)
    calls = []
    monkeypatch.setattr(editor, "_seek_preview_relative", lambda delta: calls.append(delta))
    monkeypatch.setattr(editor, "mark_current_line_playback_time", lambda: calls.append("mark"))
    monkeypatch.setattr(editor, "toggle_preview", lambda: calls.append("play"))
    widget = getattr(editor, widget_name)
    focus_editor(editor, qapp, widget)
    widget.selectAll()
    QTest.keyClicks(widget, "q e a d f")
    text = widget.toPlainText() if widget_name == "line_text" else widget.text()
    assert text == "q e a d f"
    assert calls == []


def test_timing_letters_do_not_intercept_timestamp_or_reading_inputs(editor, qapp, monkeypatch):
    editor._select_line_index(1)
    calls = []
    monkeypatch.setattr(editor, "_seek_preview_relative", lambda delta: calls.append(delta))
    monkeypatch.setattr(editor, "mark_current_line_playback_time", lambda: calls.append("mark"))
    inline = QLineEdit(editor.furigana_table.viewport())
    for widget in (editor.line_time, editor.line_time.lineEdit(), editor.lyrics_offset, inline):
        focus_editor(editor, qapp, widget)
        for key in (Qt.Key.Key_Q, Qt.Key.Key_E, Qt.Key.Key_A, Qt.Key.Key_D, Qt.Key.Key_F):
            QTest.keyClick(widget, key)
    assert calls == []


def test_navigation_follows_sidebar_order_and_preserves_edits(make_lyrics_editor, qapp):
    editor = make_lyrics_editor(karaoke=False)
    editor.lines = [LyricLine(2, "First"), LyricLine(8, "Last"), LyricLine(5, "Middle")]
    editor.sidebar_sort_combo.setCurrentIndex(2)
    editor._populate_lines(preserve_line_index=1)
    focus_editor(editor, qapp, editor.line_text)
    editor.line_text.setPlainText("Edited last")
    QTest.keyClick(editor.line_text, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert editor._current_line_index() == 2
    assert editor.line_text.toPlainText() == "Middle"
    QTest.keyClick(editor.line_text, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    QTest.keyClick(editor.line_text, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert editor._current_line_index() == 1
    assert editor.lines[1].text == "Edited last"


def test_add_move_and_list_delete_shortcuts_are_undoable(make_lyrics_editor, qapp):
    editor = make_lyrics_editor(karaoke=False)
    before = deepcopy(editor.lines)
    focus_editor(editor, qapp, editor.line_text)
    QTest.keyClick(editor.line_text, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    added = editor.lines[1]
    assert len(editor.lines) == 3
    assert editor._current_line_index() == 1
    modifiers = Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier
    QTest.keyClick(editor.line_text, Qt.Key.Key_Up, modifiers)
    assert editor.lines[0] is added
    assert editor._current_line_index() == 0
    QTest.keyClick(editor.line_text, Qt.Key.Key_Down, modifiers)
    assert editor.lines[1] is added
    editor.line_list.setFocus()
    QTest.keyClick(editor.line_list, Qt.Key.Key_Delete)
    assert editor.lines == before
    for _ in range(4):
        editor.undo()
    assert editor.lines == before
    assert not editor.document_dirty


def test_commit_shortcut_applies_provisional_waveform_boundary(make_lyrics_editor, qapp):
    editor = make_lyrics_editor(karaoke=False)
    focus_editor(editor, qapp, editor.line_time)
    editor._waveform_lyric_preview_handle = "end"
    editor._waveform_lyric_preview_ms = 6500
    QTest.keyClick(editor.line_time, Qt.Key.Key_Return,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert editor.lines[1].start == 6.5
    assert editor._waveform_lyric_preview_handle is None
    editor.undo()
    assert editor.lines[1].start == 5


def test_holding_mark_key_does_not_stamp_multiple_lines(make_lyrics_editor, qapp):
    editor = make_lyrics_editor(karaoke=False)
    editor.player = SimpleNamespace(media_player=SimpleNamespace(position=lambda: 3000))
    focus_editor(editor, qapp, editor.line_list)
    QTest.keyClick(editor.line_list, Qt.Key.Key_F)
    repeated = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F,
                         Qt.KeyboardModifier.NoModifier, "f", True)
    qapp.sendEvent(editor.line_list, repeated)
    assert [line.start for line in editor.lines] == [3, 5]
    assert len(editor._undo_stack) == 1


def test_lyrics_shortcuts_are_scoped_to_lyrics_tab_and_editor_window(editor, qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(editor, "add_line", lambda: calls.append("add"))
    monkeypatch.setattr(editor, "mark_current_line_playback_time", lambda: calls.append("mark"))
    monkeypatch.setattr(editor, "use_playback_time", lambda: calls.append("karaoke"))
    focus_editor(editor, qapp, editor.line_list)
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    QTest.keyClick(editor.line_list, Qt.Key.Key_F)
    QTest.keyClick(editor.line_list, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert calls == []
    editor.tabs.setCurrentWidget(editor.karaoke_scroll)
    editor.segment_table.setCurrentCell(0, 0)
    editor.segment_table.setFocus()
    qapp.processEvents()
    QTest.keyClick(editor.segment_table, Qt.Key.Key_F)
    QTest.keyClick(editor.segment_table, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert calls == ["karaoke"]
    editor.tabs.setCurrentWidget(editor.lyrics_page)
    child = QDialog(editor)
    try:
        focus_editor(child, qapp, child)
        QTest.keyClick(child, Qt.Key.Key_F)
        QTest.keyClick(child, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        assert calls == ["karaoke"]
    finally:
        child.close()


@pytest.mark.parametrize("karaoke", [False, True])
def test_shortcut_reference_is_available_without_karaoke(make_lyrics_editor, qapp, monkeypatch, karaoke):
    editor = make_lyrics_editor(karaoke=karaoke)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[1:]))
    focus_editor(editor, qapp, editor.line_list)
    assert editor.lyrics_shortcuts_button.isVisible()
    editor.lyrics_shortcuts_button.click()
    assert messages[0][0] == "Lyrics Editor Shortcuts"
    assert "Ctrl+Enter" in messages[0][1]
    assert "Ctrl+S" in messages[0][1]
    assert "Shift+click waveform" in messages[0][1]
    assert "disabled while typing" in messages[0][1]
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    assert not editor.lyrics_shortcuts_button.isVisible()
    if karaoke:
        editor.tabs.setCurrentWidget(editor.karaoke_scroll)
        assert not editor.lyrics_shortcuts_button.isVisible()
        assert editor.karaoke_shortcuts_button.isVisible()


@pytest.mark.parametrize("karaoke", [False, True])
@pytest.mark.parametrize("zoom,pan_ms", [(1, 0), (4, 7000)])
def test_shift_click_waveform_plays_from_clicked_audio_time_without_editing(
    make_lyrics_editor, qapp, karaoke, zoom, pan_ms,
):
    editor = make_lyrics_editor(karaoke=karaoke)
    editor.lyrics_offset.setValue(500)
    editor.document_dirty = False
    editor._reset_history_baseline()
    editor.lyrics_transport_toggle.setChecked(True)
    waveform = editor.lyrics_waveform
    focus_editor(editor, qapp, waveform)
    waveform.set_duration(20000)
    waveform._lyric_zoom = zoom
    waveform._lyric_pan_ms = pan_ms
    before = deepcopy(editor.lines)
    markers = (waveform._lyric_preview_start_ms, waveform._lyric_preview_end_ms)
    calls = []
    editor.player = SimpleNamespace(media_player=SimpleNamespace(
        setPosition=lambda position: calls.append(("seek", position)),
        play=lambda: calls.append(("play",)),
        pause=lambda: calls.append(("pause",)),
        position=lambda: 0,
        duration=lambda: 20000,
    ))
    editor._segment_preview_range = (2200, 3500)
    x = 8 + round((waveform.width() - 16) * 0.6)
    position = QPoint(x, waveform.height() // 2)
    expected_ms = round(pan_ms + (x - 8) / (waveform.width() - 16) * (20000 / zoom))

    QTest.mouseClick(waveform, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ShiftModifier, position)

    assert calls == [("seek", expected_ms), ("play",)]
    assert editor._segment_preview_range is None
    # Playback should continue past any earlier segment preview's end.
    editor.preview_position_changed(expected_ms + 100)
    assert calls == [("seek", expected_ms), ("play",)]
    assert editor.lines == before
    assert not editor.document_dirty
    assert not editor._undo_stack
    assert editor._waveform_lyric_preview_handle is None
    assert (waveform._lyric_preview_start_ms, waveform._lyric_preview_end_ms) == markers
    assert waveform._lyric_drag_handle is None


def test_plain_waveform_click_still_edits_timing(make_lyrics_editor, qapp):
    editor = make_lyrics_editor(karaoke=False)
    editor.lyrics_transport_toggle.setChecked(True)
    waveform = editor.lyrics_waveform
    focus_editor(editor, qapp, waveform)
    waveform.set_duration(10000)
    timing = []
    playback = []
    waveform.lyricTimingRequested.connect(lambda handle, value: timing.append((handle, value)))
    waveform.playFromRequested.connect(playback.append)

    QTest.mouseClick(waveform, Qt.MouseButton.LeftButton,
                     pos=QPoint(waveform.width() // 2, waveform.height() // 2))

    assert len(timing) == 1 and timing[0][0] == "start"
    assert playback == []


def test_shift_click_keeps_karaoke_waveform_timing_behavior(editor, qapp):
    editor.tabs.setCurrentWidget(editor.karaoke_scroll)
    editor.karaoke_transport_toggle.setChecked(True)
    waveform = editor.karaoke_waveform
    focus_editor(editor, qapp, waveform)
    waveform.set_duration(10000)
    timing = []
    playback = []
    waveform.karaokeTimingRequested.connect(lambda handle, value: timing.append((handle, value)))
    waveform.playFromRequested.connect(playback.append)

    QTest.mouseClick(waveform, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ShiftModifier,
                     QPoint(waveform.width() // 2, waveform.height() // 2))

    assert len(timing) == 1 and timing[0][0] == "start"
    assert playback == []


def test_shift_click_without_audio_does_not_play_or_change_timing(make_lyrics_editor, qapp):
    editor = make_lyrics_editor(karaoke=False)
    editor.lyrics_transport_toggle.setChecked(True)
    waveform = editor.lyrics_waveform
    focus_editor(editor, qapp, waveform)
    playback = []
    waveform.playFromRequested.connect(playback.append)
    before = deepcopy(editor.lines)

    QTest.mouseClick(waveform, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ShiftModifier,
                     QPoint(waveform.width() // 2, waveform.height() // 2))

    assert playback == []
    assert editor.lines == before
    assert not editor.document_dirty
