"""Persistent, independent histories for search fields."""

from core.settings import load_all_settings, save_all_settings


HISTORY_LIMITS = {"lyrics": 15, "library": 20, "ytdlp": 50}


def clean_history(values, limit):
    result = []
    seen = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value and value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
        if len(result) >= limit:
            break
    return result


def load_search_history(category):
    return clean_history(_load_entries(category), HISTORY_LIMITS[category])


def _load_entries(category):
    histories = load_all_settings().get("search_history", {})
    return histories.get(category, []) if isinstance(histories, dict) else []


def save_search_history(category, values):
    _save_entries(category, clean_history(values, HISTORY_LIMITS[category]))


def _save_entries(category, values):
    payload = load_all_settings()
    histories = payload.get("search_history", {})
    histories = dict(histories) if isinstance(histories, dict) else {}
    histories[category] = values
    payload["search_history"] = histories
    save_all_settings(payload)


def split_lyrics_query(query):
    artist, separator, title = query.strip().partition(" - ")
    if separator and artist.strip() and title.strip():
        return title.strip(), artist.strip()
    return query.strip(), ""


def clean_lyrics_history(values):
    entries = []
    seen = set()
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str):
            title, artist = split_lyrics_query(value)
        elif (isinstance(value, (list, tuple)) and len(value) == 2
              and all(isinstance(part, str) for part in value)):
            title, artist = (part.strip() for part in value)
        else:
            continue
        key = title.casefold(), artist.casefold()
        if title and key not in seen:
            entries.append((title, artist))
            seen.add(key)
        if len(entries) == HISTORY_LIMITS["lyrics"]:
            break
    return entries


def load_lyrics_search_history():
    return clean_lyrics_history(_load_entries("lyrics"))


def save_lyrics_search_history(values):
    _save_entries("lyrics", clean_lyrics_history(values))
