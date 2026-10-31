import pytest

from assets.scarlet.danmaku import Bullet, DanmakuGame, PointItem, StarItem


def combat_game():
    """Arrange a boss whose opening health fill and first volley are complete."""
    game = DanmakuGame()
    game.hp_fill_remaining = 0
    game.wave = 1
    return game


def advance(game, seconds, **controls):
    while seconds > 1e-8:
        step = min(.1, seconds)
        game.update(step, **controls)
        seconds -= step


def test_successive_deathbombs_shrink_window_and_death_resets_it():
    game = combat_game()
    game.start_or_pause()
    game._spawn_in = 100
    for index, window in enumerate((.1, .075, .05)):
        advance(game, game.bomb_cooldown)
        game.invulnerable = 0
        game.bullets = [Bullet(game.x, game.y, 0, 0)]
        game.update(1 / 120)
        assert game.deathbomb_remaining == pytest.approx(window)
        game.update(window - .001)
        game.bomb()
        assert game.lives == 3
        assert game.deathbomb_streak == index + 1
        assert not game.dying
    assert game.deathbomb_window == pytest.approx(.025)
    advance(game, game.bomb_cooldown)
    game.bombs = 1
    game.invulnerable = 0
    game.bullets = [Bullet(game.x, game.y, 0, 0)]
    game.update(1 / 120)
    game.update(.026)
    game.bomb()  # Too late for the now-stricter window.
    assert game.dying
    assert game.lives == 2
    assert game.bombs == 1
    assert game.deathbomb_streak == 0
    assert game.deathbomb_window == .1
    advance(game, .5)
    game.invulnerable = 0
    game.bullets = [Bullet(game.x, game.y, 0, 0)]
    game.update(1 / 120)
    assert game.deathbomb_remaining == pytest.approx(.1)


def test_ordinary_bomb_and_restart_handle_deathbomb_streak():
    game = combat_game()
    game.start_or_pause()
    game.deathbomb_streak = 2
    game.bomb()
    assert game.deathbomb_streak == 2
    game.deathbomb_streak = 20
    assert game.deathbomb_window == .025
    game.reset()
    assert game.deathbomb_streak == 0
    assert game.deathbomb_window == .1


@pytest.mark.parametrize("lives", [1, 3])
def test_death_animation_freezes_controls_until_respawn_or_game_over(lives):
    game = combat_game()
    game.start_or_pause()
    game._spawn_in = 100
    game.lives, game.bombs = lives, 0
    game.x, game.y = 60, 300
    hostile = Bullet(game.x, game.y, 0, 0)
    moving = Bullet(200, 200, 10, 20)
    game.bullets = [hostile, moving]
    game.update(1 / 120)
    assert game.dying and game.state == "playing"
    assert game.lives == lives - 1
    assert game.invulnerable == 0
    assert game.death_remaining == game.DEATH_DURATION
    game.items.append(PointItem(game.x, game.y, vy=0))
    game.bullets.append(Bullet(game.x + 18, game.y, 0, 0))
    game.bombs = 1
    advance(game, .2, dx=1, dy=-1, shooting=True)
    game.bomb()
    assert (game.x, game.y) == (60, 300)
    assert not game.shots
    assert game.point_items == game.graze == game.score == 0
    assert game.bombs == 1
    assert game.lives == lives - 1
    assert hostile in game.bullets and moving.x > 200 and moving.y > 200
    remaining = game.death_remaining
    game.pause()
    advance(game, 1)
    assert game.death_remaining == remaining
    game.start_or_pause()
    advance(game, remaining - .001)
    assert game.dying
    assert (game.x, game.y) == (60, 300)
    advance(game, .002)
    assert not game.dying
    if lives > 1:
        assert game.state == "playing"
        assert (game.x, game.y) == (game.WIDTH / 2, game.HEIGHT - 65)
        assert game.invulnerable > 2.4
    else:
        assert game.state == "lost"


@pytest.mark.parametrize("clear_by", ["damage", "timeout"])
def test_each_phase_has_full_independent_hp_and_timer(clear_by):
    game = combat_game()
    game.start_or_pause()
    for phase in range(3):
        assert game.phase == phase
        assert game.hp == game.MAX_HP
        assert game.phase_remaining == game.PHASE_DURATION
        game.bullets = [Bullet(50, 50, 0, 0)]
        game.shots = [Bullet(50, 50, 0, 0)]
        if clear_by == "damage":
            game.hp = 0
        else:
            game.phase_elapsed = game.PHASE_DURATION
        game._check_win()
        assert game.transition_remaining == game.PHASE_BREAK
        assert not game.bullets and not game.shots
        assert any(isinstance(item, StarItem) for item in game.items)
        if phase < 2:
            assert game.state == "playing"
            assert game.score == phase * 100  # Only earlier stars, no clear bonus yet.
        else:
            assert game.state == "playing"
        advance(game, game.PHASE_BREAK)
        if phase == 2:
            assert game.state == "won"
            assert game.score == 7800  # Three cancelled bullets plus clear bonus.
            game._check_win()
            assert game.score == 7800


def test_partial_damage_and_total_elapsed_do_not_skip_health_bars():
    game = combat_game()
    game.start_or_pause()
    game.hp = 120
    game.elapsed = 74
    game._check_win()
    assert game.phase == 0 and game.hp == 120
    assert game.state == "playing"
    game.hp = 0
    game._check_win()
    assert game.phase == 1 and game.hp == game.MAX_HP
    assert game.phase_remaining == 25


@pytest.mark.parametrize("phase", [0, 2])
def test_phase_completion_waits_for_death_animation(phase):
    game = combat_game()
    game.start_or_pause()
    game._phase = phase
    game.bombs = 0
    game.bullets = [Bullet(game.x, game.y, 0, 0)]
    game.update(1 / 120)
    game.hp = 0
    game._check_win()
    assert game.state == "playing" and game.phase == phase
    advance(game, .44)
    assert game.dying and game.phase == phase
    advance(game, .02)
    assert not game.dying
    assert game.transition_remaining > 0
    advance(game, game.transition_remaining)
    assert game.state == ("playing" if phase == 0 else "won")
    assert game.phase == min(phase + 1, 2)


def test_patterns_get_denser_and_faster_with_each_health_bar():
    game = combat_game()
    game.start_or_pause()
    counts, speeds = [], []
    for _ in range(3):
        game._spawn()
        counts.append(len(game.bullets))
        speeds.append(max((b.vx ** 2 + b.vy ** 2) ** .5 for b in game.bullets))
        game.hp = 0
        game._check_win()
        advance(game, game.PHASE_BREAK)
    assert counts == sorted(set(counts))
    assert speeds == sorted(set(speeds))
