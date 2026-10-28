from copy import deepcopy
from types import SimpleNamespace

import pytest

from lyrics.loader import load_lyrics
from lyrics.models import LyricLine, LyricSegment
from ui.lrc_editor_controls import KaraokeSeparationWidget


def load_line(editor, line, others=()):
    editor.lines = [line, *others]
    editor._populate_lines(preserve_line_index=0)
    editor.document_dirty = False
    editor._reset_history_baseline()


def two_chunks(*, implicit=False):
    return LyricLine(2, "ABCD", [
        LyricSegment(2.2, "AB", end=None if implicit else 3),
        LyricSegment(3, "CD", end=4),
    ], end=4.5, has_karaoke=True)


def test_waveform_implicit_final_end_uses_seconds_not_milliseconds(editor):
    line = LyricLine(2, "AB", [LyricSegment(2.2, "AB")], has_karaoke=True)
    load_line(editor, line, [LyricLine(5, "Next")])
    editor._refresh_karaoke_waveform_editor()
    assert editor.karaoke_waveform._karaoke_segments[0][:2] == (2200, 5000)


def test_waveform_retains_explicit_trailing_pause(editor):
    line = LyricLine(2, "AB", [LyricSegment(2.2, "AB")], end=4, has_karaoke=True)
    load_line(editor, line, [LyricLine(5, "Next")])
    editor._refresh_karaoke_waveform_editor()
    assert editor.karaoke_waveform._karaoke_segments[0][1] == 4000


def test_waveform_uses_chronological_boundary_when_source_order_differs(editor):
    line = LyricLine(2, "AB", [LyricSegment(2.2, "AB")], has_karaoke=True)
    load_line(editor, line, [LyricLine(1, "Earlier"), LyricLine(5, "Later")])
    editor._refresh_karaoke_waveform_editor()
    assert editor.karaoke_waveform._karaoke_segments[0][1] == 5000


@pytest.mark.parametrize("handle,row", [("linked_end", 0), ("linked_start", 1)])
def test_linked_drag_supports_implicit_shared_boundaries(editor, handle, row):
    load_line(editor, two_chunks(implicit=True))
    editor.segment_table.setCurrentCell(row, 0 if handle == "linked_start" else 1)
    before = deepcopy(editor.lines)
    editor._waveform_karaoke_timing_changed(handle, 3200)
    assert editor.lines[0].segments[0].end == 3.2
    assert editor.lines[0].segments[1].start == 3.2
    editor._waveform_karaoke_timing_finished()
    editor.undo()
    assert editor.lines == before


def test_disconnected_link_drag_does_not_mutate_or_add_undo_history(editor):
    line = two_chunks()
    line.segments[0].end = 2.8
    load_line(editor, line)
    before = deepcopy(editor.lines)
    editor._waveform_karaoke_timing_changed("linked_end", 3200)
    assert editor.lines == before
    assert not editor._undo_stack


def test_drag_with_no_valid_interval_does_not_create_end_before_start(editor):
    line = LyricLine(2, "AB", [LyricSegment(6, "AB", end=7)], end=7, has_karaoke=True)
    load_line(editor, line, [LyricLine(5, "Next")])
    editor.segment_table.setCurrentCell(0, 1)
    before = deepcopy(editor.lines)
    editor._waveform_karaoke_timing_changed("end", 6500)
    assert editor.lines == before
    assert not editor._undo_stack
    assert "No valid End" in editor.overlap_warning.text()


def test_drag_is_one_undo_step_and_does_not_resize_unlinked_neighbor(editor):
    load_line(editor, two_chunks())
    before = deepcopy(editor.lines)
    editor._waveform_karaoke_timing_changed("start", 2300)
    editor._waveform_karaoke_timing_changed("start", 2400)
    editor._waveform_karaoke_timing_finished()
    assert len(editor._undo_stack) == 1
    assert editor.lines[0].segments[1] == before[0].segments[1]
    editor.undo()
    assert editor.lines == before


def test_nudging_last_end_preserves_trailing_pause_and_can_be_undone(editor):
    before = deepcopy(editor.lines)
    editor.segment_table.setCurrentCell(0, 1)
    editor.karaoke_nudge_step.setValue(10)
    editor.nudge_karaoke_boundary(1)
    assert editor.lines[0].segments[0].end == 3.51
    assert editor.lines[0].end == 4
    assert editor.save_button.isEnabled()
    editor.undo()
    assert editor.lines == before


def test_each_nudge_creates_its_own_undo_step(editor):
    editor.segment_table.setCurrentCell(0, 0)
    editor.nudge_karaoke_boundary(1)
    editor.nudge_karaoke_boundary(1)
    assert editor.lines[0].segments[0].start == pytest.approx(2.22)
    editor.undo()
    assert editor.lines[0].segments[0].start == pytest.approx(2.21)


def test_merging_chunks_preserves_full_interval_and_ruby(editor):
    line = two_chunks()
    line.segments[0].ruby = "first"
    line.segments[0].ruby_source = "manual"
    load_line(editor, line)
    editor.separator_widget.set_content("ABCD", set())
    editor.apply_visual_separation()
    merged = editor.lines[0].segments
    assert len(merged) == 1
    assert (merged[0].start, merged[0].end) == (2.2, 4)
    assert merged[0].ruby_spans == [(0, 2, "first")]
    assert editor.lines[0].end == 4.5 and editor.lines[0].has_karaoke


def test_splitting_last_line_uses_its_explicit_end_without_next_lyric(editor):
    line = LyricLine(2, "ABCD", [LyricSegment(2, "ABCD")], end=10, has_karaoke=True)
    load_line(editor, line)
    editor.separator_widget.set_content("ABCD", {2})
    editor.apply_visual_separation()
    assert [(segment.start, segment.end) for segment in editor.lines[0].segments] == [(2, 6), (6, 10)]


def test_multiline_separation_retains_structural_newline_and_visual_rows(editor):
    line = LyricLine(2, "AB\nCD", [
        LyricSegment(2.2, "AB", end=3), LyricSegment(3, "\n"),
        LyricSegment(3, "CD", end=4, visual_line=1),
    ], end=4.5, has_karaoke=True)
    load_line(editor, line)
    editor.separator_widget.set_content(line.text, set())
    editor.apply_visual_separation()
    assert [segment.text for segment in editor.lines[0].segments] == ["AB", "\n", "CD"]
    assert editor.lines[0].segments[-1].visual_line == 1
    assert editor.save_document()
    reopened = load_lyrics(editor.lrc_path)[0]
    assert reopened.text == "AB\nCD"
    assert reopened.segments[-1].visual_line == 1


def test_separator_does_not_split_combining_accents_or_emoji_sequences(qapp):
    separator = KaraokeSeparationWidget()
    separator.set_content("e\u0301\U0001f469\u200d\U0001f4bbX", {1, 2, 3, 4, 5})
    assert separator.boundaries() == {2, 5}
    assert separator._valid_boundaries == [2, 5]
    separator.close()


class FakeMedia:
    def __init__(self):
        self.current_position = 0
        self.playing = False
        self.pause_count = 0

    def position(self):
        return self.current_position

    def duration(self):
        return 10000

    def setPosition(self, value):
        self.current_position = value

    def play(self):
        self.playing = True

    def pause(self):
        self.playing = False
        self.pause_count += 1


def test_selected_segment_preview_seeks_plays_and_pauses_at_its_end(editor):
    media = FakeMedia()
    editor.player = SimpleNamespace(media_player=media)
    editor.preview_karaoke_segment()
    assert media.position() == 2200 and media.playing
    editor.preview_position_changed(3499)
    assert media.playing
    editor.preview_position_changed(3500)
    assert not media.playing and media.pause_count == 1
    editor.preview_position_changed(3600)
    assert media.pause_count == 1


def test_manual_seeking_cancels_segment_preview_limit(editor):
    media = FakeMedia()
    editor.player = SimpleNamespace(media_player=media)
    editor.preview_karaoke_segment()
    editor.seek_preview(4000)
    editor.preview_position_changed(4000)
    assert media.playing


def test_closing_editor_mid_preview_pauses_audio(editor):
    media = FakeMedia()
    editor.player = SimpleNamespace(media_player=media)
    editor.preview_karaoke_segment()
    editor.close()
    assert not media.playing


def test_marking_single_segment_keeps_explicit_karaoke_on_save(editor):
    media = FakeMedia()
    media.current_position = 2400
    editor.player = SimpleNamespace(media_player=media)
    editor.segment_table.setCurrentCell(0, 0)
    editor.use_playback_time()
    assert editor.lines[0].has_karaoke
    assert editor.save_document()
    assert load_lyrics(editor.lrc_path)[0].has_karaoke


def test_separate_visual_rows_do_not_create_false_overlap_warning(editor):
    line = LyricLine(2, "AB\nCD", [
        LyricSegment(2.2, "AB", end=4), LyricSegment(2, "\n"),
        LyricSegment(2.2, "CD", end=4, visual_line=1),
    ], end=4.5, has_karaoke=True)
    load_line(editor, line)
    editor._update_overlap_warning(line)
    assert editor.overlap_warning.isHidden()


def test_overlap_warning_uses_actual_table_rows_when_whitespace_is_hidden(editor):
    line = LyricLine(2, "A B", [
        LyricSegment(2.2, "A", end=3.5), LyricSegment(3, " "),
        LyricSegment(3, "B", end=4),
    ], end=4.5, has_karaoke=True)
    load_line(editor, line)
    editor._update_overlap_warning(line)
    assert "row(s): 3" in editor.overlap_warning.text()


def test_editing_generated_ruby_marks_new_reading_manual_and_persists_it(editor):
    line = two_chunks()
    line.segments[0].ruby = "generated"
    line.segments[0].ruby_source = "generated"
    load_line(editor, line)
    editor.segment_table.item(0, 3).setText("authored")
    assert editor.lines[0].segments[0].ruby_source == "manual"
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert restored.segments[0].ruby == "authored"


def test_reset_karaoke_enables_save_preserves_ruby_and_supports_undo(editor, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    line = two_chunks()
    line.segments[0].ruby = "keep"
    line.segments[0].ruby_source = "manual"
    load_line(editor, line)
    before = deepcopy(editor.lines)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: next(button for button in box.buttons() if button.text() == "Clear Karaoke"))
    editor.clear_all_karaoke()
    assert editor.save_button.isEnabled()
    assert not editor.lines[0].has_karaoke
    assert editor.lines[0].segments[0].ruby_spans == [(0, 2, "keep")]
    editor.undo()
    assert editor.lines == before
