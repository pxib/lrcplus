from array import array
from dataclasses import replace
from math import atan2, cos, hypot, sin, tau
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QMenu, QStackedWidget, QWidget

from core.plugin_manager import PluginManager
from core.rhythm_analysis import AudioRhythm, analyze_audio_rhythm, beat_grid
from core.rhythm_melody import MelodyNote
from lyrics.models import LyricLine, LyricSegment
from plugins.community.rhythm_games import plugin as rhythm_games
from plugins.community.rhythm_games.plugin import (
    Judgement, Note, Passage, RhythmBoard, RhythmDialog, Round, apply_audio_rhythm, audio_beat_passage,
    circle_patterns, full_song_passage, generate_circle_centers, lyric_passages,
)


def test_chart_uses_authored_timing_without_adding_translation_targets():
    lines = [
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


def test_line_timed_lyrics_use_beats_between_boundaries_and_keep_whole_captions():
    lines = (
        LyricLine(4, "Next line", end=4.8),
        LyricLine(1, "Whole line\nTranslation", translation_lines=[1]),
        LyricLine(2.8, ""),
        LyricLine(5.5, "Last line"),
    )
    second, first, last = lyric_passages(lines, 100, 5700)
    assert (first.line_end_ms, second.line_end_ms, last.line_end_ms) == (2900, 4900, 5700)
    rhythm = AudioRhythm((1250, 1300, 1420, 1510, 2600, 2750, 5690), (4350,))
    first, second, last = (apply_audio_rhythm(passage, rhythm) for passage in (first, second, last))

    assert [note.time_ms for note in first.notes] == [1100, 1300, 1510, 2600]
    assert {note.text for note in first.notes} == {"Whole line"}
    assert [note.time_ms for note in second.notes] == [4100, 4350]
    assert [note.time_ms for note in last.notes] == [5600]


def test_generated_taps_leave_room_for_authored_sliders_in_mixed_full_song():
    slider = Note(1500, "Authored", end_ms=2300)
    karaoke = Passage("Karaoke", (slider, Note(2800, "Also authored")))
    line = Passage("Line", tuple(Note(time, "Plain line") for time in (1000, 1450, 1800, 2450, 2600)),
                   line_end_ms=2800)
    full = full_song_passage((line, karaoke))
    assert full.notes == (Note(1000, "Plain line"), slider, Note(2600, "Plain line"), karaoke.notes[-1])


def test_audio_only_chart_uses_onsets_when_beats_are_unavailable():
    chart = audio_beat_passage(AudioRhythm(accents_ms=(1600,), onsets_ms=(1000, 1100, 1600, 2300)), 2200)
    assert chart.full_song and chart.label == "Audio beats"
    assert [(note.time_ms, note.text) for note in chart.notes] == [(1000, ""), (1600, "")]
    assert audio_beat_passage(AudioRhythm(), 2200) is None


def test_audio_chart_snaps_supported_quarter_beats_and_triplets():
    rhythm = AudioRhythm(
        beats_ms=(1000, 1800, 2600, 3400),
        onsets_ms=(1208, 1390, 1610, 2058, 2334, 2810, 3210),
    )
    chart = audio_beat_passage(rhythm, 3800)
    assert [note.time_ms for note in chart.notes] == [1000, 1200, 1400, 1600, 1800, 2067, 2333, 2600, 3400]


def test_melody_cues_snap_to_the_beat_grid_and_to_slider_tails():
    rhythm = AudioRhythm(
        beats_ms=(1000, 1500, 2000, 2500, 3000),
        onsets_ms=(1082,),
        melody_notes=(MelodyNote(1124, 1340, 64), MelodyNote(1504, 2020, 66), MelodyNote(2251, 2450, 69)),
    )
    chart = audio_beat_passage(rhythm, 3400)
    assert [(note.time_ms, note.finish_ms) for note in chart.notes] == [
        (1000, 1000), (1125, 1125), (1500, 2000), (2250, 2250), (2500, 2500), (3000, 3000),
    ]


@pytest.mark.parametrize(("period", "pitch_offsets", "fill"), [
    (500, (125, 250, 375), 250),
    (320, (160,), None),
    (320, (80, 160, 240), 80),
])
def test_melody_only_fills_leave_a_longer_gap_in_each_beat(period, pitch_offsets, fill):
    beats = tuple(1000 + beat * period for beat in range(9))
    melody = tuple(MelodyNote(start + offset, start + offset + 120, 63 + index)
                   for start in beats[:-1] for index, offset in enumerate(pitch_offsets))
    rhythm = AudioRhythm(beats_ms=beats, onsets_ms=beats, melody_notes=melody)
    chart = audio_beat_passage(rhythm, beats[-1] + 100)
    extra = () if fill is None else tuple(start + fill for start in beats[:-1])
    assert [note.time_ms for note in chart.notes] == sorted((*beats, *extra))


def test_melody_leaves_attack_supported_triplets_in_place():
    rhythm = AudioRhythm(
        beats_ms=(1000, 1500, 2000, 2500),
        onsets_ms=(1167, 1333),
        melody_notes=(MelodyNote(1125, 1245, 63), MelodyNote(1250, 1370, 66), MelodyNote(1375, 1495, 69)),
    )
    chart = audio_beat_passage(rhythm, 3000)
    assert [note.time_ms for note in chart.notes] == [1000, 1167, 1333, 1500, 2000, 2500]


def test_off_grid_melody_alone_keeps_fast_runs_from_forming():
    notes = tuple(MelodyNote(time, time + 150, 60 + index) for index, time in enumerate(range(1000, 2300, 185)))
    chart = audio_beat_passage(AudioRhythm(melody_notes=notes), 3000)
    assert [note.time_ms for note in chart.notes] == [note.start_ms for note in notes[::2]]


def test_audio_chart_keeps_bursts_across_jitter_missing_beats_and_tempo_changes():
    rhythm = AudioRhythm(
        beats_ms=(1000, 1508, 1996, 2500, 3500, 4000, 4330, 4660, 4990),
        onsets_ms=(1126, 1249, 1380, 3108, 3246, 3377, 4110, 4221),
    )
    chart = audio_beat_passage(rhythm, 5200)

    assert [note.time_ms for note in chart.notes] == [
        1000, 1125, 1250, 1375, 1500, 2000, 2500, 3000, 3125, 3250, 3375, 3500,
        4000, 4110, 4220, 4330, 4660, 4990,
    ]
    assert [note.beat_length_ms for note in chart.notes if note.time_ms in (3500, 4000)] == [500, 330]
    assert next(note for note in chart.notes if note.time_ms == 3000).on_beat


def test_grid_splits_a_modest_tempo_change_before_beats_drift():
    beats = (*range(1000, 5000, 500), *range(5000, 9501, 450))
    grid = beat_grid(beats)
    assert grid.beat_length(2000) == 500
    assert grid.beat_length(8000) == 450
    assert max(abs(a - b) for a, b in zip(beats, grid.beats_ms, strict=True)) <= 42


def test_audio_chart_maps_sustains_and_leaves_silent_sections_empty():
    rhythm = AudioRhythm(
        beats_ms=tuple(range(1000, 8500, 500)),
        onsets_ms=(1000, 2300, 3000, 7000),
        sustained_ms=((1000, 2030), (3000, 6110)),
        silence_ms=((2450, 2950), (6200, 6950), (7450, 8500)),
    )
    chart = audio_beat_passage(rhythm, 8500)
    slider, spinner, final = chart.notes
    assert (slider.time_ms, slider.end_ms, slider.spans) == (1000, 2000, 2)
    assert spinner.spinner and (spinner.time_ms, spinner.end_ms) == (3000, 6000)
    assert final.time_ms == 7000


def test_audio_chart_uses_onsets_in_beat_gaps_without_displacing_detected_beats():
    rhythm = AudioRhythm(
        beats_ms=(1500, 2000, 2500, 4200, 4700, 5200),
        onsets_ms=(250, 600, 1350, 1751, 2243, 2620, 2800, 3100, 4070, 4440, 5450, 5850),
    )
    chart = audio_beat_passage(rhythm, 5700)
    assert [note.time_ms for note in chart.notes] == [
        250, 600, 1500, 1750, 2000, 2250, 2500, 2800, 3100, 4200, 4450, 4700, 5200, 5450,
    ]


def test_line_chart_uses_the_song_grid_within_its_own_boundaries():
    line, = lyric_passages([LyricLine(2.1, "A whole line", end=3.35)], 0, 4500)
    rhythm = AudioRhythm(beats_ms=(2000, 2500, 3000, 3500), onsets_ms=(2243, 2745, 3260))
    passage = apply_audio_rhythm(line, rhythm)
    assert [(note.time_ms, note.text) for note in passage.notes] == [
        (2100, "A whole line"), (2500, "A whole line"), (2750, "A whole line"), (3000, "A whole line"),
    ]


def test_sustained_line_slider_absorbs_beat_taps_and_leaves_room_for_the_next_attack():
    line, = lyric_passages([LyricLine(1, "Hold the line", end=3.1)], 0, 4000)
    rhythm = AudioRhythm(beats_ms=(1000, 1500, 2000, 2500, 3000), onsets_ms=(1005, 2490),
                         sustained_ms=((1005, 2420),))
    generated = apply_audio_rhythm(line, rhythm)
    assert [(note.time_ms, note.finish_ms, note.text) for note in generated.notes] == [
        (1000, 2250, "Hold the line"), (2500, 2500, "Hold the line"),
    ]

    karaoke = Passage("Karaoke", (Note(1000, "Authored tap"),))
    full = full_song_passage((karaoke, generated))
    assert not full.notes[0].slider
    assert full.notes[0].text == "Authored tap / Hold the line"


@pytest.mark.parametrize(("line_end", "tail"), [(1.6, 1450), (1.5, None)])
def test_line_slider_respects_the_next_lyric_boundary(line_end, tail):
    line, = lyric_passages([LyricLine(1, "Line", end=line_end)], 0, 3000)
    rhythm = AudioRhythm(onsets_ms=(1000,), sustained_ms=((1000, 2050),))
    note, = apply_audio_rhythm(line, rhythm).notes
    assert note.end_ms == tail


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
    assert set(rhythm.accents_ms) <= set(rhythm.onsets_ms)
    assert all(0 <= time < rhythm_games.DEMO.notes[-1].finish_ms + 750 for time in rhythm.accents_ms)
    chart = audio_beat_passage(rhythm, 15000)
    assert all(any(abs(note.time_ms - beat) < 30 for note in chart.notes) for beat in (1000, 1500, 2000, 2500))

    cancelled.set()
    assert analyze_audio_rhythm(track, cancelled) == AudioRhythm()


@pytest.mark.parametrize(("tone_seconds", "spinner"), [(0.95, False), (3.5, True)])
def test_audio_analysis_distinguishes_a_held_tone_from_short_attacks(tmp_path, tone_seconds, spinner):
    track = tmp_path / "held-tone.wav"
    rate = 22050
    final_attack = 1.2 + tone_seconds + 0.5
    track_seconds = final_attack + 0.8
    samples = array("h", [0]) * round(track_seconds * rate)
    for start, duration, frequency in ((0.45, 0.06, 720), (1.2, tone_seconds, 470), (final_attack, 0.065, 690)):
        first = round(start * rate)
        for index in range(round(duration * rate)):
            samples[first + index] = round(12000 * sin(tau * frequency * index / rate))
    with wave.open(str(track), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(samples.tobytes())

    rhythm = analyze_audio_rhythm(track, Event())
    (start, end), = rhythm.sustained_ms
    assert 1100 <= start <= 1250
    assert end - start == pytest.approx(tone_seconds * 1000, abs=75)
    first, sustained, final = audio_beat_passage(rhythm, round(track_seconds * 1000)).notes
    assert first.end_ms is None and final.end_ms is None
    assert sustained.finish_ms == pytest.approx(end, abs=35)
    assert sustained.spinner is spinner and sustained.slider is not spinner


@pytest.mark.parametrize("stereo", [False, True])
def test_audio_chart_follows_a_quieter_melody_in_a_mix(tmp_path, stereo):
    rate = 22050
    foreground = np.zeros(round(4.2 * rate))
    background = np.zeros_like(foreground)

    def add_tone(samples, start, end, midi, level):
        first, last = round(start * rate), round(end * rate)
        frames = np.arange(last - first)
        envelope = np.minimum(1, np.minimum(frames, frames[::-1]) / 100)
        phase = tau * (440 * 2 ** ((midi - 69) / 12)) * frames / rate
        samples[first:last] += level * envelope * (np.sin(phase) + 0.22 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase))

    add_tone(foreground, 0.2, 3.8, 74, 0.33)
    backing = ((0.625, 1.125, 63), (1.125, 1.75, 67), (1.75, 2.375, 65), (2.375, 3.25, 69))
    for start, end, midi in backing:
        add_tone(background, start, end, midi, 0.025 if stereo else 0.065)
    rng = np.random.default_rng(37)
    for start in np.arange(0.5, 4, 0.5):
        first, size = round(start * rate), round(0.052 * rate)
        foreground[first:first + size] += 0.18 * rng.uniform(-1, 1, size) * np.linspace(1, 0, size)

    samples = np.column_stack((foreground + background, foreground)) if stereo else foreground + background

    track = tmp_path / "backing-melody.wav"
    with wave.open(str(track), "wb") as audio:
        audio.setparams((2 if stereo else 1, 2, rate, 0, "NONE", ""))
        audio.writeframes((samples * 32767).astype("<i2").tobytes())

    rhythm = analyze_audio_rhythm(track, Event())
    chart = audio_beat_passage(rhythm, 4200)
    baseline = audio_beat_passage(replace(rhythm, melody_notes=()), 4200)
    assert [note.midi_note for note in rhythm.melody_notes] == [74, *(midi for _, _, midi in backing)]
    for note, (start, end, _) in zip(rhythm.melody_notes[1:], backing, strict=True):
        assert note.start_ms == pytest.approx(start * 1000, abs=25)
        assert note.end_ms == pytest.approx(end * 1000, abs=25)
        assert any(abs(target.time_ms - note.start_ms) <= 25 for target in chart.notes)
    assert not any(abs(note.time_ms - 1125) <= 25 for note in baseline.notes)


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


@pytest.mark.parametrize("rhythm", [AudioRhythm((1000, 2000, 3600), (2000,)), None])
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
        assert tuple(note.time_ms for note in dialog.passages[0].notes) == (
            (1000, 2000, 2400, 3600) if rhythm is not None else (2000, 2400)
        )
    finally:
        dialog.reject()
        host.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("rhythm", [
    AudioRhythm((2000, 2400)),
    AudioRhythm(melody_notes=(MelodyNote(2000, 2190, 63), MelodyNote(2400, 2580, 67))),
    None,
])
def test_audio_only_start_uses_detected_beats_or_returns_to_demo(qapp, monkeypatch, rhythm):
    host = QMainWindow()
    host.media_player = SimpleNamespace(playbackState=lambda: QMediaPlayer.PlaybackState.StoppedState)
    host.volume_slider = SimpleNamespace(value=lambda: 25)
    api = SimpleNamespace(get_current_track=lambda: "song.mp3", get_position=lambda: 2000,
                          get_setting=lambda _key, default: default, pause=Mock())
    dialog = RhythmDialog(host, api, (), audio_only=True, duration_ms=3000)
    host.audio_output = dialog.output
    monkeypatch.setattr(dialog.player, "setSource", Mock())
    dialog._rhythm_job = Mock()
    try:
        assert dialog.passage.currentText() == "Audio beats"
        dialog.start.click()
        assert dialog.round is None and not dialog.start.isEnabled()
        dialog._rhythm_ready(rhythm)
        if rhythm is None:
            assert dialog.passage.currentText() == "Demo" and dialog.start.isEnabled()
            assert dialog.round is None and not api.pause.called
        else:
            assert dialog._selected_passage().full_song
            assert [note.time_ms for note in dialog.round.notes] == [2000, 2400]
            api.pause.assert_called_once()
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


def test_continuous_short_groups_follow_a_shallow_arc_then_a_line():
    notes = tuple(Note(time, str(time)) for time in range(1000, 2125, 125))
    field = QRectF(44, 44, 424, 296)
    radius = 36.5
    centers = generate_circle_centers(notes, field, radius)
    assert len(centers) == len(notes)
    assert all(field.contains(center) for center in centers)
    assert centers == generate_circle_centers(notes, field, radius)

    turns = []
    for group in (centers[:5], centers[5:]):
        steps = [b - a for a, b in zip(group, group[1:])]
        assert all(hypot(step.x(), step.y()) == pytest.approx(33.75) for step in steps)
        turns.append([atan2(a.x() * b.y() - a.y() * b.x(), QPointF.dotProduct(a, b))
                      for a, b in zip(steps, steps[1:])])
    assert abs(sum(turns[0])) == pytest.approx(tau / 8)
    assert turns[0] == pytest.approx([turns[0][0]] * 3)
    assert turns[1] == pytest.approx([0, 0])


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


def test_demo_has_a_spiral_to_practice():
    notes = rhythm_games.DEMO.notes
    field = QRectF(44, 44, 424, 296)
    centers = generate_circle_centers(notes, field, 36.5)
    (spiral,) = (pattern for pattern, shape, _ in rhythm_games.plan_circle_patterns(notes, 36.5) if shape == "spiral")
    start = notes.index(spiral[0])
    points = centers[start:start + len(spiral)]
    steps = [b - a for a, b in zip(points, points[1:])]
    turns = [abs(atan2(a.x() * b.y() - a.y() * b.x(), QPointF.dotProduct(a, b)))
             for a, b in zip(steps, steps[1:])]
    assert all(a > b > 0 for a, b in zip(turns, turns[1:]))
    assert all(32 < hypot(step.x(), step.y()) < 35.5 for step in steps)


@pytest.mark.parametrize("rest_ms", [0, 8000])
def test_generated_long_streams_stay_open_even_after_a_rest(rest_ms):
    notes = tuple(Note(1000 + stream * (1750 + rest_ms) + step * 125, "", beat_length_ms=500)
                  for stream in range(4) for step in range(14))
    field = QRectF(44, 44, 424, 296)
    placements = tuple(rhythm_games.plan_circle_patterns(notes, 36.5))
    shapes = ("zigzag_stream", "s_curve", "line", "zigzag_stream")
    expected_sizes = (14,) if rest_ms else (4, 4, 3, 3)
    assert [(len(pattern), shape) for pattern, shape, _ in placements] == [
        (size, shape) for shape in shapes for size in expected_sizes
    ]
    assert tuple(note for pattern, _, _ in placements for note in pattern) == notes

    centers = generate_circle_centers(notes, field, 36.5)
    assert all(field.contains(point) for point in centers)
    start = 0
    for pattern, _, _ in placements:
        end = start + len(pattern)
        stream = centers[start:end]
        heading = stream[1] - stream[0]
        assert all(QPointF.dotProduct(heading, b - a) > 0 for a, b in zip(stream, stream[1:]))
        start = end


@pytest.mark.parametrize("interval", [100, 125, 150, 166])
def test_connected_fast_groups_continue_forward_and_avoid_the_earlier_path(interval):
    field = QRectF(44, 44, 424, 296)
    radius = 36.5
    notes = tuple(Note(1000 + step * interval, "", beat_length_ms=500) for step in range(56))
    centers = generate_circle_centers(notes, field, radius)
    assert len(centers) == len(notes) and all(field.contains(point) for point in centers)
    groups = [pattern for pattern, _, _ in rhythm_games.plan_circle_patterns(notes, radius)]
    start = len(groups[0])
    for previous, following in zip(groups, groups[1:]):
        incoming = centers[start - 1] - centers[start - min(5, len(previous))]
        entry = centers[start] - centers[start - 1]
        assert QPointF.dotProduct(incoming, entry) > 0
        assert hypot(entry.x(), entry.y()) >= radius * 0.45
        end = start + len(following)
        older = centers[max(0, start - 9):start - 2]
        assert all(hypot(point.x() - other.x(), point.y() - other.y()) >= radius * 1.25
                   for point in centers[start:end] for other in older)
        start = end


def test_zigzag_stream_fits_a_straight_cursor_path_and_spaces_out_accented_steps():
    notes = tuple(Note(1000 + step * 125, "", beat_length_ms=500, accented=step in (4, 8)) for step in range(14))
    radius = 36.5
    stream = generate_circle_centers(notes, QRectF(44, 44, 424, 296), radius)
    direction = stream[-1] - stream[0]
    length = hypot(direction.x(), direction.y())
    lane_offsets = []
    for point in stream[1:-1]:
        delta = point - stream[0]
        lane_offsets.append((direction.x() * delta.y() - direction.y() * delta.x()) / length)
    assert max(abs(offset) for offset in lane_offsets) < radius / 2
    assert all(before * after < 0 for before, after in zip(lane_offsets, lane_offsets[1:]))

    steps = [b - a for a, b in zip(stream, stream[1:])]
    distances = [hypot(step.x(), step.y()) for step in steps]
    assert distances[3] == pytest.approx(distances[7])
    assert distances[3] > 1.15 * distances[2]


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


@pytest.mark.parametrize("interval", [125, 250])
def test_repeated_demanding_groups_switch_to_shorter_gentler_patterns(interval):
    phrase_length = 4 * interval + 550
    notes = tuple(Note(1000 + phrase * phrase_length + step * interval, "", beat_length_ms=500)
                  for phrase in range(4) for step in range(5))
    field = QRectF(44, 44, 424, 296)
    centers = generate_circle_centers(notes, field, 36.5)
    assert centers == generate_circle_centers(notes, field, 36.5)
    assert len(centers) == len(notes) and all(field.contains(point) for point in centers)

    distances, turns = [], []
    for phrase in range(4):
        points = centers[phrase * 5:phrase * 5 + 5]
        steps = [b - a for a, b in zip(points, points[1:])]
        distances.append(hypot(steps[0].x(), steps[0].y()))
        turns.append(max(abs(atan2(a.x() * b.y() - a.y() * b.x(), QPointF.dotProduct(a, b)))
                         for a, b in zip(steps, steps[1:])))
    assert turns[0] == pytest.approx(turns[1])
    assert turns[2] < turns[0] * 0.5
    assert distances[2] < min(distances[0], distances[1])
    assert turns[3] != pytest.approx(turns[2])


def test_chains_of_wide_jumps_shorten_periodically_and_reset_at_a_rest():
    notes = tuple(Note(time, "", beat_length_ms=500) for time in (1000, 1500, 2000, 2500, 3000, 3800, 4300, 4800, 5300))
    centers = generate_circle_centers(notes, QRectF(44, 44, 424, 296), 36.5)
    jumps = [b - a for a, b in zip(centers, centers[1:])]
    assert [hypot(jump.x(), jump.y()) for jump in jumps] == pytest.approx([150, 150, 120, 150, 180, 150, 150, 120])


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


def test_curved_repeat_slider_follows_the_track_at_constant_speed(qapp):
    board = RhythmBoard(None)
    slider = Note(1000, "slide", end_ms=2600, spans=2, beat_length_ms=400)
    try:
        board.set_frame((slider,), 0, 1000)
        start, _, middle, _, end = [board.slider_ball(0, time) for time in range(1000, 1801, 200)]
        assert hypot(*(middle - (start + end) / 2).toTuple()) > 15 * board._scale
        assert board.slider_ball(0, 2200) == middle
        assert board.slider_ball(0, 2600) == start

        path = board._slider_paths[0]
        quarter_points = [path.at(fraction / 4) for fraction in range(5)]
        distances = [hypot(*(b - a).toTuple()) for a, b in zip(quarter_points, quarter_points[1:])]
        assert distances == pytest.approx([path.distances[-1] / 4] * 4, rel=0.02)
        assert slider.slider_checkpoints() == ((1400, 10), (1800, 30), (2200, 10), (2600, 30))
        round_ = Round((slider,))
        assert round_.tap(1000, 0) == (None, True)
        for time_ms, _ in slider.slider_checkpoints():
            judgements = round_.advance(time_ms, lambda _: True)
        assert judgements == [Judgement(0, 300)]
    finally:
        board.deleteLater()


def test_long_straight_slider_fits_from_the_middle_of_the_field():
    field = QRectF(44, 44, 424, 296)
    center = field.center()
    control, end = rhythm_games.slider_curve(Note(1000, "", end_ms=2500), 1, center, field)
    assert field.contains(control) and field.contains(end)
    assert hypot(*(end - center).toTuple()) > 150


def test_next_circle_stays_clear_of_a_long_slider_body(qapp):
    board = RhythmBoard(None)
    slider = Note(1000, "slide", end_ms=2450, beat_length_ms=500)
    try:
        board.set_frame((slider, Note(2700, "tap", beat_length_ms=500)), 0, 1000)
        next_center = board.target_center(1)
        clearance = min(hypot(*(board.slider_ball(0, time_ms) - next_center).toTuple())
                        for time_ms in range(slider.time_ms, slider.end_ms + 1, 25))
        assert clearance > 2 * board.target_radius
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
