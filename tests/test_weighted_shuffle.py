"""Weighting, persistence, asynchronous history, and transport integration."""

from datetime import datetime, timedelta, timezone
import random

import pytest

from core.audio_controller import AudioController
from core.favorites import favorite_key
from core.playlists import path_key
from core.settings import get_playback_settings, save_playback_settings
from core.statistics_store import StatisticsStore
from core.weighted_shuffle import track_weight
from ui.main_window import Player
from ui.settings_dialog import SettingsDialog


NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def test_recent_songs_recover_and_favorites_get_a_bounded_boost():
    assert track_weight(now=NOW) == 1
    assert track_weight(favorite=True, now=NOW) == 2
    assert track_weight(last_played=NOW.isoformat(), now=NOW) == pytest.approx(.2)
    assert track_weight(last_played=(NOW - timedelta(days=7)).isoformat(), now=NOW) == pytest.approx(.6)
    assert track_weight(last_played=(NOW - timedelta(days=30)).isoformat(), now=NOW) == 1
    assert track_weight(last_played=(NOW + timedelta(days=1)).isoformat(), now=NOW) == pytest.approx(.2)
    assert track_weight(last_played="bad", now=NOW) == 1
    assert track_weight(favorite=True, last_played=NOW.isoformat(),
                        prefer_favorites=False, prefer_older=False, now=NOW) == 1


def test_weighted_choices_exclude_current_and_keep_every_other_song_eligible(tmp_path, monkeypatch):
    paths = [tmp_path / f"{i}.mp3" for i in range(4)]
    controller = AudioController()
    controller.playback_settings = {"shuffle_mode": "weighted"}
    controller.favorite_paths = {favorite_key(paths[1])}
    controller._weighted_shuffle_history = lambda: {path_key(paths[2]): datetime.now(timezone.utc).isoformat()}
    rng = random.Random(42)
    monkeypatch.setattr("core.audio_controller.random.choices", rng.choices)
    counts = [0] * 4
    for _ in range(2000):
        counts[controller._choose_shuffle_index(paths, 0)] += 1
    assert counts[0] == 0
    assert counts[1] > counts[3] > counts[2] > 0
    assert controller._choose_shuffle_index(paths[:1], 0) == 0
    # A preloaded gapless successor must be the one actually selected.
    controller._gapless_shuffle_choice = (tuple(map(str, paths)), 0, 2)
    assert controller._choose_shuffle_index(paths, 0) == 2
    assert controller._gapless_shuffle_choice is None


def test_history_query_includes_all_recent_tracks_and_handles_reset(qapp, tmp_path):
    store = StatisticsStore(path=tmp_path / "history.sqlite3")
    results = []
    store.shuffle_history_ready.connect(lambda generation, history: results.append((generation, history)))
    today = datetime.now(timezone.utc).date()
    try:
        entries = [dict(path_key=str(i), path=f"{i}.mp3", title=str(i),
                        artist="", album="", album_artist="", metadata_complete=True,
                        day=today.isoformat(), listened_ms=30000, plays=1,
                        last_played=f"{today}T00:00:00+00:00") for i in range(120)]
        old = dict(entries[0], path_key="old", day=(today - timedelta(days=40)).isoformat())
        store.record(entries + [old])
        store.request_shuffle_history()
        store._executor.submit(lambda: None).result(timeout=5)
        qapp.processEvents()
        assert len(results[-1][1]) == 120
        store.clear().result(timeout=5)
        store.request_shuffle_history()
        store._executor.submit(lambda: None).result(timeout=5)
        qapp.processEvents()
        assert results[-1] == (store.generation, {})
    finally:
        store.close()


def test_weighted_mode_settings_and_restart(qapp):
    save_playback_settings({"shuffle_mode": "weighted", "shuffle_enabled": True,
                            "shuffle_prefer_favorites": False, "shuffle_prefer_older": True})
    for _ in range(2):
        window = Player()
        try:
            assert window.shuffle_button.text() == "Shuffle: Weighted"
            dialog = SettingsDialog(window.lrc_search_paths, window.global_lyrics_settings,
                                    playback_settings=window.playback_settings, parent=window)
            try:
                assert dialog.shuffle_mode_combo.currentData() == "weighted"
                assert not dialog.shuffle_favorites_checkbox.isChecked()
                assert dialog.shuffle_favorites_checkbox.isEnabled()
                values = dialog.get_playback_settings()
                assert values["shuffle_mode"] == "weighted"
                assert not values["shuffle_prefer_favorites"]
                save_playback_settings(values)
                dialog.shuffle_mode_combo.setCurrentIndex(0)
                assert not dialog.shuffle_older_checkbox.isEnabled()
            finally:
                dialog.deleteLater()
            # With tracking disabled, even cached history is ignored.
            window._shuffle_last_played = {"one": NOW.isoformat()}
            window.statistics.enabled = False
            assert window._weighted_shuffle_history() == {}
        finally:
            window.close()
            window.deleteLater()
            qapp.processEvents()
    assert get_playback_settings()["shuffle_mode"] == "weighted"
