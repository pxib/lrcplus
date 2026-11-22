"""Persist saved radio stations independently of playback history."""

from dataclasses import asdict
import json

from core.file_io import atomic_write_text
from core.radio_browser import station_from_entry
from core.settings import get_app_data_dir


def _path():
    return get_app_data_dir() / "radio_favorites.json"


def load_station_favorites():
    try:
        saved = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, list):
        return {}
    stations = {}
    for entry in saved:
        station = station_from_entry(entry)
        if station is not None:
            stations.setdefault(station.key, station)
    return stations


def save_station_favorites(stations):
    atomic_write_text(_path(), json.dumps([asdict(station) for station in stations], ensure_ascii=False))
