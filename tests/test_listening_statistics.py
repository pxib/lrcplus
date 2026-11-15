"""Accounting uses real elapsed time and observable playback progress."""

from datetime import datetime, timedelta, timezone

import pytest

from core.listening_statistics import ListeningStatistics


class Playback:
    def __init__(self):
        self.now = 0.0
        self.wall = datetime(2026, 11, 15, 12, tzinfo=timezone(timedelta(hours=7)))
        self.position = 0
        self.records = []
        self.snapshot = dict(playing=True, audible=True, rate=1, clock_id="native", duration_ms=120000)
        self.tracker = ListeningStatistics(self.records.extend, clock=lambda: self.now, wall_clock=lambda: self.wall)
        self.start()

    def start(self, name="one"):
        self.tracker.start_track({"path_key": name, "path": name + ".mp3", "title": name},
                                 position_ms=self.position, **self.snapshot)

    def advance(self, seconds, media_ms=None, **changes):
        self.now += seconds
        self.wall += timedelta(seconds=seconds)
        self.position += media_ms if media_ms is not None else round(seconds * self.snapshot["rate"] * 1000)
        self.snapshot.update(changes)
        self.tracker.sample(self.position, **self.snapshot)

    def totals(self):
        self.tracker.flush()
        return sum(row["listened_ms"] for row in self.records), sum(row["plays"] for row in self.records)


def test_pause_resume_qualifies_once_and_flushes_partial_listening():
    play = Playback()
    play.advance(20, playing=False)
    play.advance(100, media_ms=0)
    play.advance(0, media_ms=0, playing=True)
    play.advance(20)
    play.advance(20)
    assert play.totals() == (60000, 1)


def test_speed_changes_count_real_seconds_not_media_seconds():
    play = Playback()
    play.advance(10, rate=2)
    play.advance(10)
    play.advance(10, rate=0.5)
    play.advance(10)
    assert play.position == 55000
    assert play.totals() == (40000, 1)


def test_seeks_in_both_directions_do_not_add_time_or_reset_qualification():
    play = Playback()
    play.advance(15)
    play.position = 90000
    play.tracker.rebase(play.position, **play.snapshot)
    play.advance(15)
    play.position = 0
    play.tracker.rebase(play.position, **play.snapshot)
    play.advance(30)
    assert play.totals() == (60000, 1)


def test_unannounced_jumps_and_buffering_are_excluded():
    play = Playback()
    play.advance(1, media_ms=90000)
    play.advance(20, media_ms=0)
    play.advance(1)
    assert play.totals() == (1000, 0)


def test_system_sleep_and_wall_clock_edits_do_not_inflate_time():
    play = Playback()
    play.advance(3600, media_ms=1000)
    play.wall -= timedelta(days=2)
    play.advance(1)
    assert play.totals() == (2000, 0)


def test_muted_intervals_are_excluded_but_resume_keeps_attempt():
    play = Playback()
    play.advance(15, audible=False)
    play.advance(60)
    play.advance(0, media_ms=0, audible=True)
    play.advance(15)
    assert play.totals() == (30000, 1)


def test_same_song_repeats_are_new_attempts_and_short_skips_keep_time():
    play = Playback()
    play.advance(30)
    play.position = 0
    play.start()
    play.advance(30)
    play.position = 0
    play.start("two")
    play.advance(5)
    assert play.totals() == (65000, 2)
    assert play.records[-1]["path_key"] == "two"
    assert play.records[-1]["plays"] == 0


@pytest.mark.parametrize("duration,seconds,plays", [(10000, 5, 1), (10000, 4, 0), (0, 30, 1)])
def test_short_and_unknown_duration_thresholds(duration, seconds, plays):
    play = Playback()
    play.snapshot["duration_ms"] = duration
    play.position = 0
    play.start()
    play.advance(seconds)
    assert play.totals() == (seconds * 1000, plays)


def test_midnight_splits_time_and_qualifies_on_the_correct_day():
    play = Playback()
    play.wall = datetime(2026, 11, 15, 23, 59, 40, tzinfo=timezone(timedelta(hours=7)))
    play.start()
    play.advance(40)
    play.tracker.flush()
    assert [(row["day"], row["listened_ms"], row["plays"]) for row in play.records] == [
        ("2026-11-15", 20000, 0), ("2026-11-16", 20000, 1),
    ]
    assert play.records[-1]["last_played"].startswith("2026-11-15T17:00:20")


def test_disabling_and_clearing_do_not_replay_pending_time():
    play = Playback()
    play.advance(10)
    play.tracker.set_enabled(False, position_ms=play.position, **play.snapshot)
    play.advance(50)
    play.tracker.set_enabled(True, position_ms=play.position, **play.snapshot)
    play.advance(20)
    play.tracker.clear(position_ms=play.position, **play.snapshot)
    play.records.clear()
    play.advance(5)
    assert play.totals() == (5000, 0)


def test_a_play_qualifying_exactly_at_midnight_belongs_to_the_new_day():
    play = Playback()
    play.wall = datetime(2026, 11, 15, 23, 59, 30, tzinfo=timezone.utc)
    play.start()
    play.advance(30)
    play.tracker.flush()
    assert [(row["day"], row["listened_ms"], row["plays"]) for row in play.records] == [
        ("2026-11-15", 30000, 0), ("2026-11-16", 0, 1),
    ]


def test_switching_output_clock_never_counts_counter_jumps():
    play = Playback()
    play.advance(1)
    play.position = 0
    play.advance(1, media_ms=0, clock_id=("pcm", 1))
    play.advance(2)
    play.position = 0
    play.advance(1, media_ms=0, clock_id=("pcm", 2))
    play.advance(3)
    assert play.totals() == (6000, 0)
