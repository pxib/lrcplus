from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtTest import QTest

from assets.scarlet.boss_music import BossTheme, STAGE_THEME_PATH, THEME_PATH
from assets.scarlet.danmaku_game import DanmakuWindow
from assets.scarlet.dialogue import BGM_CUE_INDEX


@pytest.fixture
def stage_audio(monkeypatch, fake_midi_factory, fake_sound_effects):
    stage = fake_midi_factory(STAGE_THEME_PATH)
    boss = fake_midi_factory(THEME_PATH)
    events = []
    for name, backend in (("stage", stage), ("boss", boss)):
        monkeypatch.setattr(backend, "prepare", lambda name=name: events.append((name, "prepare")), raising=False)
        for operation in ("play", "close"):
            original = getattr(backend, operation)

            def record(*args, name=name, operation=operation, original=original, **kwargs):
                events.append((name, operation))
                return original(*args, **kwargs)

            monkeypatch.setattr(backend, operation, record)
    monkeypatch.setattr("assets.scarlet.boss_music.make_midi_backend", fake_midi_factory)
    monkeypatch.setattr("assets.scarlet.sound_effects.make_sound_effect", fake_sound_effects.factory)
    return SimpleNamespace(stage=stage, boss=boss, events=events)


@pytest.fixture
def window(qapp, stage_audio):
    window = DanmakuWindow()
    window.show()
    qapp.processEvents()
    window.timer.stop()
    yield window
    window.close()
    window.deleteLater()


def test_stage_theme_starts_with_warmup_and_loops(window, stage_audio):
    assert window.story_phase == "warmup"
    assert window.music.path == STAGE_THEME_PATH
    assert stage_audio.events[:2] == [("stage", "prepare"), ("stage", "play")]
    assert stage_audio.stage.calls == [("play", True)]
    assert not stage_audio.boss.calls
    stage_audio.stage.current_mode = "stopped"
    window.music._poll()
    assert stage_audio.stage.calls == [("play", True), ("play", True)]


def test_stage_theme_obeys_pause_and_mute_without_restarting(window, stage_audio):
    QTest.keyClick(window, Qt.Key.Key_P)
    assert stage_audio.stage.calls[-1] == ("pause",)
    QTest.keyClick(window, Qt.Key.Key_M)
    assert window.music.muted and window.sfx.muted
    QTest.keyClick(window, Qt.Key.Key_P)
    assert window.game.state == "playing"
    assert stage_audio.stage.current_mode == "paused"
    QTest.keyClick(window, Qt.Key.Key_M)
    assert stage_audio.stage.calls[-1] == ("play", False)
    assert not stage_audio.boss.calls


def test_stage_continues_through_entrance_and_dialogue_until_boss_cue(window, stage_audio):
    window._begin_entrance()
    window._sync_music()
    assert window.music.path == STAGE_THEME_PATH
    assert stage_audio.stage.calls == [("play", True)]
    assert stage_audio.stage.current_mode == "playing"
    assert not stage_audio.boss.calls
    window._update_story(window.ENTRANCE_DURATION)
    for _ in range(BGM_CUE_INDEX - 1):
        window._advance_dialogue()
        assert stage_audio.stage.current_mode == "playing"
        assert stage_audio.stage.calls == [("play", True)]
    assert not stage_audio.boss.calls
    window._advance_dialogue()
    assert window.story_phase == "intro"
    assert window.music.path == THEME_PATH
    assert stage_audio.stage.current_mode == "stopped"
    assert stage_audio.events[-3:] == [("stage", "close"), ("boss", "prepare"), ("boss", "play")]
    assert stage_audio.boss.calls == [("play", True)]
    assert stage_audio.events.index(("stage", "close")) < stage_audio.events.index(("boss", "play"))
    while window.story_phase == "intro":
        window._advance_dialogue()
    assert stage_audio.boss.calls == [("play", True)]


@pytest.mark.parametrize("after_cue", [False, True])
def test_dialogue_music_pauses_and_resumes_current_track(window, stage_audio, after_cue):
    window._begin_entrance()
    window._update_story(window.ENTRANCE_DURATION)
    for _ in range(BGM_CUE_INDEX if after_cue else 1):
        window._advance_dialogue()
    backend = stage_audio.boss if after_cue else stage_audio.stage
    assert backend.current_mode == "playing"
    window._pause()
    assert backend.calls[-1] == ("pause",)
    QCoreApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
    assert backend.calls[-1] == ("play", False)
    assert window.story_phase == "intro"


@pytest.mark.parametrize("skip", [False, True])
def test_boss_music_continues_during_outro_until_finish_or_skip(window, stage_audio, skip):
    window._start_combat()
    calls = list(stage_audio.boss.calls)
    window.game.state = "won"
    window._check_battle_end()
    assert window.story_phase == "outro"
    assert stage_audio.boss.current_mode == "playing"
    assert stage_audio.boss.calls == calls
    window._pause()
    assert stage_audio.boss.calls[-1] == ("pause",)
    QCoreApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
    assert stage_audio.boss.calls[-1] == ("play", False)
    if skip:
        QTest.keyClick(window, Qt.Key.Key_Escape)
    else:
        while window.story_phase == "outro":
            assert stage_audio.boss.current_mode == "playing"
            window._advance_dialogue()
    assert window.story_phase == "complete"
    assert stage_audio.boss.calls[-1] == ("close",)
    assert not window.music.timer.isActive()


def test_skip_and_restart_switch_tracks_without_overlap(window, stage_audio):
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert window.story_phase == "combat"
    assert stage_audio.stage.current_mode == "stopped"
    assert stage_audio.boss.current_mode == "playing"
    QTest.keyClick(window, Qt.Key.Key_R)
    assert window.story_phase == "warmup"
    assert window.music.path == STAGE_THEME_PATH
    assert stage_audio.boss.current_mode == "stopped"
    assert stage_audio.stage.calls[-1] == ("play", True)
    window._change_difficulty("Lunatic")
    window._sync_music()
    assert window.game.difficulty == "Lunatic"
    assert window.music.path == STAGE_THEME_PATH
    assert stage_audio.stage.current_mode == "playing"


def test_hidden_warmup_releases_device_and_reopens_paused(window, stage_audio, qapp):
    window.hide()
    assert stage_audio.stage.calls[-1] == ("close",)
    assert not window.music.timer.isActive()
    window.show()
    qapp.processEvents()
    window.timer.stop()
    assert window.game.state == "paused"
    assert stage_audio.stage.current_mode == "stopped"
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert stage_audio.stage.calls[-1] == ("play", True)
    window.close()
    assert stage_audio.stage.calls[-1] == ("close",)


def test_mute_is_preserved_when_switching_to_boss_theme(window, stage_audio):
    QTest.keyClick(window, Qt.Key.Key_M)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert window.story_phase == "combat" and window.music.muted
    assert stage_audio.stage.current_mode == "stopped"
    assert not stage_audio.boss.calls
    QTest.keyClick(window, Qt.Key.Key_M)
    assert stage_audio.boss.calls == [("play", True)]


def test_missing_stage_theme_does_not_block_boss_music(qapp, stage_audio, monkeypatch, tmp_path):
    monkeypatch.setattr("assets.scarlet.danmaku_game.STAGE_THEME_PATH", tmp_path / "missing.mid")
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        assert window.music.error
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert not window.music.error
        assert stage_audio.boss.calls == [("play", True)]
    finally:
        window.close()
        window.deleteLater()


def test_parent_destruction_releases_current_track_after_switch(qapp, stage_audio):
    parent = QObject()
    theme = BossTheme(parent, path=STAGE_THEME_PATH)
    theme.sync("playing")
    theme.set_track(THEME_PATH)
    theme.prepare()
    assert stage_audio.stage.current_mode == "stopped"
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert stage_audio.boss.calls[-1] == ("close",)
