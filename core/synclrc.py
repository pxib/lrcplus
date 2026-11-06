"""SyncLRC searches and conversion of enhanced LRC to LRCX."""
import re
from threading import Event

from core.http_client import requests

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL
from core.lrclib import _retry_after
from core.native_lyrics import timestamp_ms
from lyrics.formatting import format_emphasis


BASE_URL = "https://api.synclrc.dev"
STAMP = re.compile(r"([\[<])(\d{1,3}):([0-5]\d)(?:\.(\d{1,3}))?([\]>])")
LINE = re.compile(r"^\s*((?:\[\d{1,3}:[0-5]\d(?:\.\d{1,3})?\]\s*)+)(.*)$")
WORD = re.compile(r"<\d{1,3}:[0-5]\d(?:\.\d{1,3})?>")
OFFSET = re.compile(r"(?mi)^\s*\[offset:([+-]?\d+)\]\s*$")


class SyncLRCError(RuntimeError):
    def __init__(self, message, retry_after=0, *, status_code=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


def _request(path, params, cancel):
    if cancel.is_set():
        return {}
    try:
        response = requests.get(
            BASE_URL + path, params=params,
            headers={"User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})"},
            timeout=(6, 15),
        )
    except requests.RequestException as error:
        raise SyncLRCError("Could not reach SyncLRC. Check your connection and try again.") from error
    try:
        if cancel.is_set():
            return {}
        if response.status_code != 200:
            delay = _retry_after(response.headers.get("Retry-After")) or 0
            if response.status_code == 429:
                delay = max(60, delay)
            message = ("SyncLRC has no lyrics available for this recording." if response.status_code == 404
                       else f"SyncLRC request failed (HTTP {response.status_code}). Please try again later.")
            raise SyncLRCError(message, delay, status_code=response.status_code)
        try:
            payload = response.json()
        except ValueError as error:
            raise SyncLRCError("SyncLRC returned invalid JSON.") from error
        if not isinstance(payload, dict):
            raise SyncLRCError("SyncLRC returned an unexpected response.")
        return payload
    finally:
        response.close()


def _text(value):
    return value if isinstance(value, str) else ""


def _timed(value, karaoke=False):
    """Expand repeated timestamps, bake in LRC offsets, and escape LRCX styles."""
    value = _text(value)
    offset = OFFSET.search(value)
    offset = int(offset[1]) if offset else 0
    rows = []
    has_words = False

    def milliseconds(match):
        return (int(match[2]) * 60 + int(match[3])) * 1000 + int((match[4] or "0").ljust(3, "0"))

    for raw in value.splitlines():
        line = LINE.match(raw)
        if not line:
            continue
        stamps, body = line.groups()
        words = list(WORD.finditer(body)) if karaoke else []
        has_words |= bool(words)
        if karaoke:
            parts, cursor = [], 0
            for word in words:
                parts.append(format_emphasis(body[cursor:word.start()], []))
                stamp = STAMP.fullmatch(word[0])
                parts.append(f"<{timestamp_ms(max(0, milliseconds(stamp) - offset))}>")
                cursor = word.end()
            parts.append(format_emphasis(body[cursor:], []))
            body = "".join(parts)
        for stamp in STAMP.finditer(stamps):
            ms = max(0, milliseconds(stamp) - offset)
            rows.append((ms, f"[{timestamp_ms(ms)}]{body}"))
    rows.sort(key=lambda row: row[0])
    return "\n".join(row[1] for row in rows), has_words


def _lyrics(document):
    fields = document.get("lyrics")
    fields = fields if isinstance(fields, dict) else document
    karaoke, has_words = _timed(fields.get("karaoke"), karaoke=True)
    if not has_words:
        karaoke, _ = _timed(fields.get("karaoke"))
    synced, _ = _timed(fields.get("synced"))
    plain = _text(fields.get("plain"))
    return {
        "syncedLyrics": karaoke if has_words else synced or karaoke,
        "plainLyrics": plain,
        "lyricsFormat": "lrcx" if has_words else "lrc",
    }


def search(query, cancel=None):
    cancel = cancel if cancel is not None else Event()
    # The app's default query uses Artist - Title; the API expects keywords.
    query = re.sub(r"\s+[-\u2013\u2014]\s+", " ", query).strip()
    payload = _request("/search", {"q": query, "limit": 10}, cancel)
    if cancel.is_set():
        return []
    results = payload.get("results")
    if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
        raise SyncLRCError("SyncLRC returned invalid search results.")
    records = []
    for item in results:
        canonical_id = _text(item.get("id"))
        if item.get("instrumental") is True or not re.fullmatch(r"[0-9a-fA-F]{32}", canonical_id):
            continue
        record = {
            "source": "SyncLRC", "id": canonical_id,
            "trackName": _text(item.get("track")), "artistName": _text(item.get("artist")),
            "albumName": _text(item.get("album")), "duration": item.get("duration"),
            "sourceUrl": "https://synclrc.dev/",
            **_lyrics(item),
        }
        # An explicit empty lyrics object means unavailable, rather than a
        # reason to request every empty search hit again.
        if isinstance(item.get("lyrics"), dict) and not (record["syncedLyrics"] or record["plainLyrics"].strip()):
            continue
        records.append(record)
    return records


def fetch_lyrics(record, cancel=None):
    cancel = cancel if cancel is not None else Event()
    canonical_id = _text(record.get("id"))
    if not re.fullmatch(r"[0-9a-fA-F]{32}", canonical_id):
        raise SyncLRCError("Invalid SyncLRC recording ID.")
    payload = _request(f"/lyrics/{canonical_id}", {}, cancel)
    if cancel.is_set():
        return {}
    if payload.get("id") != canonical_id:
        raise SyncLRCError("SyncLRC returned a different recording.")
    lyrics = _lyrics(payload)
    if payload.get("instrumental") is True or not (lyrics["syncedLyrics"] or lyrics["plainLyrics"].strip()):
        raise SyncLRCError("SyncLRC has no lyrics available for this recording.", status_code=404)
    return lyrics
