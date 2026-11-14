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
    histories = load_all_settings().get("search_history", {})
    values = histories.get(category, []) if isinstance(histories, dict) else []
    return clean_history(values, HISTORY_LIMITS[category])


def save_search_history(category, values):
    payload = load_all_settings()
    histories = payload.get("search_history", {})
    histories = dict(histories) if isinstance(histories, dict) else {}
    histories[category] = clean_history(values, HISTORY_LIMITS[category])
    payload["search_history"] = histories
    save_all_settings(payload)
