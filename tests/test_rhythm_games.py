from math import atan2, cos, hypot, sin, tau
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QMenu, QStackedWidget, QWidget

from core.plugin_manager import PluginManager
from core.rhythm_analysis import AudioRhythm, analyze_audio_rhythm
from lyrics.models import LyricLine, LyricSegment
from plugins.community.rhythm_games import plugin as rhythm_games
from plugins.community.rhythm_games.plugin import (
    Judgement, Note, Passage, RhythmBoard, RhythmDialog, Round, apply_audio_rhythm,
    circle_patterns, full_song_passage, generate_circle_centers, lyric_passages,
)


def test_chart_uses_authored_timing_without_adding_translation_targets():
    lines = [
        LyricLine(0, "Plain LRC"),
        LyricLine(1, "歌を歌う\nSing a song", [
            LyricSegment(1, "歌", ruby="うた"),
            LyricSegment(1, "を"),
            LyricSegment(1.2, "Sing a song", visual_line=1),
            LyricSegment(1.5, "歌う", ruby_spans=[(0, 1, "うた")]),
            LyricSegment(2, "♪"),
        ], has_karaoke=True, translation_lines=[1]),
    ]

    passage, = lyric_passages(lines, 250, 4000)
    assert passage.notes == (Note(1250, "歌 を", "うた"), Note(1750, "歌う", "うた"))
    assert passage.label == "0:01  歌を歌う"
    assert list(lyric_passages(lines, 250, 1250)) == []


def test_full_song_chart_sorts_all_passages_and_combines_simultaneous_lines():
    first = Note(1300, "歌", "うた")
    passages = (
        Passage("Later line", (Note(2300, "later"),)),
        Passage("First line", (first, Note(1500, "word"))),
        Passage("Overlapping line", (first, Note(1500, "voice"), Note(2700, "last"))),
    )
    chart = full_song_passage(passages)
    assert chart.full_song and chart.label == "Full song"
    assert chart.notes == (first, Note(1500, "word / voice"), Note(2300, "later"), Note(2700, "last"))


def test_sustained_lyrics_make_room_for_overlapping_lines_and_audio_end():
    lines = [
        LyricLine(1, "Sustain now", [
            LyricSegment(1, "Sustain", end=2.2), LyricSegment(2.2, "now", end=4),
        ], has_karaoke=True),
        LyricLine(1.4, "Quick", [LyricSegment(1.4, "Quick", instant=True, end=2.3)], has_karaoke=True),
    ]
    first, second = lyric_passages(lines, 100, 3000)
    assert first.notes == (Note(1100, "Sustain", end_ms=2150), Note(2300, "now", end_ms=3000))
    assert second.notes == (Note(1500, "Quick"),)
    assert full_song_passage((first, second)).notes == (Note(1100, "Sustain"), *second.notes, first.notes[-1])


def test_audio_analysis_reads_a_local_click_track(tmp_path):
    track = tmp_path / "clicks.wav"
    track.write_bytes(rhythm_games.demo_wave())
    cancelled = Event()
    rhythm = analyze_audio_rhythm(track, cancelled)
    assert rhythm.accents_ms
    assert all(0 <= time < rhythm_games.DEMO.notes[-1].finish_ms + 750 for time in rhythm.accents_ms)

    cancelled.set()
    assert analyze_audio_rhythm(track, cancelled) == AudioRhythm()


def test_beat_accent_can_open_a_new_pattern_and_widen_its_entry_jump():
    notes = tuple(Note(time, str(time)) for time in (1000, 1300, 1600, 1900, 2200, 2500))
    passage = Passage("Lyrics", notes)
    accented = apply_audio_rhythm(passage, AudioRhythm((1885,), (1910,)))
    assert tuple(note.time_ms for note in accented.notes) == tuple(note.time_ms for note in notes)
    assert list(map(len, circle_patterns(notes))) == [6]
    assert list(map(len, circle_patterns(accented.notes))) == [3, 3]

    field = QRectF(44, 44, 424, 296)
    centers = generate_circle_centers(accented.notes, field, 36.5)
    jump = centers[3] - centers[2]
    assert hypot(jump.x(), jump.y()) == pytest.approx(90 * 1.16)


def test_repeated_beat_patterns_can_keep_their_direction():
    notes = tuple(Note(time, str(time)) for time in (1000, 1300, 1600, 2550, 2850, 3150))
    passage = apply_audio_rhythm(Passage("Lyrics", notes), AudioRhythm((1000, 2550)))
    centers = generate_circle_centers(passage.notes, QRectF(44, 44, 424, 296), 36.5)
    turns = []
    for index in (0, 3):
        a, b = centers[index + 1] - centers[index], centers[index + 2] - centers[index + 1]
        turns.append(a.x() * b.y() - a.y() * b.x())
    assert abs(turns[0]) > 0
    assert turns[0] == pytest.approx(turns[1])


@pytest.mark.parametrize("rhythm", [AudioRhythm((2000,), (2000,)), None])
def test_song_round_waits_for_rhythm_analysis_and_uses_fallback_on_error(qapp, monkeypatch, rhythm):
    host = QMainWindow()
    host.media_player = SimpleNamespace(playbackState=lambda: QMediaPlayer.PlaybackState.StoppedState)
    host.volume_slider = SimpleNamespace(value=lambda: 25)
    api = SimpleNamespace(get_current_track=lambda: "song.mp3", get_position=lambda: 2000,
                          get_setting=lambda _key, default: default, pause=Mock())
    passage = Passage("Lyrics", (Note(2000, "one"), Note(2400, "two")))
    dialog = RhythmDialog(host, api, (passage,))
    host.audio_output = dialog.output
    load = Mock()
    monkeypatch.setattr(dialog.player, "setSource", load)
    job = dialog._rhythm_job = Mock()
    try:
        dialog._start_round()
        assert dialog.round is None and not dialog.start.isEnabled()
        assert not api.pause.called and not load.called

        dialog._rhythm_ready(rhythm)
        job.close.assert_called_once()
        api.pause.assert_called_once()
        assert load.call_args.args[0].toLocalFile() == "song.mp3"
        assert dialog.round.notes[0].accented == (rhythm is not None)
        assert tuple(note.time_ms for note in dialog.round.notes) == (2000, 2400)
    finally:
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("selection,start_ms,end_signal", [(0, 0, True), (0, 0, False), (1, 800, False)])
def test_full_song_waits_for_audio_end_while_passage_stops_after_last_note(
        qapp, selection, start_ms, end_signal):
    host = QMainWindow()
    api = SimpleNamespace(get_current_track=lambda: "song.mp3", get_position=lambda: 2000,
                          get_setting=lambda _key, default: default)
    passage = Passage("0:02  note", (Note(2000, "note"),))
    dialog = RhythmDialog(host, api, (passage,))
    native_player = dialog.player
    seek_positions = []
    position = 2500
    dialog.player = SimpleNamespace(
        setPosition=seek_positions.append, play=lambda: None, pause=lambda: None,
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.BufferedMedia,
        position=lambda: position, duration=lambda: 4000,
    )
    try:
        assert dialog.passage.currentText() == passage.label
        dialog.passage.setCurrentIndex(selection)
        dialog.round = Round(dialog._selected_passage().notes)
        dialog._play_round()
        assert seek_positions == [start_ms]
        dialog._tick()
        if selection == 0:
            assert dialog.round is not None
            assert dialog.progress.value() == 2500
            if end_signal:
                dialog._media_status(QMediaPlayer.MediaStatus.EndOfMedia)
            else:
                position = 4000
                dialog._tick()
            assert dialog.progress.value() == 4000
        assert dialog.round is None
        assert dialog.start.text() == "Retry"
    finally:
        dialog.player = native_player
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


def test_rhythm_round_locks_future_notes_and_scores_each_note_once():
    notes = tuple(Note(time, str(time)) for time in (1000, 1300, 1800, 2300, 2700))
    round_ = Round(notes)
    assert round_.tap(550, 0) == (None, False)
    assert round_.tap(1190, 1) == (None, False)
    assert round_.index == 0
    assert round_.tap(945, 0) == (Judgement(0, 100), True)
    assert round_.tap(1420, 1) == (Judgement(1, 50), True)
    assert round_.tap(1649, 2) == (Judgement(2, 0), False)
    assert round_.tap(1800, 2) == (None, False)
    assert round_.combo == 0
    assert round_.tap(2300, 3) == (Judgement(3, 300), True)
    assert round_.advance(2850) == [Judgement(4, 0)]
    assert round_.combo == 0 and round_.best_combo == 2
    assert round_.counts == {300: 1, 100: 1, 50: 1, 0: 2}
    assert round_.score == 450 and round_.accuracy == 0.3 and round_.grade == "D"
    assert round_.advance(float("inf")) == []


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("error,value", [(49, 300), (50, 100), (99, 100), (100, 50), (149, 50), (150, 0)])
def test_rhythm_timing_tiers_apply_on_both_sides_of_the_note(direction, error, value):
    round_ = Round((Note(1000, "one"),))
    time = 1000 + direction * error
    expired = round_.advance(time)
    if expired:
        assert expired == [Judgement(0, value)]
    else:
        assert round_.tap(time, 0) == (Judgement(0, value), bool(value))


def test_circle_generation_scales_jumps_with_timing_and_keeps_targets_in_bounds():
    notes = tuple(Note(time, str(time)) for time in (1000, 1120, 1360, 2100, 2500, 2830))
    field = QRectF(75, 75, 580, 245)
    radius = 35
    centers = generate_circle_centers(notes, field, radius)

    assert centers == generate_circle_centers(notes, field, radius)
    assert all(field.contains(center) for center in centers)
    assert centers[1] - centers[0] == QPointF(3.5, 3.5)
    jumps = [hypot((b - a).x(), (b - a).y()) for a, b in zip(centers, centers[1:])]
    assert 70 < jumps[1] < jumps[2] <= 181
    for index, note in enumerate(notes):
        for later in range(index + 1, len(notes)):
            gap = notes[later].time_ms - note.time_ms
            if not 200 <= gap <= 1150:
                continue
            delta = centers[later] - centers[index]
            assert hypot(delta.x(), delta.y()) >= 79.99


def test_regular_fast_notes_follow_short_arcs_without_leaving_the_field():
    notes = tuple(Note(time, str(time)) for time in range(1000, 2125, 125))
    field = QRectF(44, 44, 424, 296)
    radius = 36.5
    centers = generate_circle_centers(notes, field, radius)
    assert len(centers) == len(notes)
    assert all(field.contains(center) for center in centers)
    assert centers == generate_circle_centers(notes, field, radius)

    for start, stop in ((0, 5), (5, 9)):
        steps = [b - a for a, b in zip(centers[start:stop - 1], centers[start + 1:stop])]
        assert all(hypot(step.x(), step.y()) == pytest.approx(33.75) for step in steps)
        turns = [a.x() * b.y() - a.y() * b.x() for a, b in zip(steps, steps[1:])]
        assert abs(turns[0]) > 0
        assert turns == pytest.approx([turns[0]] * len(turns))


def test_four_regular_jumps_form_a_square():
    notes = tuple(Note(time, str(time)) for time in (1000, 1300, 1600, 1900))
    field = QRectF(44, 44, 424, 296)
    a, b, c, d = generate_circle_centers(notes, field, 36.5)
    sides = (b - a, c - b, d - c, a - d)
    assert all(field.contains(point) for point in (a, b, c, d))
    assert [hypot(side.x(), side.y()) for side in sides] == pytest.approx([90] * 4)
    assert QPointF.dotProduct(sides[0], sides[1]) == pytest.approx(0)


def test_very_fast_run_alternates_turn_direction():
    notes = tuple(Note(time, str(time)) for time in (1000, 1095, 1200, 1298, 1405, 1500))
    field = QRectF(44, 44, 424, 296)
    centers = generate_circle_centers(notes, field, 36.5)
    steps = [b - a for a, b in zip(centers, centers[1:])]
    turns = [atan2(a.x() * b.y() - a.y() * b.x(), QPointF.dotProduct(a, b)) for a, b in zip(steps, steps[1:])]
    assert turns == pytest.approx([turns[0], -turns[0], turns[0], -turns[0]])
    assert abs(turns[0]) == pytest.approx(tau / 4)


def test_long_fast_run_can_spiral_before_a_shorter_arc():
    notes = tuple(Note(time, str(time)) for time in range(1000, 3000, 125))
    field = QRectF(44, 44, 424, 296)
    centers = generate_circle_centers(notes, field, 36.5)
    assert len(centers) == len(notes)
    assert all(field.contains(point) for point in centers)

    steps = [b - a for a, b in zip(centers[:11], centers[1:12])]
    turns = [abs(atan2(a.x() * b.y() - a.y() * b.x(), QPointF.dotProduct(a, b)))
             for a, b in zip(steps, steps[1:])]
    assert all(a > b > 0 for a, b in zip(turns, turns[1:]))
    assert all(32 < hypot(step.x(), step.y()) < 35.5 for step in steps)


def test_regular_jumps_form_a_triangle_and_a_back_and_forth_pattern():
    notes = tuple(Note(time, str(time)) for time in (1000, 1275, 1550, 2250, 2550, 2850, 3150, 3450))
    field = QRectF(44, 44, 424, 296)
    centers = generate_circle_centers(notes, field, 36.5)
    assert all(field.contains(center) for center in centers)

    first, second, third = centers[:3]
    sides = [second - first, third - second, first - third]
    assert [hypot(side.x(), side.y()) for side in sides] == pytest.approx([83] * 3)

    jumps = [b - a for a, b in zip(centers[3:], centers[4:])]
    assert [hypot(step.x(), step.y()) for step in jumps] == pytest.approx([90] * 4)
    for a, b in zip(jumps, jumps[1:]):
        assert a.x() * b.x() + a.y() * b.y() < 0
        assert hypot((a + b).x(), (a + b).y()) == pytest.approx(36.5 * 1.1)


def test_lookahead_keeps_the_intended_jump_into_the_next_pattern():
    notes = tuple(Note(time, str(time)) for time in (1000, 1350, 1700, 1825, 2075, 2325, 3025, 3375, 3650))
    field = QRectF(44, 44, 424, 296)
    centers = generate_circle_centers(notes, field, 36.5)
    delta = centers[6] - centers[5]
    assert hypot(delta.x(), delta.y()) == pytest.approx(rhythm_games.MAX_JUMP_PX)


@pytest.mark.parametrize("spans", [1, 2])
def test_circle_after_slider_is_placed_from_the_visual_tail(qapp, spans):
    board = RhythmBoard(None)
    slider = Note(1000, "slide", end_ms=1500, spans=spans)
    try:
        board.set_frame((slider, Note(1660, "tap")), 0, 1000)
        head = board.target_center(0)
        tail = board.slider_ball(0, slider.end_ms)
        after = board.target_center(1)
        assert hypot((after - tail).x(), (after - tail).y()) == pytest.approx(35.2 * board._scale)
        if spans == 1:
            assert hypot((after - head).x(), (after - head).y()) > 90 * board._scale
        else:
            assert head == tail
    finally:
        board.deleteLater()


def test_stacked_notes_keep_their_preview_positions_and_hitboxes(qapp):
    notes = (Note(1000, "one"), Note(1120, "two"), Note(1240, "three"), Note(1500, "four"))
    board = RhythmBoard(None)
    round_ = Round(notes)
    try:
        board.set_frame(notes, 0, -81)
        assert list(board._visible_indices()) == [0]
        board.set_frame(notes, 0, -79)
        assert list(board._visible_indices()) == [0, 1]
        second, third = (board.target_center(index) for index in (1, 2))
        board.set_frame(notes, 0, 1000)
        first = board.target_center(0)
        assert first.x() < second.x() < third.x()
        assert first.y() < second.y() < third.y()
        assert list(board._visible_indices()) == [0, 1, 2, 3]
        edge = second + QPointF(1, 1) * ((board.target_radius - 1) / 2 ** 0.5)
        assert board.target_at(edge) == 1
        assert round_.tap(1000, board.target_at(edge)) == (None, False)
        assert board.target_at(second) == 0
        assert round_.tap(1000, board.target_at(second)) == (Judgement(0, 300), True)
        board.set_frame(notes, round_.index, 1120)
        assert list(board._visible_indices()) == [1, 2, 3]
        assert board.target_center(1) == second and board.target_center(2) == third
        assert board.target_at(second) == 1
        assert round_.tap(1120, board.target_at(second)) == (Judgement(1, 300), True)
        assert round_.combo == 2
    finally:
        board.deleteLater()


def test_dense_run_limits_previews_and_leaves_each_note_hittable(qapp):
    notes = tuple(Note(time, str(time)) for time in range(1000, 2200, 100))
    board = RhythmBoard(None)
    round_ = Round(notes)
    try:
        board.set_frame(notes, 0, 975, round_)
        assert list(board._visible_indices()) == [0, 1, 2, 3, 4]
        painter = Mock()
        for index in board._visible_indices():
            lead = notes[index].time_ms - board._preview_start(index)
            board._draw_approach_ring(painter, index, board.target_center(index), board.palette().text().color(), lead)
        assert painter.drawEllipse.call_count == 5
        active, upcoming, *later = [call.args[0] for call in painter.setOpacity.call_args_list]
        assert max(later) < upcoming < active

        for index, note in enumerate(notes):
            board.set_frame(notes, round_.index, note.time_ms, round_)
            assert list(board._visible_indices()) == list(range(index, min(index + 5, len(notes))))
            target = board.target_at(board.target_center(index))
            assert round_.tap(note.time_ms, target) == (Judgement(index, 300), True)
        assert round_.accuracy == 1
    finally:
        board.deleteLater()


def test_dense_approach_rings_shrink_on_the_normal_clock(qapp):
    notes = tuple(Note(time, str(time)) for time in range(1000, 2200, 100))
    board = RhythmBoard(None)
    painter = Mock()
    color = board.palette().text().color()
    try:
        for index, position in ((0, 250), (5, 1130), (5, 1500)):
            board.set_frame(notes, max(0, index - 1), position)
            assert index in board._visible_indices()
            lead = notes[index].time_ms - board._preview_start(index)
            board._draw_approach_ring(painter, index, board.target_center(index), color, lead)
            assert painter.drawEllipse.call_count == 1
            _, radius, _ = painter.drawEllipse.call_args.args
            remaining = notes[index].time_ms - position
            assert radius == pytest.approx(board.target_radius * (1 + 3 * remaining / rhythm_games.LEAD_IN_MS))
            painter.reset_mock()
    finally:
        board.deleteLater()


def test_hit_animation_grows_and_fades_behind_the_next_stacked_note(qapp, monkeypatch):
    clock = 20.0
    monkeypatch.setattr(rhythm_games, "monotonic", lambda: clock)
    board = RhythmBoard(None)
    notes = (Note(1000, "one"), Note(1120, "two"))
    try:
        board.set_frame(notes, 1, 1000)
        center = board.target_center(1).toPoint()
        outside_circle = center + QPoint(round(board.target_radius * 1.3), 0)
        baseline = board.grab().toImage()
        board.animate(Judgement(0, 300))
        initial = board.grab().toImage()
        assert initial.pixelColor(outside_circle) == baseline.pixelColor(outside_circle)

        clock += rhythm_games.HIT_ANIMATION_SECONDS / 2
        expanded = board.grab().toImage()
        assert expanded.pixelColor(outside_circle) != baseline.pixelColor(outside_circle)
        assert expanded.pixelColor(center) == baseline.pixelColor(center)
        assert board.target_at(board.target_center(1)) == 1

        clock += rhythm_games.HIT_ANIMATION_SECONDS
        board._advance_feedback()
        assert board.grab().toImage() == baseline
        assert not board._feedback_timer.isActive()
    finally:
        board.clear_feedback()
        board.deleteLater()


def test_missed_stack_note_displays_fading_feedback_beside_the_live_circle(qapp, monkeypatch):
    host = QMainWindow()
    api = SimpleNamespace(get_current_track=lambda: None, get_setting=lambda _key, default: default)
    dialog = RhythmDialog(host, api, ())
    notes = (Note(1000, "one"), Note(1120, "two"))
    clock = 20.0
    monkeypatch.setattr(rhythm_games, "monotonic", lambda: clock)
    monkeypatch.setattr(dialog, "_position", lambda: 1151)
    try:
        dialog.open()
        qapp.processEvents()
        dialog.round = Round(notes)
        dialog.preroll_until = float("inf")
        board = dialog.board
        board.set_frame(notes, 1, 1151)
        baseline = board.grab().toImage()
        dialog._tick()

        assert dialog.status.text() == "Miss"
        effect, = board._feedback
        assert effect.index == 0 and effect.value == 0
        center = board.target_center(1)
        marker = (center + effect.offset).toPoint()
        assert board.target_at(center) == 1
        assert board.target_at(center + effect.offset) is None
        start_image = board.grab().toImage()
        assert start_image.pixelColor(marker) != baseline.pixelColor(marker)
        assert start_image.pixelColor(center.toPoint()) == baseline.pixelColor(center.toPoint())

        clock += rhythm_games.MISS_ANIMATION_SECONDS / 2
        assert board.grab().toImage().pixelColor(marker) != start_image.pixelColor(marker)
        clock += rhythm_games.MISS_ANIMATION_SECONDS
        board._advance_feedback()
        assert board.grab().toImage() == baseline
        assert not board._feedback_timer.isActive()
    finally:
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


def test_100_feedback_stays_inside_the_field_for_a_stack_in_the_corner(qapp, monkeypatch):
    def corner_stack(notes, field, radius):
        step = QPointF(radius * 0.1, radius * 0.1)
        return [field.bottomRight() - step * index for index in reversed(range(len(notes)))]

    monkeypatch.setattr(rhythm_games, "generate_circle_centers", corner_stack)
    clock = 20.0
    monkeypatch.setattr(rhythm_games, "monotonic", lambda: clock)
    board = RhythmBoard(None)
    notes = (Note(1000, "one"), Note(1120, "two"))
    try:
        round_ = Round(notes)
        judgement, _ = round_.tap(1080, 0)
        assert judgement.value == 100
        board.set_frame(notes, round_.index, 1080, round_)
        center = board.target_center(1)
        baseline = board.grab().toImage()
        board.animate(judgement)

        effect, = board._feedback
        marker = board.target_center(0) + effect.offset
        assert marker.x() < center.x() and marker.y() < center.y()
        assert board._field.contains(QRectF(marker.x() - 22, marker.y() - 12, 44, 24))
        initial = board.grab().toImage()
        assert initial.pixelColor(marker.toPoint()) != baseline.pixelColor(marker.toPoint())
        assert initial.pixelColor(center.toPoint()) == baseline.pixelColor(center.toPoint())

        clock += rhythm_games.HIT_ANIMATION_SECONDS / 2
        assert board.grab().toImage().pixelColor(marker.toPoint()) != initial.pixelColor(marker.toPoint())
    finally:
        board.clear_feedback()
        board.deleteLater()


def test_circles_require_aim_for_both_clicks_and_keys(qapp, monkeypatch):
    host = QMainWindow()
    api = SimpleNamespace(get_current_track=lambda: None, get_setting=lambda _key, default: default)
    dialog = RhythmDialog(host, api, ())
    sounds = []
    assert Path(dialog.hit_sound.source().toLocalFile()) == rhythm_games.HIT_SOUND_PATH
    monkeypatch.setattr(dialog.hit_sound, "isLoaded", lambda: True)
    monkeypatch.setattr(dialog.hit_sound, "play", lambda: sounds.append("hit"))
    try:
        dialog.open()
        dialog.activateWindow()
        notes = tuple(Note(time, str(time)) for time in (1000, 1500, 2000, 2500))
        dialog.round = Round(notes)
        dialog.preroll_until = float("inf")
        dialog._set_controls_running(True)
        qapp.processEvents()
        board = dialog.board
        clock = 1000
        monkeypatch.setattr(dialog, "_position", lambda: clock)
        board.set_frame(notes, 0, clock)

        QTest.mouseClick(board, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        assert dialog.round.index == 0
        QTest.mouseClick(board, Qt.MouseButton.LeftButton, pos=board.target_center(1).toPoint())
        assert sounds == []
        assert board._feedback == [] and dialog.round.index == 0
        QTest.mouseClick(board, Qt.MouseButton.LeftButton, pos=board.target_center(0).toPoint())
        assert dialog.round.counts[300] == 1
        assert board._feedback_timer.isActive()
        assert board.target_at(board.target_center(0)) is None

        clock = 1500
        QTest.mouseClick(board, Qt.MouseButton.RightButton, pos=board.target_center(1).toPoint())
        assert dialog.round.counts[300] == 2
        pointer = QPoint(10, 10)
        monkeypatch.setattr(rhythm_games, "QCursor", SimpleNamespace(pos=lambda: board.mapToGlobal(pointer)))
        clock = 2080
        dialog._tick()
        QTest.keyClick(board, Qt.Key.Key_Z)
        assert dialog.round.index == 2
        pointer = board.target_center(2).toPoint()
        QTest.keyClick(board, Qt.Key.Key_X)
        assert dialog.round.counts[100] == 1
        assert dialog.status.text() == "100"

        clock = 2651
        pointer = board.target_center(3).toPoint()
        QTest.keyClick(board, Qt.Key.Key_Z)
        assert dialog.status.text() == "Miss"
        assert dialog.round.counts[0] == 1 and dialog.round.combo == 0
        assert board._feedback[-1].index == 3 and board._feedback[-1].value == 0
        assert sounds == ["hit", "hit", "hit"]
    finally:
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("miss_repeat", [False, True])
def test_reversing_slider_tracks_a_held_key_and_recovers_after_release(qapp, monkeypatch, miss_repeat):
    host = QMainWindow()
    api = SimpleNamespace(get_current_track=lambda: None, get_setting=lambda _key, default: default)
    dialog = RhythmDialog(host, api, ())
    notes = (Note(1000, "slide", end_ms=2000, spans=2),)
    board = dialog.board
    clock = 1000
    monkeypatch.setattr(dialog, "_position", lambda: clock)
    monkeypatch.setattr(rhythm_games, "QCursor", SimpleNamespace(pos=lambda: board.mapToGlobal(pointer)))
    try:
        dialog.open()
        dialog.round = Round(notes)
        dialog.preroll_until = float("inf")
        dialog._set_controls_running(True)
        qapp.processEvents()
        board.set_frame(notes, 0, clock, dialog.round)

        pointer = board.target_center(0).toPoint()
        QTest.keyPress(board, Qt.Key.Key_Z)
        assert dialog.round.combo == 1 and dialog.round.index == 0
        assert board.slider_ball(0, 1500) != board.target_center(0)
        assert board.slider_ball(0, 2000) == board.target_center(0)
        for clock, _points in notes[0].slider_checkpoints():
            pointer = board.slider_ball(0, clock).toPoint()
            if miss_repeat and clock == 1500:
                QTest.keyRelease(board, Qt.Key.Key_Z)
                assert dialog.round.combo == 0
            elif miss_repeat and clock == 1750:
                QTest.keyPress(board, Qt.Key.Key_X)
            else:
                QTest.mouseMove(board, pointer)
        assert dialog.round.index == 1
        assert dialog.round.counts[100 if miss_repeat else 300] == 1
        assert dialog.round.best_combo == (2 if miss_repeat else 5)
    finally:
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


def test_spinner_requires_held_rotation_and_awards_a_clear_and_bonus(qapp, monkeypatch):
    host = QMainWindow()
    api = SimpleNamespace(get_current_track=lambda: None, get_setting=lambda _key, default: default)
    dialog = RhythmDialog(host, api, ())
    notes = (Note(1000, "spin", end_ms=2000, spinner=True),)
    board = dialog.board
    clock = 1000
    monkeypatch.setattr(dialog, "_position", lambda: clock)
    try:
        dialog.open()
        dialog.round = Round(notes)
        dialog.preroll_until = float("inf")
        qapp.processEvents()
        board.set_frame(notes, 0, clock, dialog.round)
        center = board.target_center(0)

        def orbit(step):
            angle = step * tau / 8
            return (center + QPointF(70 * cos(angle), 70 * sin(angle))).toPoint()

        for step in range(8):
            QTest.mouseMove(board, orbit(step))
        assert dialog.round.spinner_progress == 0
        QTest.mousePress(board, Qt.MouseButton.LeftButton, pos=orbit(0))
        for step in (1, 0) * 8:
            QTest.mouseMove(board, orbit(step))
        assert dialog.round.spinner_progress == pytest.approx(0)
        for step in range(1, 33):
            clock = 1000 + step * 28
            QTest.mouseMove(board, orbit(-step))
        assert dialog.round.spinner_progress == 1
        QTest.mouseRelease(board, Qt.MouseButton.LeftButton, pos=orbit(0))
        clock = 2000
        dialog._tick()
        assert dialog.round.counts[300] == 1
        assert dialog.round.score >= 1700
        assert dialog.round.grade == "SS"
    finally:
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


def test_audio_end_judges_the_last_slider_tail(qapp, monkeypatch):
    host = QMainWindow()
    api = SimpleNamespace(get_current_track=lambda: "song.mp3", get_position=lambda: 1000,
                          get_setting=lambda _key, default: default)
    notes = (Note(1000, "hold", end_ms=1450),)
    dialog = RhythmDialog(host, api, (Passage("Passage", notes),))
    round_ = Round(notes)
    dialog.round = round_
    dialog.offset_ms = 0
    board = dialog.board
    board.set_frame(notes, 0, 1000, round_)
    try:
        assert round_.tap(1000, 0) == (None, True)
        monkeypatch.setattr(dialog.player, "duration", lambda: 1450)
        monkeypatch.setattr(RhythmBoard, "held", property(lambda _self: True))
        board.pointer = board.slider_ball(0, 1450)
        dialog._media_status(QMediaPlayer.MediaStatus.EndOfMedia)
        assert round_.counts[300] == 1
        assert dialog.round is None
        assert "Grade SS" in dialog.score.text()
    finally:
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


def test_menu_and_hidden_lyrics_shortcut_open_and_clean_up_plugin(qapp):
    window = QMainWindow()
    window.plugins_menu = QMenu("Plugins", window)
    window.workspace_stack = QStackedWidget()
    window.lyrics_page = QWidget()
    window.workspace_stack.addWidget(QWidget())
    window.workspace_stack.addWidget(window.lyrics_page)
    window.setCentralWidget(window.workspace_stack)
    window.current_audio_path = None
    window.media_player = SimpleNamespace()
    plugin_dir = Path(__file__).resolve().parents[1] / "plugins"
    manager = PluginManager()
    metadata = next(item for item in manager.discover_plugins(plugin_dir) if item["plugin_id"] == "rhythm_games")
    manager.load_from_file("rhythm_games", metadata["plugin_dir"] / "plugin.py", metadata)
    manager.set_host_window(window)
    manager.emit("app_ready", window=window)
    instance = manager.plugins["rhythm_games"].instance
    try:
        action = window.plugins_menu.actions()[0]
        action.trigger()
        assert action.text().startswith("Rhythm Games")
        assert instance.dialog.passage.currentText() == "Demo"
        instance.dialog.reject()
        assert instance.dialog is None

        instance.secret.activated.emit()
        assert instance.dialog is None
        window.workspace_stack.setCurrentWidget(window.lyrics_page)
        window.show()
        window.activateWindow()
        qapp.processEvents()
        QTest.keyClick(window, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        assert instance.dialog is not None
        manager.unload_all()
        assert instance.dialog is None
    finally:
        manager.unload_all()
        window.close()
        window.deleteLater()
        qapp.processEvents()
