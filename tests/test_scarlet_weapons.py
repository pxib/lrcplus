from math import hypot
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest

from assets.scarlet.danmaku import (
    Bullet, DanmakuGame, HomingShot, PointItem, PowerItem, WarmupEnemy,
)
from assets.scarlet.danmaku_game import DanmakuWindow, ReimuSprite
from assets.scarlet.danmaku_scene import ScarletScene


def advance(game, seconds, **controls):
    while seconds > 1e-8:
        step = min(seconds, .1)
        game.update(step, **controls)
        seconds -= step


@pytest.fixture
def quiet_audio(monkeypatch, fake_midi, fake_sound_effects):
    monkeypatch.setattr("assets.scarlet.boss_music.make_midi_backend", lambda path: fake_midi)
    monkeypatch.setattr("assets.scarlet.sound_effects.make_sound_effect", fake_sound_effects.factory)


@pytest.mark.parametrize("power, straight_count", [(0, 3), (9, 3), (10, 3), (11, 6), (128, 6)])
@pytest.mark.parametrize("focus", [False, True])
def test_shot_counts_and_origins_follow_power_and_focus(power, straight_count, focus):
    game = DanmakuGame()
    game.power = power
    game.start_or_pause()
    game.update(1 / 120, focus=focus, shooting=True)
    main = [shot for shot in game.shots if not isinstance(shot, HomingShot)]
    homing = [shot for shot in game.shots if isinstance(shot, HomingShot)]
    assert len(main) == straight_count
    assert len(homing) == (0 if focus else 5)
    origins = game.option_positions if power > 10 else ((game.x, game.y - 14),)
    for shot in main:
        x, y = shot.x - shot.vx / 120, shot.y - shot.vy / 120
        assert any(hypot(x - ox, y - oy) < 1e-8 for ox, oy in origins)
    for shot in homing:
        assert shot.x - shot.vx / 120 == pytest.approx(game.x)
        assert shot.y - shot.vy / 120 == pytest.approx(game.y - 6)


def test_options_move_together_above_reimu_and_return_smoothly():
    game = DanmakuGame()
    game.start_or_pause()
    side = game.option_positions
    game.update(.08, focus=True)
    middle = game.option_positions
    assert side[0][0] < middle[0][0] < game.x
    assert side[1][0] > middle[1][0] > game.x
    assert middle[0][1] == middle[1][1] < side[0][1]
    game.update(.08, focus=True)
    focused = game.option_positions
    assert focused[0][0] == game.x - 10 and focused[1][0] == game.x + 10
    assert focused[0][1] == game.y - 31
    game.pause()
    game.update(.1)
    assert game.option_positions == focused
    game.start_or_pause()
    advance(game, .16)
    assert game.option_positions == side


def test_options_rotate_oppositely_and_hide_below_threshold(qapp, monkeypatch):
    game = DanmakuGame()
    scene, sprite = ScarletScene(), ReimuSprite()
    scene.update_presentation(.1, False, 0, True)
    angle = scene.option_rotation
    scene.update_presentation(.1, False, 0, True, paused=True)
    assert scene.option_rotation == angle
    calls = []
    monkeypatch.setattr(scene, "yin_yang", lambda p, x, y, angle: calls.append((x, y, angle)))
    image = QImage(384, 448, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        scene.actors(painter, game, sprite, 0)
        assert len(calls) == 2
        assert calls[0][2] == -calls[1][2] != 0
        calls.clear()
        game.power = 10
        scene.actors(painter, game, sprite, 0)
        assert not calls
    finally:
        painter.end()


def test_ordinary_and_scattered_items_fall_vertically_at_constant_speed():
    game = DanmakuGame()
    game.start_or_pause()
    game._spawn_in = 100
    ordinary = PointItem(35, 80)
    game.items = [ordinary]
    game._scatter_points((200, 300))
    advance(game, .3)
    before = [(item.x, item.y) for item in game.items]
    advance(game, .2)
    assert len(game.items) == 11
    for item, (x, y) in zip(game.items, before):
        assert item.x == x
        assert item.y == pytest.approx(y + game.ITEM_SPEED * .2)
        assert item.vy == game.ITEM_SPEED
        assert item.burst_remaining == 0


def test_pickup_popup_matches_score_rises_linearly_freezes_and_expires():
    game = DanmakuGame()
    game.start_or_pause()
    game.items = [PointItem(game.x, game.y), PowerItem(game.x, game.y)]
    game.power = 8
    game.update(1 / 120)
    assert game.score == 1000 and game.point_items == 1
    assert game.power == 12 and game.has_options
    assert len(game.score_popups) == 1
    popup = game.score_popups[0]
    assert popup.value == 1000
    x, y = popup.x, popup.y
    game.update(.1)
    assert popup.x == x and popup.y == pytest.approx(y - 3.2)
    game.pause()
    advance(game, .5)
    assert popup.y == pytest.approx(y - 3.2)
    game.start_or_pause()
    advance(game, .7)
    assert not game.score_popups


@pytest.mark.parametrize("phase", [0, 1, 2])
def test_every_health_fill_blocks_shots_and_bombs_until_first_attack(phase):
    game = DanmakuGame()
    game._phase = phase
    game.start_or_pause()
    bx, by = game.boss
    game.shots = [Bullet(bx, by, 0, 0)]
    game.update(.1)
    game.bomb()
    assert game.hp == game.MAX_HP and game.score == 0 and not game.items
    assert game.hp_fill_progress == pytest.approx(.1 / game.HP_FILL_DURATION)
    assert not game.boss_vulnerable and game.wave == 0
    game.pause()
    remaining = game.hp_fill_remaining
    advance(game, .5)
    assert game.hp_fill_remaining == remaining
    game.start_or_pause()
    advance(game, .25)
    assert game.wave > 0 and game.bullets
    assert not game.boss_vulnerable
    assert game.phase_elapsed == 0
    advance(game, .9)
    assert game.boss_vulnerable
    bx, by = game.boss
    game.shots = [Bullet(bx, by, 0, 0)]
    game.update(1 / 120)
    assert game.hp == game.MAX_HP - 1
    assert "bossdamage" in game.take_sound_events()


def test_boss_stays_protected_if_its_first_volley_has_not_fired():
    game = DanmakuGame()
    game.start_or_pause()
    game._spawn_in = 100
    advance(game, 1.3)
    assert game.hp_fill_progress == 1
    assert not game.boss_vulnerable
    game.bomb()
    assert game.hp == game.MAX_HP


def test_bomb_cooldown_prevents_spam_freezes_on_pause_and_allows_reuse():
    game = DanmakuGame()
    game.start_or_pause()
    game.hp_fill_remaining = 0
    game.wave = 1
    game.bomb()
    for _ in range(10):
        game.bomb()
    assert game.bombs == 2 and game.hp == game.MAX_HP - 24
    assert game.bomb_cooldown == game.BOMB_COOLDOWN
    game.pause()
    advance(game, 3)
    assert game.bomb_cooldown == game.BOMB_COOLDOWN
    game.start_or_pause()
    advance(game, game.BOMB_COOLDOWN - .001)
    game.bomb()
    assert game.bombs == 2
    advance(game, .002)
    game.bomb()
    assert game.bombs == 1 and game.hp == game.MAX_HP - 48
    game.reset()
    assert game.bomb_cooldown == 0


def test_warmup_enemies_drop_power_and_points_once_and_keep_inventory_for_boss():
    game = DanmakuGame()
    game.start_warmup()
    assert game.power == 0
    enemy = WarmupEnemy(game.x, 120, hp=1)
    game.enemies = [enemy]
    game.shots = [Bullet(enemy.x, enemy.y, 0, 0), Bullet(enemy.x, enemy.y, 0, 0)]
    game.update(1 / 120)
    assert not game.enemies
    assert len(game.items) == 2
    assert sum(isinstance(item, PowerItem) for item in game.items) == 1
    for item in game.items:
        item.x, item.y = game.x, game.y
    game.update(1 / 120)
    assert game.power == game.WARMUP_POWER and game.point_items == 1
    lives, bombs, score = game.lives, game.bombs, game.score
    game.prepare_boss()
    assert game.power == game.WARMUP_POWER and game.state == "ready"
    assert (game.lives, game.bombs, game.score) == (lives, bombs, score)
    assert game.elapsed == 0 and not game.bullets and not game.shots
    assert game.hp_fill_progress == 0


def test_first_three_warmup_waves_and_bombs_leave_pickups():
    game = DanmakuGame()
    game.start_warmup()
    game.invulnerable = 100
    advance(game, 6.1)
    assert len(game.enemies) == 6 and game._warmup_wave == 3
    assert game.bullets and game.hp == game.MAX_HP
    game.bomb()
    assert not game.enemies
    assert len(game.items) == 12
    assert sum(isinstance(item, PowerItem) for item in game.items) == 6


def test_longer_warmup_can_reach_90_power_from_one_side():
    game = DanmakuGame()
    game.start_warmup()
    game.invulnerable = 100
    for _ in range(int(game.WARMUP_DURATION * 100)):
        powers = [item for item in game.items if isinstance(item, PowerItem) and item.x < game.WIDTH / 2]
        target = max(powers, key=lambda item: item.y, default=None)
        direction = 0 if target is None or abs(target.x - game.x) < 2 else (1 if target.x > game.x else -1)
        game.update(.01, dx=direction, shooting=True)
    assert not game.enemies and game._warmup_wave == game.WARMUP_WAVES
    assert game.power >= 90 and game.has_options


def test_active_health_bar_visibly_fills_from_left_to_right(qapp):
    game = DanmakuGame()
    scene, sprite = ScarletScene(), ReimuSprite()
    scene.story_phase = "combat"
    scene.boss_position = None
    for progress in (0, .5, 1):
        game.hp_fill_remaining = game.HP_FILL_DURATION * (1 - progress)
        image = QImage(640, 480, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        scene.paint(painter, game, sprite, 0, 0, 0, 60)
        painter.end()
        left = image.pixelColor(32 + 90, 16 + 4).name()
        right = image.pixelColor(32 + 300, 16 + 4).name()
        assert left == ("#3b1526" if progress == 0 else "#fff0d9")
        assert right == ("#fff0d9" if progress == 1 else "#3b1526")


def test_window_runs_warmup_then_entrance_dialogue_and_combat(qapp, quiet_audio, fake_midi):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window.clock = SimpleNamespace(restart=lambda: 100)
        assert window.story_phase == "warmup" and window.game.power == 0
        QTest.keyPress(window, Qt.Key.Key_Z)
        for _ in range(10):
            window._tick()
        assert window.game.enemies and window.game.shots
        assert window.dialogue_index == 0 and not fake_midi.calls
        QTest.keyRelease(window, Qt.Key.Key_Z)
        window.game.power = 12
        window.game.warmup_elapsed = window.game.WARMUP_DURATION
        window._tick()
        assert window.story_phase == "entrance"
        assert window.game.power == 12 and window.game.state == "ready"
        window._update_story(window.ENTRANCE_DURATION)
        assert window.story_phase == "intro"
        while window.story_phase == "intro":
            window._advance_dialogue()
        assert window.story_phase == "combat" and window.game.state == "playing"
        assert window.game.power == 12 and window.game.hp_fill_progress == 0
        window._restart()
        assert window.story_phase == "warmup" and window.game.power == 0
    finally:
        window.close()
        window.deleteLater()
