from math import hypot

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from assets.scarlet.danmaku import Bullet, DanmakuGame, HomingShot, WarmupEnemy
from assets.scarlet.danmaku_game import DanmakuWindow
from core.settings import load_all_settings


def advance(game, seconds, **controls):
    while seconds > 1e-8:
        step = min(seconds, .1)
        game.update(step, **controls)
        seconds -= step


@pytest.mark.parametrize("mode", ["warmup", "boss"])
@pytest.mark.parametrize("shot_type", [Bullet, HomingShot])
@pytest.mark.parametrize("position, velocity", [
    ((.1, 200), (-360, 0)), ((383.9, 200), (360, 0)),
    ((100, .1), (0, -360)), ((100, 447.9), (0, 360)),
])
def test_player_shots_are_removed_as_soon_as_they_cross_any_edge(mode, shot_type, position, velocity):
    game = DanmakuGame()
    game.mode = mode
    game.start_or_pause()
    game._warmup_spawn_in = game._spawn_in = 100
    shot = shot_type(*position, *velocity)
    game.shots = [shot]
    game.update(1 / 120)
    assert not game.shots
    # A later target cannot bring the removed projectile back.
    game.enemies = [WarmupEnemy(100, 100)]
    advance(game, 1)
    assert not game.shots


@pytest.mark.parametrize("position", [(-.1, 100), (384.1, 100), (100, -.1), (100, 448.1)])
def test_already_offscreen_homing_shots_cannot_steer_back_in(position):
    game = DanmakuGame()
    game.start_warmup()
    game.enemies = [WarmupEnemy(100, 100)]
    game.shots = [HomingShot(*position, 360, 0)]
    game.update(1 / 120)
    assert not game.shots


@pytest.mark.parametrize("difficulty", DanmakuGame.DIFFICULTIES)
def test_fairies_fly_in_smoothly_and_wait_until_arrival_to_attack(difficulty):
    game = DanmakuGame(difficulty)
    game.start_warmup()
    advance(game, .4)
    left, right = game.enemies
    assert left.y < 0 and right.y < 0
    assert left.entering and right.entering
    start_x, start_y = left.x, left.y
    advance(game, .3)
    assert start_x < left.x < left.target_x
    assert start_y < left.y < 38
    assert not game.bullets
    position, age = (left.x, left.y), left.age
    game.pause()
    advance(game, 1)
    assert (left.x, left.y) == position and left.age == age
    game.start_or_pause()
    advance(game, .5)
    assert not left.entering
    assert left.x == left.target_x and 38 <= left.y < 39
    assert not game.bullets
    advance(game, .81)
    assert game.bullets


@pytest.mark.parametrize("phase", [0, 2, 4])
def test_lunatic_boss_patterns_are_denser_faster_and_more_frequent(phase):
    normal, lunatic = (DanmakuGame(difficulty) for difficulty in DanmakuGame.DIFFICULTIES)
    for game in (normal, lunatic):
        game._phase = phase
        game.start_or_pause()
        game.update(.1)
        game.update(.1)
        game.update(.06)
    assert len(lunatic.bullets) > len(normal.bullets)
    assert max(hypot(b.vx, b.vy) for b in lunatic.bullets) > max(
        hypot(b.vx, b.vy) for b in normal.bullets)
    # Its next volley occurs earlier, even though the opening volley is shared.
    normal.bullets.clear()
    lunatic.bullets.clear()
    advance(normal, .9)
    advance(lunatic, .9)
    assert len(lunatic.bullets) > len(normal.bullets)
    lunatic.reset()
    assert lunatic.difficulty == "Lunatic"


def test_lunatic_fairies_fire_fans_more_often():
    normal, lunatic = (DanmakuGame(difficulty) for difficulty in DanmakuGame.DIFFICULTIES)
    for game in (normal, lunatic):
        game.start_warmup()
        game.enemies = [WarmupEnemy(100, 100, fire_in=0)]
        game.update(1 / 120)
    assert len(normal.bullets) == 1 and len(lunatic.bullets) == 3
    assert lunatic.enemies[0].fire_in < normal.enemies[0].fire_in


@pytest.mark.parametrize("difficulty", DanmakuGame.DIFFICULTIES)
def test_both_difficulties_can_complete_the_encounter_with_bounded_bullets(difficulty):
    game = DanmakuGame(difficulty)
    game.invulnerable = 1000
    game.start_warmup()
    advance(game, game.WARMUP_DURATION)
    game.prepare_boss()
    game.start_or_pause()
    for phase in game.PHASES:
        advance(game, 3, shooting=True)
        assert len(game.bullets) < 1800 and len(game.shots) < 200
        if phase.damage_finish:
            game.hp = 0
        else:
            game.phase_elapsed = game.PHASE_DURATION
        game._check_win()
        advance(game, game.PHASE_BREAK)
    assert game.state == "won" and game.phase == len(game.PHASES) - 1


def test_difficulty_selector_restarts_run_and_keeps_scores_separate(qapp, monkeypatch, fake_midi, fake_midi_factory, fake_sound_effects):
    from assets.scarlet.boss_music import STAGE_THEME_PATH

    preparations = []
    stage_midi = fake_midi_factory(STAGE_THEME_PATH)
    monkeypatch.setattr(stage_midi, "prepare", lambda: preparations.append(True), raising=False)
    monkeypatch.setattr("assets.scarlet.boss_music.make_midi_backend", fake_midi_factory)
    monkeypatch.setattr("assets.scarlet.sound_effects.make_sound_effect", fake_sound_effects.factory)
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        assert preparations == [True]
        assert fake_midi.calls == []
        assert window.difficulty_selector.isVisible()
        assert window.difficulty_selector.currentText() == "Normal"
        window.game.score = 1234
        window.game.power = 90
        window.difficulty_selector.setCurrentText("Lunatic")
        window.difficulty_selector.activated.emit(1)
        assert window.game.difficulty == "Lunatic"
        assert window.high_score == 0
        assert window.story_phase == "warmup"
        assert window.game.power == window.game.score == 0
        window.game.score = 4321
        QTest.keyClick(window, Qt.Key.Key_R)
        assert window.game.difficulty == "Lunatic"
        assert window.high_score == 4321 and window.game.score == 0
        window.difficulty_selector.setCurrentText("Normal")
        window.difficulty_selector.activated.emit(0)
        assert window.game.difficulty == "Normal" and window.high_score == 1234
        record = load_all_settings()["easter_eggs"]
        assert record["scarlet_high_score"] == 1234
        assert record["scarlet_lunatic_high_score"] == 4321
    finally:
        window.close()
        window.deleteLater()
