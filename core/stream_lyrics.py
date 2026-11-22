"""Read ICY track titles and look up unambiguous plain lyrics."""

import re
import unicodedata

from core.app_info import APP_NAME, APP_VERSION
from core.http_client import requests
from core import lrclib


def song_from_title(value):
    parts = re.split(r"\s+[-–—]\s+", " ".join(value.split()), maxsplit=1)
    if len(parts) == 2 and all(parts) and max(map(len, parts)) <= 200:
        return tuple(parts)
    return None


def _read_exact(raw, size, cancelled, *, keep=True):
    data = bytearray()
    read = getattr(raw, "read1", raw.read)
    while size and not cancelled.is_set():
        block = read(min(size, 4096))
        if not block:
            break
        size -= len(block)
        if keep:
            data.extend(block)
    return bytes(data) if not size else None


def watch_icy_titles(url, cancelled, titles):
    """Read metadata on a separate connection without feeding the audio player."""
    if cancelled.is_set():
        return
    headers = {"Icy-MetaData": "1", "User-Agent": f"{APP_NAME}/{APP_VERSION}"}
    with requests.get(url, headers=headers, stream=True, timeout=(4, 4)) as response:
        response.raise_for_status()
        try:
            interval = int(response.headers.get("icy-metaint", 0))
        except (TypeError, ValueError):
            return
        if not 0 < interval <= 262144:
            return
        previous = None
        while not cancelled.is_set() and _read_exact(response.raw, interval, cancelled, keep=False) is not None:
            size = _read_exact(response.raw, 1, cancelled)
            if not size:
                return
            if size[0] == 0:
                continue
            data = _read_exact(response.raw, size[0] * 16, cancelled)
            if data is None:
                return
            match = re.search(rb"(?:^|;)StreamTitle='(.*?)';", data, re.IGNORECASE | re.DOTALL)
            if match:
                try:
                    title = match[1].decode("utf-8")
                except UnicodeDecodeError:
                    title = match[1].decode("cp1252", errors="replace")
                if title != previous:
                    titles.append(title)
                    previous = title


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) if isinstance(value, str) else ""


def find_stream_lyrics(song, cancelled):
    artist, title = song
    matches = {}
    for row in lrclib.search(title, cancelled, artist=artist):
        if (row.get("instrumental") or _normalized(row.get("artistName")) != _normalized(artist)
                or _normalized(row.get("trackName")) != _normalized(title)):
            continue
        text = row.get("plainLyrics")
        if isinstance(text, str) and 0 < len(text) <= 32000 and text.strip():
            lines = tuple(line.strip() for line in text.splitlines() if line.strip())
            matches.setdefault(tuple(_normalized(line) for line in lines), lines)
    return next(iter(matches.values())) if len(matches) == 1 else ()
