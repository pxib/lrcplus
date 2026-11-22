"""Read ICY track titles and look up unambiguous plain lyrics."""

import re
import unicodedata
from collections import Counter

from core.app_info import APP_NAME, APP_VERSION
from core.http_client import requests
from core import lrclib
from lyrics.parser import parse_lrc_lines


_INLINE_TIMESTAMP = re.compile(r"<\d+:\d+(?:\.\d+)?>")
_TRAILING_TITLE_TAG = re.compile(r"(.+?)\s+(?:\(([^()]+)\)|\[([^\[\]]+)\])")
_VERSION_SUFFIXES = {
    "acoustic", "cover", "demo", "edit", "instrumental", "karaoke",
    "live", "mix", "remaster", "remastered", "remix", "ver", "version",
}


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


def watch_icy_titles(url, cancelled, titles=None):
    """Publish ICY titles, or return the first artist/title pair for a one-off lookup."""
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
                    previous = title
                    if titles is None:
                        song = song_from_title(title)
                        if song:
                            return song
                    else:
                        titles.append(title)


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) if isinstance(value, str) else ""


def _lyric_content(text):
    # Releases can reflow the same words or change their punctuation. Keep
    # letters, marks, and symbols so distinct versions still require agreement.
    return "".join(char for char in _normalized(text)
                   if not char.isspace() and not unicodedata.category(char).startswith("P"))


def _plain_lines(record):
    text = record.get("plainLyrics")
    if isinstance(text, str) and 0 < len(text) <= 32000 and text.strip():
        return tuple(line.strip() for line in text.splitlines() if line.strip())
    synced = record.get("syncedLyrics")
    if not isinstance(synced, str) or not 0 < len(synced) <= 32000:
        return ()
    try:
        timed = parse_lrc_lines(synced.splitlines())
    except (ValueError, OverflowError):
        return ()
    return tuple(line for _, text in timed if (line := _INLINE_TIMESTAMP.sub("", text).strip()))


def _is_minor_omission(text, reference):
    if not 0 < len(reference) - len(text) <= len(reference) // 50:
        return False
    position = 0
    for char in reference:
        if char == text[position]:
            position += 1
            if position == len(text):
                return True
    return False


def _choose_lyrics(rows):
    matches = {}
    counts = Counter()
    for row in rows:
        lines = _plain_lines(row)
        content = _lyric_content(" ".join(lines))
        if content:
            matches.setdefault(content, lines)
            counts[content] += 1
    if not counts:
        return ()
    best, votes = counts.most_common(1)[0]
    # A clear majority can tolerate a tiny omission in another release's text.
    # Distinct lyrics or a much shorter edit still make the result ambiguous.
    if votes * 2 > counts.total() and all(
        content == best or _is_minor_omission(content, best) for content in counts
    ):
        return matches[best]
    return ()


def _album_query(title):
    match = _TRAILING_TITLE_TAG.fullmatch(unicodedata.normalize("NFKC", title).strip())
    if match:
        base, parenthetical, bracketed = match.groups()
        album = _normalized(parenthetical or bracketed)
        # Leave common recording labels attached to the track title.
        if album and not set(re.findall(r"\w+", album)) & (_VERSION_SUFFIXES | {"tv", "extended"}):
            return base, album
    return title, ""


def find_stream_lyrics(song, cancelled):
    artist, title = song
    artist_key, title_key = _normalized(artist), _normalized(title)
    query_title, album_key = _album_query(title)
    query_key = _normalized(query_title)
    exact = []
    from_album = []
    completions = {}
    for row in lrclib.search(query_title, cancelled, artist=artist):
        if row.get("instrumental") or _normalized(row.get("artistName")) != artist_key:
            continue
        candidate = _normalized(row.get("trackName"))
        if candidate == title_key:
            exact.append(row)
        elif album_key and candidate == query_key and _normalized(row.get("albumName")) == album_key:
            from_album.append(row)
        elif len(title_key.split()) >= 3:
            # Some stations omit the last word. Accept a unique completion
            # from the same artist without guessing among alternate versions.
            prefix, _, word = candidate.rpartition(" ")
            if prefix == title_key and word.isalnum() and word not in _VERSION_SUFFIXES:
                completions.setdefault(candidate, []).append(row)
    if exact:
        return _choose_lyrics(exact)
    if from_album:
        return _choose_lyrics(from_album)
    if len(completions) == 1:
        return _choose_lyrics(next(iter(completions.values())))
    return ()
