"""Bounded LRCLIB lookup for the importer's isolated Python worker."""
import json
import math
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if __package__:
    from .artwork import normalized, search_terms, USER_AGENT
    from .provider_pacing import wait_for_provider
else:
    from artwork import normalized, search_terms, USER_AGENT
    from provider_pacing import wait_for_provider


MAX_RESPONSE_BYTES = 2_000_000
MAX_LYRIC_LINES = 5000


def _fetch(title, artist):
    wait_for_provider("lrclib")
    url = "https://lrclib.net/api/search?" + urlencode({"track_name": title, "artist_name": artist})
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("LRCLIB returned an oversized response.")
    data = json.loads(data)
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError("LRCLIB returned invalid search results.")
    return data


def _duration(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None


def find_lyrics(info):
    """Require matching title/artist and, when supplied, a close duration."""
    title, artist, album = search_terms(info)
    if not normalized(title) or not normalized(artist):
        return None
    duration = _duration(info.get("duration"))
    candidates = []
    for item in _fetch(title, artist):
        if (normalized(item.get("trackName") or "") != normalized(title)
                or normalized(item.get("artistName") or "") != normalized(artist)
                or item.get("instrumental")):
            continue
        length = _duration(item.get("duration"))
        if duration is not None and (length is None or abs(duration - length) > 3):
            continue
        for key, extension in (("syncedLyrics", ".lrc"), ("plainLyrics", ".txt")):
            text = item.get(key)
            if not isinstance(text, str) or not text.strip():
                continue
            # Multi-timestamp LRC lines expand into multiple player rows.
            rows = sum(max(1, len(re.findall(r"\[\d+:\d+(?:\.\d+)?\]", line))) for line in text.splitlines())
            if rows > MAX_LYRIC_LINES:
                continue
            if extension == ".lrc" and not re.search(r"(?m)^\[\d+:\d+(?:\.\d+)?\]", text):
                continue
            score = (extension != ".lrc", normalized(item.get("albumName") or "") != normalized(album),
                     abs(duration - length) if duration is not None else 0)
            candidates.append((score, {"text": text.strip() + "\n", "extension": extension}))
            break
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None
