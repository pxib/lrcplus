"""Keep the most recent successfully tuned stations on this device."""

from dataclasses import asdict
from datetime import datetime
import json

from core.file_io import atomic_write_text
from core.radio_browser import station_from_entry
from core.settings import get_app_data_dir


LIMIT = 20


def _path():
    return get_app_data_dir() / "radio_history.json"


def load_station_history():
    try:
        saved = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(saved, list):
        return []
    history = []
    for entry in saved[:LIMIT]:
        station = station_from_entry(entry)
        if station is not None:
            try:
                played = datetime.fromisoformat(entry["played"])
            except (KeyError, TypeError, ValueError):
                continue
            history.append((station, played))
    return history


def remember_station(station):
    history = [(station, datetime.now().astimezone())]
    history.extend(entry for entry in load_station_history() if entry[0].key != station.key)
    _write_history(history[:LIMIT])


def clear_station_history():
    _write_history([])


def _write_history(history):
    atomic_write_text(_path(), json.dumps([
        {**asdict(station), "played": played.isoformat(timespec="seconds")}
        for station, played in history
    ], ensure_ascii=False))
