"""Regression coverage for editor timing, recovery, and save destinations."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from lyrics.editor_source import parse_editor_source
from lyrics.karaoke_timing import segment_end
from lyrics.loader import load_lyrics
from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx


@pytest.mark.parametrize("single", [False, True])
def test_equal_start_chunks_survive_save_and_source_reload(editor, single):
    editor.lines = [LyricLine(2, "ABCD", [LyricSegment(2, "ABCD")])]
    editor._populate_lines(preserve_line_index=0)
    editor.separator_widget.set_content("ABCD", set() if single else {2})
    editor.apply_visual_separation()
    expected = ["ABCD"] if single else ["AB", "CD"]
    assert [s.text for s in editor.lines[0].segments] == expected
    assert editor.save_document()
    assert not editor.save_button.isEnabled()
    reopened = load_lyrics(editor.lrc_path)[0]
    assert reopened.has_karaoke
    assert [(s.text, s.start) for s in reopened.segments] == [(text, 2) for text in expected]
    assert editor.reload_model_from_source()
    assert [s.text for s in editor.lines[0].segments] == expected


def test_equal_start_chunks_keep_ruby_and_visual_rows(tmp_path):
    line = LyricLine(2, "AB\nCD", [
        LyricSegment(2, "AB", ruby="first", ruby_source="manual"),
        LyricSegment(2, "\n"),
        LyricSegment(2, "CD", ruby="second", ruby_source="manual", visual_line=1),
    ], has_karaoke=True)
    path = tmp_path / "same-time.lrcx"
    path.write_text(serialize_lrcx([line]), encoding="utf-8")
    reopened = load_lyrics(path)[0]
    assert reopened.has_karaoke and reopened.text == line.text
    assert [(s.text, s.ruby, s.visual_line) for s in reopened.segments] == [
        ("AB", "first", 0), ("\n", None, 0), ("CD", "second", 1),
    ]


def test_crash_recovery_keeps_equal_start_chunks(editor, make_lyrics_editor, monkeypatch):
    editor.lines = [LyricLine(2, "AB", [LyricSegment(2, "A"), LyricSegment(2, "B")], has_karaoke=True)]
    editor._mark_document_dirty()
    editor._autosave_if_needed()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    recovered = make_lyrics_editor(path=editor.lrc_path, populate=False)
    assert recovered.document_dirty and recovered.lines[0].has_karaoke
    assert [s.text for s in recovered.lines[0].segments] == ["A", "B"]


@pytest.mark.parametrize("action", ["cell", "mark", "nudge"])
def test_plain_lrc_timing_edit_is_saved_and_undoable(tmp_path, make_lyrics_editor, monkeypatch, action):
    path = tmp_path / "plain.lrc"
    path.write_text("[00:02.000]Hello\n[00:05.000]Next\n", encoding="utf-8")
    editor = make_lyrics_editor(path=path, populate=False)
    before = deepcopy(editor.lines)
    editor.segment_table.setCurrentCell(0, 0)
    if action == "cell":
        editor.segment_table.item(0, 0).setText("00:02.500")
    elif action == "mark":
        editor.player = SimpleNamespace(media_player=SimpleNamespace(position=lambda: 2500))
        editor.use_playback_time()
    else:
        editor.karaoke_nudge_step.setValue(500)
        editor.nudge_karaoke_boundary(1)
    assert editor.document_dirty and editor.save_button.isEnabled()
    assert editor.lines[0].has_karaoke and editor.lines[0].segments[0].start == 2.5
    editor.undo()
    assert editor.lines == before and not editor.document_dirty
    editor.redo()
    monkeypatch.setattr(editor, "_choose_lrc_save", lambda target=None: path.with_suffix(".lrcx"))
    assert editor.save_document()
    reopened = load_lyrics(editor.result_path)[0]
    assert reopened.has_karaoke and reopened.segments[0].start == 2.5


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("source_reload", [False, True])
def test_retiming_next_line_preserves_end_semantics(tmp_path, make_lyrics_editor, explicit, source_reload):
    path = tmp_path / "ending.lrcx"
    terminal = "<00:04.000>" if explicit else ""
    path.write_text(f"[00:02.000]AB<00:03.000>CD{terminal}\n[00:05.000]Next\n", encoding="utf-8")
    editor = make_lyrics_editor(path=path, populate=False)
    if source_reload:
        assert editor.reload_model_from_source()
    editor._select_line_index(1)
    editor.line_time.setValue(8)
    assert editor.save_document()
    reopened = load_lyrics(path)
    assert reopened[1].start == 8
    assert segment_end(reopened[0], 1, reopened[1].start) == (4 if explicit else 8)
    editable = parse_editor_source(path.read_text(encoding="utf-8"), ".lrcx")
    assert editable.lines[0].end == (4 if explicit else None)
    assert "<00:05.000>" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("offset", [-500, 500])
def test_offset_applies_to_preview_markers_stamping_and_nudging(editor, monkeypatch, offset):
    positions = []
    monkeypatch.setattr(editor.karaoke_renderer_preview, "set_karaoke_position", positions.append)
    editor.lyrics_offset.setValue(offset)
    editor._refresh_karaoke_preview(3000, rebuild_segments=False)
    assert positions[-1] == 3 - offset / 1000
    assert editor.karaoke_waveform._karaoke_segments[0][:2] == (2200 + offset, 3500 + offset)
    assert editor.lyrics_waveform._selected_lyric_start_ms == 2000 + offset
    editor._update_playback_line_style(2000 + offset - 1)
    assert editor.current_playback_line == -1
    editor._update_playback_line_style(2000 + offset)
    assert editor.current_playback_line == 0

    editor.segment_table.setCurrentCell(0, 0)
    editor.karaoke_nudge_step.setValue(10)
    editor.nudge_karaoke_boundary(1)
    assert editor.lines[0].segments[0].start == pytest.approx(2.21)
    editor._waveform_karaoke_timing_changed("start", 2300 + offset)
    editor._waveform_karaoke_timing_finished()
    assert editor.lines[0].segments[0].start == 2.3
    editor.player = SimpleNamespace(media_player=SimpleNamespace(position=lambda: 2500 + offset))
    editor.use_playback_time()
    assert editor.lines[0].segments[0].start == 2.5
    editor.mark_current_line_playback_time()
    assert editor.lines[0].start == 2.5
    assert editor.lines[0].segments[0].start == 3.0
    assert editor.lines[0].segments[0].end == 4.0
    assert editor.lines[0].end == 4.5


@pytest.mark.parametrize("offset", [-500, 500])
def test_segment_preview_plays_offset_audio_interval(editor, monkeypatch, offset):
    calls = []
    editor.player = SimpleNamespace(media_player=SimpleNamespace(
        duration=lambda: 10000, position=lambda: 0,
        setPosition=lambda value: calls.append(("seek", value)),
        play=lambda: calls.append(("play",)), pause=lambda: calls.append(("pause",)),
    ))
    editor.lyrics_offset.setValue(offset)
    editor.preview_karaoke_segment()
    assert calls == [("seek", 2200 + offset), ("play",)]
    editor.preview_position_changed(3500 + offset - 1)
    assert calls[-1] == ("play",)
    editor.preview_position_changed(3500 + offset)
    assert calls[-1] == ("pause",)


@pytest.mark.parametrize("offset", [-500, 500])
def test_lyric_waveform_commits_offset_coordinates(editor, offset):
    editor.lyrics_offset.setValue(offset)
    editor._waveform_lyric_timing_changed("start", 2500 + offset)
    assert editor.line_time.value() == 2.5
    assert editor.lyrics_waveform._selected_lyric_start_ms == 2500 + offset
    editor.commit_line_timestamp()
    assert editor.lines[0].start == 2.5
    assert editor.lines[0].segments[0].start == 2.7
    assert editor.lines[0].segments[0].end == 4.0
    assert editor.lines[0].end == 4.5
    editor._waveform_lyric_timing_changed("end", 5500 + offset)
    editor.commit_line_timestamp()
    assert editor.lines[1].start == 5.5


@pytest.mark.parametrize("choice", ["Cancel", "Save as LRCX", "Save as LRC anyway"])
def test_txt_to_lrc_checks_feature_loss(tmp_path, make_lyrics_editor, monkeypatch, choice):
    path = tmp_path / "plain.txt"
    path.write_text("ABCD\n", encoding="utf-8")
    editor = make_lyrics_editor(path=path, populate=False)
    editor.separator_widget.set_content("ABCD", {2})
    editor.apply_visual_separation()
    editor.segment_table.item(0, 1).setText("00:01.000")
    editor.segment_table.item(1, 0).setText("00:01.000")
    editor.segment_table.item(1, 1).setText("00:02.000")
    destination = path.with_suffix(".lrc")
    monkeypatch.setattr(editor, "_choose_txt_conversion_save", lambda: destination)
    warnings = []

    def inspect_warning(box):
        warnings.append(box.informativeText())
        assert "Karaoke timing" in box.informativeText()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", inspect_warning)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: next(
        button for button in box.buttons() if button.text().replace("&", "") == choice
    ))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path.with_suffix(".lrcx")), ""))
    result = editor.save_document()
    assert len(warnings) == 1
    assert path.read_text(encoding="utf-8") == "ABCD\n"
    if choice == "Cancel":
        assert not result and editor.document_dirty
        assert not destination.exists() and not path.with_suffix(".lrcx").exists()
    else:
        assert result and not editor.document_dirty
        reopened = load_lyrics(editor.result_path)[0]
        assert reopened.has_karaoke == (choice == "Save as LRCX")


@pytest.fixture
def player_for_editor(qapp, tmp_path):
    player = QWidget()
    player.current_audio_path = tmp_path / "original.mp3"
    player.media_player = QMediaPlayer(player)
    player.loaded_lyrics = []
    player.load_lrc_file = player.loaded_lyrics.append
    yield player
    player.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("advance", ["before_save", "before_callback", "unchanged"])
def test_save_refresh_only_updates_original_song(editor, player_for_editor, tmp_path, qapp, advance):
    player = player_for_editor
    dialog = type(editor)(tmp_path / "original.lrcx", player)
    try:
        dialog.lines = [LyricLine(2, "Original")]
        dialog._mark_document_dirty()
        if advance == "before_save":
            player.current_audio_path = tmp_path / "next.mp3"
        assert dialog.save_document()
        if advance == "before_callback":
            player.current_audio_path = tmp_path / "next.mp3"
        qapp.processEvents()
        assert player.loaded_lyrics == ([str(dialog.result_path)] if advance == "unchanged" else [])
        assert load_lyrics(dialog.result_path)[0].text == "Original"
    finally:
        dialog.document_dirty = False
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("advance", [False, True])
def test_save_and_close_does_not_reload_previous_song(editor, player_for_editor, tmp_path, monkeypatch, qapp, advance):
    import ui.main_window as main_window

    player = player_for_editor
    path = tmp_path / "original.lrcx"
    player.get_current_lyrics_path = lambda: path

    def save_and_close(dialog):
        dialog.lines = [LyricLine(2, "Original")]
        dialog._mark_document_dirty()
        if advance:
            player.current_audio_path = tmp_path / "next.mp3"
        assert dialog.save_and_close()
        return dialog.result()

    monkeypatch.setattr("ui.lrc_editor.LrcEditorDialog", type(editor))
    monkeypatch.setattr(type(editor), "exec", save_and_close)
    main_window.Player.edit_current_lyrics(player)
    qapp.processEvents()
    assert bool(player.loaded_lyrics) == (not advance)
    assert load_lyrics(path)[0].text == "Original"
