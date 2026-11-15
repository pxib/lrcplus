"""Exercise timer deadlines with a fake clock rather than real-time sleeps."""

import pytest

from core.settings import (
    get_sleep_timer_settings, load_all_settings, save_sleep_timer_settings,
)
from core.sleep_timer import SleepTimer


@pytest.fixture
def timer(qapp):
    now = [0.0]
    playing = [True]
    timer = SleepTimer(clock=lambda: now[0], is_playing=lambda: playing[0])
    pauses = []
    timer.pause_requested.connect(lambda: pauses.append(now[0]))
    yield timer, now, playing, pauses
    timer.cancel()
    timer.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("playing", [True, False])
def test_countdown_runs_while_paused_and_expires_only_once(timer, playing):
    model, now, state, pauses = timer
    state[0] = playing
    model.start(1)
    now[0] = 59.2
    model.check()
    assert model.remaining_seconds == 1
    now[0] = 60
    model.check()
    model.check()
    model.complete()
    assert pauses == [60]
    assert not model.active
    assert not model._timer.isActive()


def test_finish_song_waits_for_a_boundary_instead_of_pausing_at_deadline(timer):
    model, now, state, pauses = timer
    model.start(1, "finish_track")
    now[0] = 59
    model.check()
    now[0] = 60
    model.check()
    assert model.finishing
    assert not pauses
    assert not model._timer.isActive()
    model.complete()
    model.complete()
    assert pauses == [60]


def test_paused_expiry_does_not_wait_for_a_song_to_resume(timer):
    model, now, state, pauses = timer
    model.start(1, "finish_track")
    now[0] = 59
    model.check()
    state[0] = False
    now[0] = 60
    model.check()
    assert not model.active
    assert pauses == [60]


def test_returning_after_suspend_consumes_even_a_finish_song_timer(timer):
    model, now, state, pauses = timer
    model.start(1, "finish_track")
    now[0] = 600
    model.check()
    assert pauses == [600]
    assert not model.active


def test_cancel_replace_and_extend_do_not_leave_an_old_deadline(timer):
    model, now, state, pauses = timer
    model.start(1)
    now[0] = 20
    model.add_five_minutes()
    assert model.remaining_seconds == 340
    model.start(2)
    now[0] = 70
    model.check()
    assert not pauses
    assert model.remaining_seconds == 70
    model.cancel()
    now[0] = 1000
    model.check()
    assert not pauses
    model.finish_current_track()
    assert model.finishing
    model.add_five_minutes()
    assert model.finishing


def test_adding_time_cannot_revive_an_expired_timer(timer):
    model, now, state, pauses = timer
    model.start(1)
    now[0] = 61
    model.add_five_minutes()
    assert pauses == [61]
    assert not model.active


@pytest.mark.parametrize("minutes", [0, -1, 721, True, "30", float("inf")])
def test_invalid_durations_are_rejected(timer, minutes):
    with pytest.raises(ValueError):
        timer[0].start(minutes)
    assert not timer[0].active


def test_only_defaults_are_persisted_and_unrelated_settings_are_preserved(timer):
    from core.settings import save_all_settings

    save_all_settings({"volume": 42})
    save_sleep_timer_settings({"minutes": 47, "action": "finish_track", "deadline": 1234})
    assert get_sleep_timer_settings() == {"minutes": 47, "action": "finish_track"}
    assert load_all_settings() == {
        "volume": 42, "sleep_timer": {"minutes": 47, "action": "finish_track"},
    }
    assert not timer[0].active


@pytest.mark.parametrize("value", [None, [], {"minutes": -1}, {"minutes": True, "action": []}])
def test_invalid_saved_defaults_fall_back_safely(value):
    assert get_sleep_timer_settings({"sleep_timer": value}) == {"minutes": 30, "action": "pause"}
