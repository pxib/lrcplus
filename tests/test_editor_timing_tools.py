from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QLabel, QListWidget, QProgressDialog, QPushButton, QSpinBox,
)

import ui.lrc_editor as editor_module
import ui.lyrics_editor_document as document_module
from lyrics.loader import load_lyrics
from lyrics.models import LyricLine

def test_consecutive_shifts_undo_and_redo_one_operation_at_a_time(editor):
    original = deepcopy(editor.lines)
    editor._apply_timing_shift(500, 0, 1)
    first_shift = deepcopy(editor.lines)
    editor._apply_timing_shift(250, 0, 0)
    second_shift = deepcopy(editor.lines)
    assert editor.undo_button.isEnabled()
    editor.undo()
    assert editor.lines == first_shift
    editor.undo()
    assert editor.lines == original
    assert not editor.undo_button.isEnabled()
    assert not editor.document_dirty
    editor.redo()
    assert editor.lines == first_shift
    editor.redo()
    assert editor.lines == second_shift
    assert editor.document_dirty and not editor.redo_button.isEnabled()


def test_drift_dialog_previews_before_editing_and_applies_as_one_undo(editor, tmp_path, monkeypatch):
    editor.lines = [LyricLine(time, f"Verse {number}") for number, time in enumerate((5, 15, 25, 35), 1)]
    editor._populate_lines()
    editor._reset_history_baseline()
    reference = tmp_path / "reference.srt"
    reference.write_text("".join(
        f"{number}\n00:00:{time:02},000 --> 00:00:{time + 1:02},000\nVerse {number}\n\n"
        for number, time in enumerate((6, 17, 28, 39), 1)
    ), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(reference), ""))
    seen = []
    playback = []
    track = tmp_path / "song.wav"
    media = SimpleNamespace(
        duration=lambda: 45000,
        position=lambda: 2500,
        playbackState=lambda: QMediaPlayer.PlaybackState.PausedState,
        pause=lambda: playback.append("pause"),
        setPosition=lambda position: playback.append(position),
    )

    def inspect_preview(dialog):
        matches = dialog.findChild(QListWidget)
        assert matches.count() == 4
        assert "00:05.000 → 00:06.000" in matches.item(0).text()
        assert [line.start for line in editor.lines] == [5, 15, 25, 35]
        matches.setCurrentRow(1)
        listen = next(button for button in dialog.findChildren(QPushButton)
                      if button.text() == "Listen at proposed time")
        assert listen.isEnabled()
        listen.click()
        return seen.pop()

    monkeypatch.setattr(QDialog, "exec", inspect_preview)
    with monkeypatch.context() as transport:
        transport.setattr(editor, "player", SimpleNamespace(media_player=media, current_audio_path=track))
        transport.setattr(editor, "_editing_song_path", editor._normalise_song_path(track))
        transport.setattr(editor, "_start_segment_preview", lambda *times: playback.append(times))
        seen.append(QDialog.DialogCode.Rejected)
        editor.show_timing_drift_dialog()
        assert not editor.document_dirty
        assert playback == [(16000, 19500, 17000), "pause", 2500]
        seen.append(QDialog.DialogCode.Accepted)
        editor.show_timing_drift_dialog()
    assert [line.start for line in editor.lines] == [6, 17, 28, 39]
    assert editor.document_dirty
    editor.undo()
    assert [line.start for line in editor.lines] == [5, 15, 25, 35]
    assert not editor.document_dirty


def test_drift_dialog_can_apply_and_undo_correction_from_an_lrcx_backup(editor, tmp_path, monkeypatch):
    editor.lines = [LyricLine(time, f"Verse {number}") for number, time in enumerate((5, 15, 25, 35), 1)]
    editor._populate_lines()
    editor.lyrics_offset.setValue(18400)
    editor._reset_history_baseline()
    backup = tmp_path / "song.lrcx.bak"
    backup.write_text("".join(f"[00:{time:02}.000]Verse {number}\n"
                              for number, time in enumerate((5, 15, 25, 35), 1)), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(backup), ""))

    def approve(dialog):
        assert any("from 18400 to 0 ms" in label.text() for label in dialog.findChildren(QLabel))
        assert "00:23.400 → 00:05.000" in dialog.findChild(QListWidget).item(0).text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", approve)
    editor.show_timing_drift_dialog()
    assert editor.lyrics_offset.value() == editor.lyrics_offset_ms == 0
    assert [line.start for line in editor.lines] == [5, 15, 25, 35]
    editor.undo()
    assert editor.lyrics_offset.value() == editor.lyrics_offset_ms == 18400


def test_automatic_drift_uses_the_loaded_tracks_caption_and_applies_without_a_picker(editor, tmp_path, monkeypatch):
    audio = tmp_path / "song.mp3"
    audio.touch()
    reference = tmp_path / "song.en.manual.srt"
    reference.write_text("".join(
        f"{number}\n00:00:{time:02},000 --> 00:00:{time + 1:02},000\nVerse {number}\n\n"
        for number, time in enumerate((6, 17, 28, 39), 1)
    ), encoding="utf-8")
    editor.lines = [LyricLine(time, f"Verse {number}") for number, time in enumerate((5, 15, 25, 35), 1)]
    editor._populate_lines()
    editor._reset_history_baseline()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: pytest.fail("opened a file picker"))
    monkeypatch.setattr("core.audio_timing_scan._vocal_envelope", lambda *_: pytest.fail("ignored nearby captions"))
    action = next(item for item in editor.findChildren(QAction) if item.text() == "Correct Timing Drift…")
    with monkeypatch.context() as transport:
        transport.setattr(editor, "player", SimpleNamespace(current_audio_path=audio))
        transport.setattr(editor, "_editing_song_path", editor._normalise_song_path(audio))
        action.trigger()
        assert [line.start for line in editor.lines] == [6, 17, 28, 39]
        assert "Timing corrected" in editor.timing_drift_status.text()
        assert editor.document_dirty
        action.trigger()
        assert "already follow" in editor.timing_drift_status.text()
    editor.undo()
    assert [line.start for line in editor.lines] == [5, 15, 25, 35]
    assert not editor.document_dirty


def test_cancelling_automatic_drift_does_not_apply_a_late_result(editor, tmp_path, monkeypatch):
    audio = tmp_path / "song.mp3"
    audio.touch()
    completed = []

    def scan(_audio, lines, _offset, _duration, cancelled):
        completed.append(cancelled)
        cancelled.wait(1)
        return lines, 1000

    monkeypatch.setattr("ui.lyrics_timing_tools.correct_audio_timing", scan)

    def cancel():
        editor.findChild(QProgressDialog).findChild(QPushButton).click()

    with monkeypatch.context() as transport:
        transport.setattr(editor, "player", SimpleNamespace(current_audio_path=audio))
        transport.setattr(editor, "_editing_song_path", editor._normalise_song_path(audio))
        QTimer.singleShot(50, cancel)
        editor.correct_current_timing_drift()
    assert completed and completed[0].is_set()
    assert editor.lyrics_offset_ms == 0
    assert not editor.document_dirty


def test_new_edit_after_undo_discards_redo_branch(editor):
    editor._apply_timing_shift(500, 0, 0)
    editor.undo()
    editor._apply_timing_shift(100, 0, 0)
    editor.redo()
    assert editor.lines[0].start == 2.1
    assert not editor.redo_button.isEnabled()


def test_invalid_shift_changes_neither_document_nor_history(editor):
    before = deepcopy(editor.lines)
    with pytest.raises(ValueError):
        editor._apply_timing_shift(-2500, 0, 1)
    assert editor.lines == before
    assert not editor._undo_stack
    assert not editor.document_dirty


def test_manual_segment_edit_can_be_undone(editor):
    before = deepcopy(editor.lines)
    item = editor.segment_table.item(0, 0)
    assert item is not None
    item.setText("2.600")
    assert editor.lines[0].segments[0].start == 2.6
    editor.undo()
    assert editor.lines == before


def test_instant_fill_checkbox_can_be_undone(editor):
    checkbox = editor.segment_table.cellWidget(0, 4)
    checkbox.setChecked(True)
    assert editor.lines[0].segments[0].instant
    editor.undo()
    assert not editor.lines[0].segments[0].instant


def test_shift_undo_restores_selection_when_sidebar_is_sorted(editor):
    editor.sidebar_sort_mode = "timestamp_desc"
    editor._populate_lines(preserve_line_index=1)
    editor._apply_timing_shift(500, 1, 1)
    editor.undo()
    assert editor._current_line_index() == 1


def test_failed_save_keeps_editor_open_dirty_and_recoverable(editor, monkeypatch):
    editor.document_dirty = True
    editor._autosave_if_needed()
    recovery = editor._autosave_path.read_text(encoding="utf-8")
    warnings = []

    def locked(*args, **kwargs):
        raise PermissionError("Locked destination")

    monkeypatch.setattr(document_module, "atomic_write_text", locked)
    monkeypatch.setattr(editor_module.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    editor.save_and_close()
    assert editor.document_dirty
    assert editor.result_path is None
    assert editor._autosave_path.read_text(encoding="utf-8") == recovery
    assert "Locked destination" in warnings[0]


def test_shifted_document_saves_and_reopens_with_backup(editor):
    original_file = "[00:00.000]Previous file\n"
    editor.lrc_path.write_text(original_file, encoding="utf-8")
    editor._apply_timing_shift(500, 0, 1)
    expected = deepcopy(editor.lines)
    editor.save_and_close()
    restored = load_lyrics(editor.result_path)
    assert [line.start for line in restored] == [line.start for line in expected]
    assert restored[0].segments[0].start == expected[0].segments[0].start
    assert restored[0].segments[0].end == expected[0].segments[0].end
    assert editor.lrc_path.with_name("song.lrcx.bak").read_text(encoding="utf-8") == original_file
    assert not editor.document_dirty


def test_timing_tools_parse_pending_source_edits_before_shifting(editor):
    editor.source_editor.setPlainText("[00:10.000]Raw source edit\n")
    assert editor._prepare_timing_tools()
    editor._apply_timing_shift(250, 0, 0)
    assert editor.lines[0].start == 10.25
    assert editor.lines[0].text == "Raw source edit"


def test_shift_dialog_previews_and_applies_range(editor, monkeypatch):
    def interact(dialog):
        first, last, delta = dialog.findChildren(QSpinBox)
        first.setValue(2)
        last.setValue(2)
        delta.setValue(250)
        apply_button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Apply)
        assert apply_button.isEnabled()
        assert any("5.250" in label.text() for label in dialog.findChildren(QLabel))
        apply_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.show_timing_shift_dialog()
    assert [line.start for line in editor.lines] == [2, 5.25]


def test_validation_dialog_can_navigate_to_affected_line(editor, monkeypatch):
    editor.lines[1].end = 4

    def interact(dialog):
        results = dialog.findChild(QListWidget)
        assert results.count() >= 1
        assert results.item(0).text().startswith("Line 2:")
        results.itemDoubleClicked.emit(results.item(0))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.show_timing_validation()
    assert editor._current_line_index() == 1


def test_validation_dialog_reports_audio_overrun_from_lyrics_offset(editor, monkeypatch):
    editor.lines = [LyricLine(9.8, "Late lyric")]
    editor._populate_lines(preserve_line_index=0)
    editor.lyrics_offset.setValue(500)
    editor.player = SimpleNamespace(media_player=SimpleNamespace(duration=lambda: 10000))
    messages = []

    def interact(dialog):
        results = dialog.findChild(QListWidget)
        messages.extend(results.item(row).text() for row in range(results.count()))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", interact)
    monkeypatch.setattr(editor_module.QMessageBox, "information", lambda *args: None)
    try:
        editor.show_timing_validation()
    finally:
        editor.player = None
    assert any("Line 1:" in message and "audio duration" in message for message in messages)
