"""Favorite tracks are preferences, independent of library scans and queues."""

import os

from core.settings import load_all_settings, save_all_settings


def favorite_key(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(os.fspath(path))))


def load_favorites():
    values = load_all_settings().get("favorite_tracks", [])
    if not isinstance(values, list):
        return set()
    return {favorite_key(path) for path in values if isinstance(path, str) and path.strip()}


def save_favorites(paths):
    keys = {favorite_key(path) for path in paths}
    payload = load_all_settings()
    payload["favorite_tracks"] = sorted(keys)
    save_all_settings(payload)
    return keys
