"""Bounded, serialized local statistics writes and dashboard queries."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
from threading import Lock

from PySide6.QtCore import QObject, Signal

from core.music_library import read_track
from core.settings import get_app_data_dir


class StatisticsStore(QObject):
    changed = Signal()
    failed = Signal(str)
    shuffle_history_ready = Signal(int, dict)
    MAX_PENDING = 4096

    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.path = Path(path) if path else get_app_data_dir() / "listening_statistics.sqlite3"
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="listening-statistics")
        self._lock = Lock()
        self._pending = OrderedDict()
        self._metadata = OrderedDict()
        self._generation = 0
        self._scheduled = None
        self._closed = False
        self._last_error = None

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @property
    def last_error(self):
        with self._lock:
            return self._last_error

    def _error(self, message):
        with self._lock:
            notify = not self._closed and message != self._last_error
            self._last_error = message
        if notify:
            self.failed.emit(message)

    def _changed(self):
        with self._lock:
            notify = not self._closed
            self._last_error = None
        if notify:
            self.changed.emit()

    def _merge(self, entries):
        overflow = False
        for entry in entries:
            key = (entry["path_key"], entry["day"])
            if key in self._pending:
                old = self._pending[key]
                entry = {**entry, "listened_ms": old["listened_ms"] + entry["listened_ms"],
                         "plays": old["plays"] + entry["plays"],
                         "last_played": max(old["last_played"], entry["last_played"])}
            self._pending[key] = dict(entry)
            if len(self._pending) > self.MAX_PENDING:
                self._pending.popitem(last=False)
                overflow = True
        return overflow

    def _schedule_locked(self):
        if self._pending and self._scheduled != self._generation:
            self._scheduled = self._generation
            self._executor.submit(self._drain, self._generation)

    def record(self, entries):
        with self._lock:
            if self._closed:
                return
            overflow = self._merge(entries)
            self._schedule_locked()
        if overflow:
            self._error("Statistics could not be saved; the oldest unsaved entries were dropped.")

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=1)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise sqlite3.DatabaseError("This statistics database needs a newer version of LRCPlus.")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tracks (
                    path_key TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL,
                    artist TEXT NOT NULL, album TEXT NOT NULL, album_artist TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily (
                    path_key TEXT NOT NULL, day TEXT NOT NULL, listened_ms INTEGER NOT NULL,
                    plays INTEGER NOT NULL, last_played TEXT NOT NULL,
                    PRIMARY KEY (path_key, day)
                );
                CREATE TABLE IF NOT EXISTS stations (
                    path_key TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS daily_date ON daily(day);
                PRAGMA user_version=2;
            """)
            return connection
        except Exception:
            connection.close()
            raise

    def _track_metadata(self, entry):
        key = entry["path_key"]
        if entry.get("metadata_complete"):
            return entry
        cached = self._metadata.get(key)
        if cached is None:
            try:
                cached = read_track(entry["path"]).to_dict()
            except (OSError, ValueError, TypeError):
                cached = {"title": entry.get("title") or Path(entry["path"]).stem}
            self._metadata[key] = cached
            if len(self._metadata) > 2048:
                self._metadata.popitem(last=False)
        else:
            self._metadata.move_to_end(key)
        return {**entry, **{name: cached.get(name, "") for name in ("title", "artist", "album", "album_artist")}}

    def _drain(self, generation):
        while True:
            with self._lock:
                if generation != self._generation:
                    return
                if not self._pending:
                    self._scheduled = None
                    return
                entries = list(self._pending.values())
                self._pending.clear()
            try:
                metadata = {entry["path_key"]: entry for entry in entries}
                metadata = {key: entry if entry.get("station") else self._track_metadata(entry)
                            for key, entry in metadata.items()}
                with closing(self._connect()) as connection, connection:
                    for key, track in metadata.items():
                        if track.get("station"):
                            connection.execute("""
                                INSERT INTO stations VALUES (?, ?, ?)
                                ON CONFLICT(path_key) DO UPDATE SET name=excluded.name,
                                    country=excluded.country
                            """, (key, track["title"], track.get("country") or ""))
                            continue
                        connection.execute("""
                            INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(path_key) DO UPDATE SET path=excluded.path,
                                title=excluded.title, artist=excluded.artist,
                                album=excluded.album, album_artist=excluded.album_artist
                        """, (key, track["path"], track.get("title") or Path(track["path"]).stem,
                              str(track.get("artist") or ""), str(track.get("album") or ""),
                              str(track.get("album_artist") or "")))
                    connection.executemany("""
                        INSERT INTO daily VALUES (:path_key, :day, :listened_ms, :plays, :last_played)
                        ON CONFLICT(path_key, day) DO UPDATE SET
                            listened_ms=daily.listened_ms+excluded.listened_ms,
                            plays=daily.plays+excluded.plays,
                            last_played=MAX(daily.last_played, excluded.last_played)
                    """, entries)
            except (sqlite3.Error, OSError) as error:
                with self._lock:
                    if generation == self._generation:
                        self._merge(entries)
                        self._scheduled = None
                self._error(f"Could not save listening statistics: {error}")
                return
            self._changed()

    def request_shuffle_history(self):
        """Read all recent tracks on the existing worker, never on the GUI thread."""
        with self._lock:
            if not self._closed:
                self._schedule_locked()
                self._executor.submit(self._shuffle_history, self._generation)

    def _shuffle_history(self, generation):
        history = {}
        try:
            with closing(self._connect()) as connection:
                # Older entries have full weight, so need not stay in memory.
                rows = connection.execute("""
                    SELECT d.path_key, MAX(d.last_played) FROM daily d JOIN tracks t USING(path_key)
                    WHERE d.day >= ? GROUP BY d.path_key
                """, ((date.today() - timedelta(days=15)).isoformat(),))
                history = dict(rows)
        except (sqlite3.Error, OSError) as error:
            self._error(f"Could not read shuffle history: {error}")
        with self._lock:
            notify = not self._closed
        if notify:
            self.shuffle_history_ready.emit(generation, history)

    def snapshot(self, days=None, today=None):
        today = today or date.today()
        with self._lock:
            if self._closed:
                raise RuntimeError("Statistics store is closed.")
            self._schedule_locked()
            return self._executor.submit(self._snapshot, days, today)

    def _snapshot(self, days, today):
        end = today.isoformat()
        start = (today - timedelta(days=days - 1)).isoformat() if days else "0001-01-01"
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            args = (start, end)
            where = "d.day BETWEEN ? AND ?"
            summary = connection.execute(f"""
                SELECT COALESCE(SUM(listened_ms),0) AS listened_ms,
                       COALESCE(SUM(plays),0) AS plays,
                       COUNT(DISTINCT CASE WHEN d.listened_ms>0 THEN t.path_key END) AS songs
                FROM daily d LEFT JOIN tracks t USING(path_key) WHERE {where}
            """, args).fetchone()
            songs = connection.execute(f"""
                SELECT t.path_key, t.path, t.title, t.artist, t.album,
                       SUM(d.listened_ms) AS listened_ms, SUM(d.plays) AS plays,
                       MAX(d.last_played) AS last_played
                FROM daily d JOIN tracks t USING(path_key) WHERE {where}
                GROUP BY t.path_key ORDER BY listened_ms DESC, plays DESC, t.title COLLATE NOCASE LIMIT 100
            """, args).fetchall()
            artists = connection.execute(f"""
                SELECT t.artist AS name, SUM(d.listened_ms) AS listened_ms, SUM(d.plays) AS plays
                FROM daily d JOIN tracks t USING(path_key) WHERE {where}
                GROUP BY t.artist ORDER BY listened_ms DESC, name COLLATE NOCASE LIMIT 100
            """, args).fetchall()
            albums = connection.execute(f"""
                SELECT t.album AS name, COALESCE(NULLIF(t.album_artist,''),t.artist) AS artist,
                       SUM(d.listened_ms) AS listened_ms, SUM(d.plays) AS plays
                FROM daily d JOIN tracks t USING(path_key) WHERE {where}
                GROUP BY t.album, COALESCE(NULLIF(t.album_artist,''),t.artist)
                ORDER BY listened_ms DESC, name COLLATE NOCASE LIMIT 100
            """, args).fetchall()
            stations = connection.execute(f"""
                SELECT s.path_key, s.name, s.country,
                       SUM(d.listened_ms) AS listened_ms, SUM(d.plays) AS plays,
                       MAX(d.last_played) AS last_played
                FROM daily d JOIN stations s USING(path_key) WHERE {where}
                GROUP BY s.path_key ORDER BY listened_ms DESC, plays DESC, s.name COLLATE NOCASE LIMIT 100
            """, args).fetchall()
            chart_start = max(start, (today - timedelta(days=364)).isoformat())
            daily = connection.execute(f"""
                SELECT d.day, SUM(d.listened_ms) AS listened_ms
                FROM daily d WHERE {where} GROUP BY d.day ORDER BY d.day DESC LIMIT 365
            """, (chart_start, end)).fetchall()
        return {"summary": dict(summary), "songs": [dict(row) for row in songs],
                "artists": [dict(row) for row in artists], "albums": [dict(row) for row in albums],
                "stations": [dict(row) for row in stations],
                "daily": [dict(row) for row in reversed(daily)]}

    def clear(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Statistics store is closed.")
            self._generation += 1
            self._pending.clear()
            self._scheduled = None
            return self._executor.submit(self._clear)

    def _clear(self):
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("DELETE FROM daily")
                connection.execute("DELETE FROM tracks")
                connection.execute("DELETE FROM stations")
            self._metadata.clear()
            self._changed()
        except (sqlite3.Error, OSError) as error:
            self._error(f"Could not clear listening statistics: {error}")
            raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._schedule_locked()
            barrier = self._executor.submit(lambda: None)
        try:
            barrier.result(timeout=2)
        except TimeoutError:
            pass  # A slow filesystem must not hold the player window open.
        self._executor.shutdown(wait=False)
