from math import hypot
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QMenu, QStackedWidget, QWidget

from core.plugin_manager import PluginManager
from lyrics.models import LyricLine, LyricSegment
from plugins.community.rhythm_games import plugin as rhythm_games
from plugins.community.rhythm_games.plugin import (
    DEMO, Note, Passage, RhythmBoard, RhythmDialog, Round, full_song_passage,
    generate_circle_centers, lyric_passages,
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


@pytest.mark.parametrize("selection,start_ms,end_signal", [(0, 0, True), (0, 0, False), (1, 1000, False)])
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


def test_rhythm_round_judges_timing_and_breaks_combo_on_miss():
    round_ = Round((Note(1000, "one"), Note(1300, "two"), Note(1800, "three"),
                    Note(2300, "four"), Note(2700, "five")))
    assert round_.tap(945, 0).message == "OK"
    assert round_.tap(1420, 1).message == "Meh"
    assert round_.combo == 2
    early = round_.tap(1649, 2)
    assert (early.message, early.missed, early.hit) == ("Too early", (2,), False)
    assert round_.tap(1800, 2).missed == ()
    assert round_.combo == 0
    assert round_.tap(2300, 3).message == "Great"
    late = round_.tap(2851, 4)
    assert (late.message, late.missed, late.hit) == ("Too late", (4,), False)
    assert round_.combo == 0
    assert (round_.hits, round_.best_combo) == (3, 2)
    assert round_.expire(float("inf")) == ()


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("error,grade", [(50, "Great"), (51, "OK"), (100, "OK"),
                                         (101, "Meh"), (150, "Meh")])
def test_rhythm_timing_tiers_apply_on_both_sides_of_the_note(direction, error, grade):
    round_ = Round((Note(1000, "one"),))
    result = round_.tap(1000 + direction * error, 0)
    assert (result.message, result.hit, result.missed) == (grade, True, ())


def test_circle_generation_scales_jumps_with_timing_and_keeps_targets_in_bounds():
    notes = tuple(Note(time, str(time)) for time in (1000, 1120, 1360, 2100, 2500, 2830))
    field = QRectF(75, 75, 580, 245)
    centers = generate_circle_centers(notes, field, 80)

    assert centers == generate_circle_centers(notes, field, 80)
    assert all(field.contains(center) for center in centers)
    assert centers[0] == centers[1]
    jumps = [hypot((b - a).x(), (b - a).y()) for a, b in zip(centers, centers[1:])]
    assert 70 < jumps[1] < jumps[2] <= 181
    for index, note in enumerate(notes):
        for later in range(index + 1, len(notes)):
            if notes[later].time_ms - note.time_ms > 1150 or centers[index] == centers[later]:
                continue
            delta = centers[later] - centers[index]
            assert hypot(delta.x(), delta.y()) >= 79.99


def test_rapid_notes_are_previewed_before_taps_at_one_aim_point(qapp):
    notes = (Note(1000, "one"), Note(1120, "two"), Note(1360, "three"))
    board = RhythmBoard(None)
    round_ = Round(notes)
    try:
        board.set_frame(notes, 0, 119)
        assert list(board._visible_stacks()) == [[0]]
        board.set_frame(notes, 0, 120)
        assert list(board._visible_stacks()) == [[0, 1]]
        board.set_frame(notes, 0, 1000)
        center = board.target_center(0)
        assert center == board.target_center(1)
        assert list(board._visible_stacks()) == [[0, 1], [2]]
        assert board.target_at(center) == 0
        assert round_.tap(1000, board.target_at(center)).hit
        board.set_frame(notes, round_.index, 1120)
        assert list(board._visible_stacks()) == [[1], [2]]
        assert board.target_at(center) == 1
        assert round_.tap(1120, board.target_at(center)).hit
        assert round_.combo == 2
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
        outside_circle = center + QPoint(46, 0)
        baseline = board.grab().toImage()
        board.animate_hit(0)
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


def test_missed_stack_note_displays_a_fading_x_beside_the_live_circle(qapp, monkeypatch):
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
        assert effect.index == 0 and not effect.hit
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
        dialog.round = Round(DEMO.notes)
        dialog.preroll_until = float("inf")
        dialog._set_controls_running(True)
        qapp.processEvents()
        board = dialog.board
        clock = 1000
        monkeypatch.setattr(dialog, "_position", lambda: clock)
        board.set_frame(DEMO.notes, 0, clock)

        QTest.mouseClick(board, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        assert dialog.round.hits == 0
        QTest.mouseClick(board, Qt.MouseButton.LeftButton, pos=board.target_center(1).toPoint())
        assert dialog.status.text() == "Too early"
        assert sounds == []
        effect, = board._feedback
        assert effect.index == 1 and not effect.hit
        assert all(1 not in stack for stack in board._visible_stacks())
        QTest.mouseClick(board, Qt.MouseButton.LeftButton, pos=board.target_center(0).toPoint())
        assert dialog.round.hits == 1
        assert board._feedback_timer.isActive()
        assert board.target_at(board.target_center(0)) is None

        pointer = QPoint(10, 10)
        monkeypatch.setattr(rhythm_games, "QCursor", SimpleNamespace(pos=lambda: board.mapToGlobal(pointer)))
        clock = 2080
        dialog._tick()
        QTest.keyClick(board, Qt.Key.Key_Z)
        assert dialog.round.hits == 1
        pointer = board.target_center(2).toPoint()
        QTest.keyClick(board, Qt.Key.Key_X)
        assert dialog.round.hits == 2
        assert dialog.status.text() == "OK"

        clock = 2651
        pointer = board.target_center(3).toPoint()
        QTest.keyClick(board, Qt.Key.Key_Z)
        assert dialog.status.text() == "Too late"
        assert dialog.round.hits == 2 and dialog.round.combo == 0
        assert board._feedback[-1].index == 3 and not board._feedback[-1].hit
        assert sounds == ["hit", "hit"]
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
