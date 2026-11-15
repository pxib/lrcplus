"""SQLite persistence, date filters, failure recovery, and reset ordering."""

from datetime import date
from threading import Event
import sqlite3

import pytest

from core.statistics_store import StatisticsStore


def record(key="one", day="2026-11-15", milliseconds=60000, plays=1, **metadata):
    return {"path_key": key, "path": key + ".mp3", "title": key,
            "artist": "Artist", "album": "Album", "album_artist": "Artist",
            "metadata_complete": True, "day": day, "listened_ms": milliseconds,
            "plays": plays, "last_played": day + "T05:00:00.000+00:00", **metadata}


@pytest.fixture
def store(qapp, tmp_path):
    store = StatisticsStore(path=tmp_path / "statistics.sqlite3")
    yield store
    store.close()


def snapshot(store, days=None):
    return store.snapshot(days, date(2026, 11, 15)).result(timeout=5)


def test_batches_merge_and_persist_across_reopening(store):
    store.record([record()])
    store.record([record(milliseconds=5000, plays=0)])
    result = snapshot(store)
    assert result["summary"] == {"listened_ms": 65000, "plays": 1, "songs": 1}
    store.close()
    other = StatisticsStore(path=store.path)
    try:
        assert snapshot(other)["summary"] == result["summary"]
    finally:
        other.close()


def test_date_filters_artist_album_aggregation_and_distinct_files(store):
    store.record([record(), record(key="two", day="2026-11-14"),
                  record(key="three", day="2026-10-15", artist="Other", album_artist="Other")])
    assert snapshot(store, 1)["summary"]["songs"] == 1
    week = snapshot(store, 7)
    assert week["summary"]["songs"] == 2
    assert len(week["artists"]) == 1
    assert week["artists"][0]["listened_ms"] == 120000
    all_time = snapshot(store)
    assert all_time["summary"]["songs"] == 3
    assert len(all_time["albums"]) == 2  # Same album title, different artists.


def test_clear_discards_pending_old_generations_and_preserves_new_records(store):
    entered, release = Event(), Event()
    future = store._executor.submit(lambda: (entered.set(), release.wait(3)))
    assert entered.wait(1)
    store.record([record()])
    clearing = store.clear()
    store.record([record(key="new", milliseconds=5000, plays=0)])
    release.set()
    clearing.result(timeout=5)
    result = snapshot(store)
    assert [row["path_key"] for row in result["songs"]] == ["new"]
    assert result["summary"]["listened_ms"] == 5000


def test_database_failure_keeps_bounded_buffer_and_can_retry(store, monkeypatch):
    connect = store._connect
    monkeypatch.setattr(store, "_connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("Test disk unavailable")))
    store.record([record()])
    store._executor.submit(lambda: None).result(timeout=5)
    assert len(store._pending) == 1
    monkeypatch.setattr(store, "_connect", connect)
    assert snapshot(store)["summary"]["listened_ms"] == 60000


def test_pending_statistics_are_bounded_when_storage_stays_unavailable(store, monkeypatch):
    monkeypatch.setattr(store, "MAX_PENDING", 3)
    monkeypatch.setattr(store, "_connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("offline")))
    store.record([record(key=str(index)) for index in range(100)])
    store._executor.submit(lambda: None).result(timeout=5)
    assert len(store._pending) <= 3


def test_future_database_schema_is_not_downgraded(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(sqlite3.DatabaseError, match="newer version"):
        snapshot(store)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
