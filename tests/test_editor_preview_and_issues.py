from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest

from lyrics.models import LyricLine
from test_karaoke_editor import FakeMedia


def attach_media(editor):
    media = FakeMedia()
    editor.player = SimpleNamespace(media_player=media)
    return media


def test_loop_restarts_on_deadline_without_position_notifications(editor):
    media = attach_media(editor)
    editor.preview_loop.setChecked(True)
    expired = QSignalSpy(editor._segment_preview_timer.timeout)
    editor.play_waveform_segment(2200, 2240)
    assert expired.wait(1000)
    assert media.playing and media.position() == 2200
    assert editor._segment_preview_range == (2200, 2240)
    assert editor._segment_preview_timer.isActive()
    editor.seek_preview(4000)
    assert editor._segment_preview_range is None
    assert not editor._segment_preview_timer.isActive()


def test_loop_padding_is_clamped_and_does_not_accumulate(editor):
    media = attach_media(editor)
    editor.preview_loop.setChecked(True)
    editor.preview_lead_in.setValue(0.5)
    editor.preview_lead_out.setValue(0.5)
    editor.play_waveform_segment(200, 9800)
    assert editor._segment_preview_range == (0, 10000)
    editor.preview_position_changed(10050)
    assert media.playing and media.position() == 0
    assert editor._segment_preview_range == (0, 10000)
    editor.preview_loop.setChecked(False)
    editor.preview_position_changed(10000)
    assert not media.playing and media.position() == 10000
    assert editor._segment_preview_range is None


def test_pause_and_close_stop_looping(editor):
    media = attach_media(editor)
    editor.preview_loop.setChecked(True)
    editor.play_waveform_segment(2200, 3500)
    editor.preview_wants_playing = True
    editor.toggle_preview()
    assert not media.playing
    assert editor._segment_preview_range is None
    assert not editor._segment_preview_timer.isActive()
    editor.play_waveform_segment(2200, 3500)
    editor.close()
    assert not media.playing
    assert not editor._segment_preview_timer.isActive()


def test_lyric_preview_uses_audio_offset_and_next_distinct_line(make_lyrics_editor):
    editor = make_lyrics_editor(karaoke=False)
    media = attach_media(editor)
    editor.lines = [LyricLine(2, "First"), LyricLine(2, "Translation"), LyricLine(5, "Next")]
    editor._populate_lines(preserve_line_index=0)
    editor.lyrics_offset.setValue(500)
    editor.preview_selection_button.click()
    assert media.playing and media.position() == 2500
    assert editor._segment_preview_range == (2500, 5500)
    editor._select_line_index(2)
    editor.preview_selection_button.click()
    assert editor._segment_preview_range == (5500, 10000)


def test_preview_button_uses_segment_on_karaoke_tab(editor):
    media = attach_media(editor)
    editor.tabs.setCurrentWidget(editor.karaoke_scroll)
    editor.preview_selection_button.click()
    assert media.playing and media.position() == 2200
    assert editor._segment_preview_range == (2200, 3500)


def test_preview_speed_updates_player_and_tracks_external_changes(editor):
    media = attach_media(editor)

    def set_rate(rate):
        media.rate = rate
        editor._segment_preview_rate_changed(rate)

    media.setPlaybackRate = set_rate
    editor.player.handle_speed_change = lambda percent: set_rate(percent / 100)
    editor.preview_speed.setEnabled(True)
    editor.preview_karaoke_segment()
    editor.preview_speed.setCurrentIndex(editor.preview_speed.findData(0.5))
    assert media.rate == 0.5
    assert editor._segment_preview_timer.interval() == 2600
    set_rate(0.9)
    assert editor.preview_speed.currentData() == 0.9
    assert editor._segment_preview_timer.interval() == 1445


def test_timing_badges_follow_source_indices_in_sorted_sidebar(editor):
    editor.lines[1].end = 4
    editor.sidebar_sort_combo.setCurrentIndex(2)
    assert not editor.line_list.item(0).icon().isNull()
    assert "before its start" in editor.line_list.item(0).toolTip()
    assert editor.line_list.item(1).icon().isNull()
    issue = editor.timing_issues_list.item(0)
    assert issue.data(Qt.ItemDataRole.UserRole) == 1
    editor._go_to_timing_issue(issue)
    assert editor._current_line_index() == 1


def test_inline_issues_refresh_after_timing_edit_and_undo(editor, qapp):
    editor.lines[1].end = 6
    editor._populate_lines(preserve_line_index=1)
    editor._reset_history_baseline()
    assert editor.timing_issues_list.count() == 0
    editor.line_time.setValue(1)
    QTest.qWait(250)
    assert not editor.line_list.item(1).icon().isNull()
    assert any("before the previous line" in editor.timing_issues_list.item(i).text()
               for i in range(editor.timing_issues_list.count()))
    editor.undo()
    assert editor.timing_issues_list.count() == 0
    assert editor.line_list.item(1).icon().isNull()


@pytest.mark.parametrize("suffix", [".lrcx", ".txt"])
def test_pending_source_and_plain_text_do_not_show_stale_timing_badges(editor, suffix):
    editor.lines[1].end = 4
    editor._populate_lines()
    assert editor.timing_issues_list.count()
    editor._source_suffix = suffix
    editor._source_dirty = suffix == ".lrcx"
    editor._refresh_inline_timing_issues()
    assert editor.timing_issues_list.count() == 0
    assert all(editor.line_list.item(i).icon().isNull() for i in range(editor.line_list.count()))
    expected = "source edits pending" if suffix == ".lrcx" else "plain text"
    assert expected in editor.timing_issues_toggle.text()
