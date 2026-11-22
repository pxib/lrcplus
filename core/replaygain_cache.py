"""ReplayGain measurements indexed by local path and file revision."""

from contextlib import closing
from pathlib import Path
import sqlite3

from core.playlists import path_key
from core.settings import get_app_data_dir


def _database():
    return get_app_data_dir() / "replaygain.sqlite3"


def file_revision(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def read_scan(path):
    database = _database()
    try:
        if not database.is_file():
            return None
        with closing(sqlite3.connect(database, timeout=1)) as connection:
            return connection.execute(
                "SELECT track_gain, track_peak, album_gain, album_peak FROM scans "
                "WHERE path = ? AND size = ? AND modified = ?",
                (path_key(path), *file_revision(path)),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None


def save_scans(rows, cancelled):
    database = _database()
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=3)) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                path TEXT PRIMARY KEY, size INTEGER, modified INTEGER,
                track_gain REAL, track_peak REAL, album_gain REAL, album_peak REAL
            )
        """)
        connection.executemany("""
            INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size, modified = excluded.modified,
                track_gain = excluded.track_gain, track_peak = excluded.track_peak,
                album_gain = CASE WHEN scans.size = excluded.size AND scans.modified = excluded.modified
                    THEN coalesce(excluded.album_gain, scans.album_gain) ELSE excluded.album_gain END,
                album_peak = CASE WHEN scans.size = excluded.size AND scans.modified = excluded.modified
                    THEN coalesce(excluded.album_peak, scans.album_peak) ELSE excluded.album_peak END
        """, rows)
        if cancelled.is_set():
            connection.rollback()
            return False
    return True
