from math import hypot

import pytest

from assets.scarlet.danmaku import Bullet, DanmakuGame, PointItem, PowerItem, StarItem


def advance(game, seconds, **controls):
    while seconds > 1e-8:
        dt = min(.1, seconds)
        game.update(dt, **controls)
        seconds -= dt


@pytest.mark.parametrize("phase", [0, 1, 2])
@pytest.mark.parametrize("timeout", [False, True])
def test_clear_turns_each_bullet_into_a_star_once_and_recenters_without_attacking(phase, timeout):
    game = DanmakuGame()
    game.start_or_pause()
    game._phase = phase
    game.boss_elapsed = 2
    origin = game.boss
    old_item = PointItem(35, 150)
    game.items = [old_item]
    game.bullets = [Bullet(50, 80, 20, 30), Bullet(240, 190, -10, 40)]
    if timeout:
        game.phase_elapsed = game.PHASE_DURATION
    else:
        game.hp = 0
    game._check_win()
    assert game.state == "playing" and game.transition_remaining == 4
    assert not game.bullets
    stars = [item for item in game.items if isinstance(item, StarItem)]
    assert [(item.x, item.y) for item in stars] == [(50, 80), (240, 190)]
    assert all(item.auto_collect for item in stars)
    assert old_item in game.items
    assert game.boss == origin
    for _ in range(3):
        game._check_win()
    assert len(game.items) == 3
    advance(game, .6, shooting=True)
    middle = game.boss
    center = (game.WIDTH / 2, 86)
    assert 0 < hypot(middle[0] - center[0], middle[1] - center[1]) < hypot(origin[0] - center[0], origin[1] - center[1])
    assert not game.bullets and not game.boss_vulnerable
    game.pause()
    remaining = game.transition_remaining
    advance(game, 1)
    assert game.transition_remaining == remaining and game.boss == middle
    game.start_or_pause()
    advance(game, 1)
    assert game.boss == center
    assert not any(isinstance(item, StarItem) for item in game.items)
    assert game.score == 200
    advance(game, remaining - 1 - .001)
    assert game.state == "playing" and not game.bullets
    advance(game, .002)
    if phase == 2:
        assert game.state == "won"
        assert game.score == 7700
        advance(game, 1)
        assert game.score == 7700
    else:
        assert game.state == "playing" and game.phase == phase + 1
        assert game.hp == game.MAX_HP and not game.bullets
        assert game.phase_elapsed == 0
        advance(game, .3)
        assert game.bullets
        assert not game.boss_vulnerable  # Next bar is still filling.


def test_lethal_bomb_converts_bullets_instead_of_losing_the_star_rewards():
    game = DanmakuGame()
    game.start_or_pause()
    game.hp_fill_remaining = 0
    game.wave = 1
    game.hp = 24
    game.bullets = [Bullet(50, 80, 0, 0), Bullet(250, 80, 0, 0)]
    game.bomb()
    assert game.transition_remaining == 4 and not game.bullets
    assert len(game.items) == 2
    assert all(isinstance(item, StarItem) for item in game.items)


def test_converted_star_burst_combines_score_popup_without_losing_points():
    game = DanmakuGame()
    game.start_or_pause()
    game.items = [StarItem(game.x + offset, game.y - 60, auto_collect=True)
                  for offset in (-40, 0, 40)]
    advance(game, .2)
    assert not game.items and game.score == 300
    assert len(game.score_popups) == 1
    assert game.score_popups[0].value == 300
    y = game.score_popups[0].y
    game.update(.1)
    assert game.score_popups[0].y == pytest.approx(y - 3.2)


@pytest.mark.parametrize("power, top, active", [(127, True, False), (128, False, False), (128, True, True)])
def test_max_power_top_collection_targets_every_item_type_and_latches(power, top, active):
    game = DanmakuGame()
    game.start_or_pause()
    game.power = power
    game._spawn_in = 100
    game.x, game.y = 30, game.COLLECTION_LINE if top else game.COLLECTION_LINE + 1
    point = PointItem(330, 350)
    power_item = PowerItem(300, 330)
    star = StarItem(280, 310)
    game.items = [point, power_item, star]
    game.update(.1)
    assert game.magnet_active == active
    assert all(item.auto_collect == active for item in game.items)
    if active:
        assert point.x < 330 and point.y < 350
        game.y = 380
        game.update(.1)
        assert not game.magnet_active
        assert all(item.auto_collect for item in game.items)
        advance(game, 1)
        assert not game.items
        assert game.score == 1100 and game.point_items == 1
        assert game.power == game.MAX_POWER
    else:
        assert point.x == 330 and point.y == pytest.approx(356.5)


def test_magnet_freezes_on_pause_and_death_cancels_ordinary_item_attraction():
    game = DanmakuGame()
    game.start_or_pause()
    game._spawn_in = 100
    game.x, game.y = 35, 100
    item = PointItem(330, 350)
    game.items = [item]
    game.update(.1)
    assert item.auto_collect
    game.pause()
    position = item.x, item.y
    advance(game, .5)
    assert (item.x, item.y) == position
    game.start_or_pause()
    game.bombs = 0
    game.bullets = [Bullet(game.x, game.y, 0, 0)]
    game.update(1 / 120)
    assert game.dying and not game.magnet_active
    assert not item.auto_collect
    position = item.x, item.y
    game.update(.1)
    assert item.x == position[0]
    assert item.y == pytest.approx(position[1] + 6.5)


def test_warmup_has_enough_available_power_for_max_and_does_not_finish_early():
    game = DanmakuGame()
    game.start_warmup()
    assert game.WARMUP_WAVES * 2 * game.WARMUP_POWER >= game.MAX_POWER
    game.warmup_elapsed = 16
    assert not game.warmup_complete
    game.warmup_elapsed = 36
    assert not game.warmup_complete  # No early end while still below 90.
    game.power = 96
    assert game.warmup_complete
