import pytest

from assets.scarlet.danmaku import Bullet, DanmakuGame


def hit_player(*, lives=3, bombs=3):
    game = DanmakuGame()
    game.hp_fill_remaining = 0
    game.wave = 1
    game.start_or_pause()
    game._spawn_in = 100
    game.lives, game.bombs = lives, bombs
    game.x, game.y = 60, 300
    game.bullets = [Bullet(game.x, game.y, 0, 0), Bullet(200, 200, 0, 0)]
    game.update(1 / 120)
    return game


@pytest.mark.parametrize("delay", [0, .04, .099])
@pytest.mark.parametrize("lives", [1, 3])
def test_bomb_inside_window_preserves_life_position_and_point_inventory(delay, lives):
    game = hit_player(lives=lives)
    assert game.state == "playing"
    assert game.lives == lives
    assert not game.items
    game.update(delay)
    game.bomb()
    assert game.state == "playing"
    assert game.lives == lives
    assert game.bombs == 2
    assert (game.x, game.y) == (60, 300)
    assert not game.items
    assert not game.bullets
    assert game.hp == game.MAX_HP - 24
    assert game.invulnerable >= 2
    assert game.deathbomb_remaining == 0
    assert game.deathbomb_flash > 0


@pytest.mark.parametrize("lives", [1, 3])
def test_expiry_commits_one_death_and_preserves_danmaku(lives):
    game = hit_player(lives=lives)
    bullets = list(game.bullets)
    game.update(game.DEATHBOMB_WINDOW)
    assert game.lives == lives - 1
    assert game.state == "playing"
    assert game.dying
    assert game.bombs == 3
    assert len(game.items) == 10
    assert game.bullets == bullets
    assert (game.x, game.y) == (60, 300)
    assert game.deathbomb_remaining == 0
    game.bomb()
    assert game.lives == lives - 1  # A late normal bomb cannot refund a life.
    assert game.bombs == 3  # Bombing is disabled during the death animation.
    for _ in range(5):
        game.update(.1)
    assert not game.dying
    if lives == 1:
        assert game.state == "lost"
    else:
        assert (game.x, game.y) == (game.WIDTH / 2, game.HEIGHT - 65)
        assert game.invulnerable > 2.4


def test_repeated_collisions_do_not_extend_the_window():
    game = hit_player()
    game.update(.05)
    assert game.deathbomb_remaining == pytest.approx(.05)
    game.update(.05)
    assert game.lives == 2
    assert len(game.items) == 10
    assert game.deathbomb_remaining == 0


def test_pausing_freezes_the_window_and_disables_bombing_until_resume():
    game = hit_player(lives=1)
    game.update(.04)
    remaining = game.deathbomb_remaining
    game.pause()
    game.update(.1)
    game.bomb()
    assert game.deathbomb_remaining == remaining
    assert game.bombs == 3
    assert game.lives == 1
    game.start_or_pause()
    game.bomb()
    assert game.lives == 1
    assert game.bombs == 2
    assert game.deathbomb_remaining == 0


def test_no_bombs_means_no_rescue_window():
    game = hit_player(lives=1, bombs=0)
    assert game.state == "playing"
    assert game.dying
    assert game.lives == 0
    assert game.deathbomb_remaining == 0
    assert len(game.items) == 10


def test_restart_discards_a_pending_death():
    game = hit_player()
    game.reset()
    assert game.deathbomb_remaining == 0
    assert game.deathbomb_flash == 0
    game.start_or_pause()
    game.update(.1)
    assert game.lives == 3
    assert game.bombs == 3
    assert not game.items


@pytest.mark.parametrize("save", [False, True])
def test_boss_clear_waits_for_pending_last_life_hit_to_resolve(save):
    game = hit_player(lives=1)
    game._phase = 2
    game.hp = 1
    bx, by = game.boss
    game.shots = [Bullet(bx, by, 0, 0)]
    game.update(1 / 120)
    assert game.hp == 0
    assert game.state == "playing"
    if save:
        game.bomb()
        assert game.transition_remaining == game.PHASE_BREAK
        for _ in range(40):
            game.update(.1)
        assert game.state == "won"
        assert game.lives == 1
        score = game.score
        game.update(.1)
        assert game.score == score
    else:
        game.update(game.DEATHBOMB_WINDOW)
        assert game.dying
        assert game.state == "playing"
        for _ in range(5):
            game.update(.1)
        assert game.state == "lost"
        assert game.lives == 0


def test_window_expiry_is_independent_of_update_batching():
    batched, split = hit_player(), hit_player()
    batched.update(.1)
    for _ in range(10):
        split.update(.01)
    assert batched.lives == split.lives == 2
    assert batched.deathbomb_remaining == split.deathbomb_remaining == 0
    assert batched.state == split.state == "playing"
