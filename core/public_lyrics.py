"""Read-only lyrics search and import adapters."""

import json
import re
from urllib.parse import quote, urlsplit

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL
from core.chinese_lyrics import LyricsProviderError
from core.http_client import requests
from core.lrclib import _retry_after
from core.remote_lyric_formats import json_timing_payload, qrc_payload, timed_payload
from core.synclrc import normalize_lrc
from lyrics.plain_ruby import encode_plain_ruby
from lyrics.ttml import parse_ttml


LRC_RED = "lrc.red"
UNISON = "Unison"
LYRICS_OVH = "lyrics.ovh"
AMLL = "AMLL TTML DB"
BETTER = "Better Lyrics"
LIRIQO = "LiriQo"
VOCADB = "VocaDB"
LYRICA = "Lyrica"
FETCH_LYRICS = "Fetch-Lyrics"
BASE_URLS = {
    LRC_RED: "https://lrc.red",
    UNISON: "https://unison.betterlyrics.org",
    LYRICS_OVH: "https://api.lyrics.ovh",
    AMLL: "https://api.amll.dev",
    BETTER: "https://api.betterlyrics.org",
    LIRIQO: "https://api.liriqo-alfarrizi.my.id/v1",
    VOCADB: "https://vocadb.net/api",
}
SERVER_DEFAULTS = {LYRICA: "https://test-0k.onrender.com", FETCH_LYRICS: "http://127.0.0.1:8400"}
PROVIDERS = (*BASE_URLS, *SERVER_DEFAULTS)
_RESULT_LIMIT = 10
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_ISRC = re.compile(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", re.ASCII)
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def valid_contact_email(value):
    return (value.isascii() and value.isprintable()
            and re.fullmatch(r"[^\s<>()@]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value) is not None)


def server_url(value):
    """Accept an explicit HTTP(S) base URL without embedded credentials."""
    value = value.strip().rstrip("/")
    if not value.isprintable() or any(char.isspace() or char in "\\?#" for char in value):
        return ""
    try:
        address = urlsplit(value)
        port = address.port
    except ValueError:
        return ""
    if (address.scheme in {"https", "http"} and address.hostname and (port is None or port > 0)
            and address.username is None and not address.query and not address.fragment):
        return value
    return ""


def _request(source, path, cancel, *, params=None, contact="", as_json=True, base_url=None, json_type=dict):
    if cancel.is_set():
        return None
    identity = f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})"
    if source == LRC_RED:
        if not valid_contact_email(contact):
            raise LyricsProviderError("Enter a contact email for lrc.red.")
        identity = f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL}; {contact})"
    try:
        with requests.get(
            (base_url or BASE_URLS[source]) + path, params=params, headers={"User-Agent": identity},
            timeout=(6, 15), stream=True, allow_redirects=False,
        ) as response:
            if cancel.is_set() or response.status_code == 404:
                return None
            if response.status_code != 200:
                delay = _retry_after(response.headers.get("Retry-After")) or 0
                if response.status_code == 429:
                    delay = max(delay, 60)
                raise LyricsProviderError(
                    f"{source} request failed (HTTP {response.status_code}).", delay,
                    status_code=response.status_code,
                )
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if cancel.is_set():
                    return None
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise LyricsProviderError(f"{source} returned too much data.")
    except requests.RequestException:
        raise LyricsProviderError(f"Could not reach {source}. Check your connection and try again.") from None
    try:
        payload = json.loads(body) if as_json else body.decode("utf-8-sig")
    except (ValueError, RecursionError):
        raise LyricsProviderError(f"{source} returned an invalid response.") from None
    if as_json and not isinstance(payload, json_type):
        raise LyricsProviderError(f"{source} returned an unexpected response.")
    return payload


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _object(value):
    return value if isinstance(value, dict) else {}


def _objects(value):
    return (row for row in value if isinstance(row, dict)) if isinstance(value, list) else ()


def _text_list(value):
    return list(filter(None, map(_text, value))) if isinstance(value, list) else []


def _rows(source, payload, field):
    if payload is None:
        return []
    rows = payload.get(field)
    if not isinstance(rows, list) or (source == UNISON and payload.get("success") is not True):
        raise LyricsProviderError(f"{source} returned invalid search results.")
    return (row for row in rows if isinstance(row, dict))


def _record(source, title, artist, album=None, duration=None, **extra):
    return {
        "source": source, "trackName": _text(title), "artistName": _text(artist),
        "albumName": _text(album), "duration": duration, **extra,
    }


def _search_lrc_red(title, artist, cancel, contact):
    def lookup(query):
        payload = _request(LRC_RED, "/search.json", cancel, params={"q": query}, contact=contact)
        records = []
        for row in _rows(LRC_RED, payload, "hits"):
            isrc = _text(row.get("isrc")).upper()
            if _ISRC.fullmatch(isrc) and _text(row.get("title")):
                records.append(_record(
                    LRC_RED, row["title"], row.get("artist"), row.get("album"), row.get("duration"),
                    id=isrc, sourceUrl=f"{BASE_URLS[LRC_RED]}/s/{isrc}",
                ))
        return records

    records = lookup(f"{artist} {title}" if artist else title)
    if not records and artist and not cancel.is_set():
        records = sorted(lookup(title), key=lambda row: row["artistName"].casefold() != artist.casefold())
    return records[:_RESULT_LIMIT]


def _search_unison(title, artist, cancel, _contact):
    payload = _request(UNISON, "/lyrics/search", cancel, params={
        "q": f"{title} {artist}".strip(), "limit": _RESULT_LIMIT,
    })
    records = []
    for row in _rows(UNISON, payload, "data"):
        entry_id = row.get("id")
        video_id = _text(row.get("videoId"))
        if type(entry_id) is int and entry_id > 0 and _VIDEO_ID.fullmatch(video_id):
            records.append(_record(
                UNISON, row.get("song"), row.get("artist"), row.get("album"), row.get("duration"),
                id=entry_id, videoId=video_id, sourceUrl=f"{BASE_URLS[UNISON]}/song/{video_id}",
            ))
    return records[:_RESULT_LIMIT]


def _search_lyrics_ovh(title, artist, cancel, _contact):
    if artist:
        # Artist and title are sufficient for the lyric endpoint. Suggestions
        # are useful when the artist is missing, and otherwise add a request.
        return [_record(LYRICS_OVH, title, artist, sourceUrl="https://lyrics.ovh/")]
    payload = _request(LYRICS_OVH, f"/suggest/{quote(title, safe='')}", cancel)
    records = []
    seen = set()
    for row in _rows(LYRICS_OVH, payload, "data"):
        artist_data = row.get("artist")
        album = row.get("album")
        artist_name = _text(artist_data.get("name")) if isinstance(artist_data, dict) else ""
        track_name = _text(row.get("title"))
        key = (artist_name.casefold(), track_name.casefold())
        if artist_name and track_name and key not in seen:
            seen.add(key)
            records.append(_record(
                LYRICS_OVH, track_name, artist_name, album.get("title") if isinstance(album, dict) else None,
                row.get("duration"), sourceUrl="https://lyrics.ovh/",
            ))
    return records[:_RESULT_LIMIT]


def _convert_lyrics(contents, kind):
    if kind == "ttml":
        lines = parse_ttml(contents)
        if not lines:
            return {}
        word_timed = any(line.has_karaoke for line in lines)
        if word_timed or any(line.start or line.end is not None for line in lines):
            return timed_payload(lines)
        contents, kind = "\n".join(line.text for line in lines), "plain"
    if kind == "lrc":
        timed, word_timed = normalize_lrc(contents, karaoke=True)
        return {"syncedLyrics": timed, "lyricsFormat": "lrcx" if word_timed else "lrc"} if timed else {}
    if kind == "plain" and _text(contents):
        return {"plainLyrics": _text(contents)}
    return {}


def _fetch_lrc_red(record, cancel, contact):
    isrc = _text(record.get("id"))
    if not _ISRC.fullmatch(isrc):
        raise LyricsProviderError("Invalid lrc.red recording ID.")
    track = f"/s/{isrc}"
    contents = _request(LRC_RED, track + ".ttml", cancel, contact=contact, as_json=False)
    if contents:
        try:
            lyrics = _convert_lyrics(contents, "ttml")
        except ValueError:
            lyrics = {}
        if lyrics:
            return lyrics
    contents = _request(LRC_RED, track + ".lrc", cancel, contact=contact, as_json=False)
    return _convert_lyrics(contents, "lrc") if contents else {}


def _fetch_unison(record, cancel, _contact):
    entry_id = record.get("id")
    if type(entry_id) is not int or entry_id <= 0:
        raise LyricsProviderError("Invalid Unison lyrics ID.")
    payload = _request(UNISON, f"/lyrics/{entry_id}", cancel)
    if payload is None:
        return {}
    entry = payload.get("data")
    if (payload.get("success") is not True or not isinstance(entry, dict)
            or entry.get("id") != entry_id or entry.get("videoId") != record["videoId"]):
        raise LyricsProviderError("Unison returned an unexpected lyric entry.")
    contents = _text(entry.get("lyrics"))
    lyrics = _convert_lyrics(contents, entry.get("format")) if contents else {}
    submitter = entry.get("submitter")
    if lyrics and isinstance(submitter, dict):
        name = _text(submitter.get("displayName"))
        key = _text(submitter.get("keyId"))
        if name and key:
            lyrics.update(submitterName=name, submitterUrl=f"{BASE_URLS[UNISON]}/curator/{quote(key, safe='')}")
    return lyrics


def _fetch_lyrics_ovh(record, cancel, _contact):
    artist = quote(record["artistName"], safe="")
    title = quote(record["trackName"], safe="")
    payload = _request(LYRICS_OVH, f"/v1/{artist}/{title}", cancel)
    return _convert_lyrics(payload.get("lyrics"), "plain") if payload else {}


def _search_amll(title, artist, cancel, _contact):
    payload = _request(AMLL, "/v1/lyrics/search", cancel, params={
        "musicName": title, "artistName": artist, "pageSize": _RESULT_LIMIT,
    })
    records = []
    for row in _rows(AMLL, _object(payload.get("data")) if payload else None, "items"):
        track_id = row.get("id")
        titles = _text_list(row.get("musicNames"))
        if type(track_id) is not int or track_id <= 0 or not titles:
            continue
        albums = _text_list(row.get("albumNames"))
        authors = _text_list(row.get("authorUsernames"))
        contributor = authors[0] if authors and re.fullmatch(r"[A-Za-z0-9-]+", authors[0]) else ""
        records.append(_record(
            AMLL, titles[0], ", ".join(_text_list(row.get("artistNames"))), next(iter(albums), ""),
            row.get("duration"), id=track_id, sourceUrl="https://github.com/amll-dev/amll-ttml-db",
            variant=contributor, submitterName=contributor,
            submitterUrl=f"https://github.com/{contributor}" if contributor else "",
        ))
    return records


def _fetch_amll(record, cancel, _contact):
    payload = _request(AMLL, "/v1/lyrics/get", cancel, params={"id": record["id"]})
    entry = _object(payload.get("data")) if payload else {}
    if entry and entry.get("id") != record["id"]:
        raise LyricsProviderError("AMLL returned a different lyric entry.")
    ttml = _text(entry.get("lyrics"))
    return _convert_lyrics(ttml, "ttml") if ttml else {}


def _fetch_better(record, cancel, _contact):
    params = {"s": record["trackName"], "a": record["artistName"], "al": record["albumName"],
              "d": record["duration"] or None}
    try:
        response = _request(BETTER, "/getLyrics", cancel, params=params)
    except LyricsProviderError as error:
        if error.status_code != 401:
            raise
        # The shared TTML cache allows anonymous reads. QQ and KuGou also
        # support anonymous uncached lookups through their own endpoints.
        response = None
    if response and _text(response.get("ttml")):
        try:
            lyrics = _convert_lyrics(response["ttml"], "ttml")
        except ValueError:
            lyrics = {}
        if lyrics:
            return lyrics
    response = _request(BETTER, "/qq/getLyrics", cancel, params=params)
    if response and _text(response.get("lyrics")):
        lyrics = qrc_payload(response["lyrics"])
        if lyrics:
            return {**lyrics, "providerDetail": "QQ Music"}
    response = _request(BETTER, "/kugou/getLyrics", cancel, params=params)
    lyrics = _convert_lyrics(response.get("lyrics"), "lrc") if response else {}
    return {**lyrics, "providerDetail": "KuGou"} if lyrics else {}


def _search_liriqo(title, artist, cancel, album, duration):
    params = {"fast": 1, "title": title, "artist": artist, "album": album or None, "duration": duration or None}
    if not artist:
        params = {"fast": 1, "q": title}
    payload = _request(LIRIQO, "/lyrics", cancel, params=params)
    if not payload:
        return []
    if payload.get("error"):
        # The service can wrap a lookup miss in an HTTP 200 error response.
        if payload.get("tracks") == [] and not payload.get("primary"):
            return []
        raise LyricsProviderError("LiriQo could not complete the lyrics lookup.")
    metadata = _object(payload.get("metadata"))
    records = []
    seen = set()
    primary = _object(payload.get("primary"))
    for row in (primary, *_objects(payload.get("tracks"))):
        name = _text(row.get("source")) or _text(row.get("provider"))
        provider_id = _text(row.get("provider")) or name
        if not provider_id or provider_id in seen:
            continue
        # Unison attribution requires the contributor and their linked profile.
        # The direct Unison adapter already supplies these for its entries.
        if provider_id == "unison_community":
            continue
        lyrics = {}
        if _text(row.get("ttml")):
            try:
                lyrics = _convert_lyrics(row["ttml"], "ttml")
            except ValueError:
                pass
        if not lyrics:
            lyrics = json_timing_payload(row.get("timed"), row.get("words"))
        if not lyrics:
            lyrics = _convert_lyrics(row.get("lrc"), "lrc") or _convert_lyrics(row.get("plain"), "plain")
        if lyrics:
            seen.add(provider_id)
            records.append(_record(
                LIRIQO, row.get("title") or metadata.get("title") or title,
                row.get("artist") or metadata.get("artist") or artist,
                row.get("album") or metadata.get("album"), metadata.get("duration"),
                sourceUrl="https://github.com/AlFarrizi-Studio/Liriqo", variant=name,
                providerDetail=_text(row.get("lyricsSource")) or name, **lyrics,
            ))
    return records[:_RESULT_LIMIT]


def _search_vocadb(title, artist, cancel, _contact):
    payload = _request(VOCADB, "/songs", cancel, params={
        "query": title, "nameMatchMode": "Auto", "sort": "FavoritedTimes", "maxResults": 20, "fields": "Lyrics",
    })
    matches = list(_rows(VOCADB, payload, "items"))
    if artist:
        matches.sort(key=lambda row: artist.casefold() not in _text(row.get("artistString")).casefold())
    records = []
    for song in matches:
        song_id = song.get("id")
        if type(song_id) is not int or song_id <= 0:
            continue
        for entry in _objects(song.get("lyrics")):
            lyrics = _convert_lyrics(entry.get("value"), "plain")
            if not lyrics:
                continue
            variant = _text(entry.get("translationType")) or "Lyrics"
            languages = ", ".join(_text_list(entry.get("cultureCodes")))
            lyric_id = entry.get("id")
            source_url = f"https://vocadb.net/S/{song_id}/lyrics"
            if type(lyric_id) is int and lyric_id > 0:
                source_url += f"?lyricsId={lyric_id}"
            records.append(_record(
                VOCADB, song.get("name") or song.get("defaultName"), song.get("artistString"),
                duration=song.get("lengthSeconds"), sourceUrl=source_url,
                variant=f"{variant} · {languages}" if languages else variant,
                providerDetail=_text(entry.get("source")), **lyrics,
            ))
    return records[:_RESULT_LIMIT]


def _search_lyrica(title, artist, cancel, server):
    credit = "https://github.com/Wilooper/Lyrica"
    if artist:
        return [_record(LYRICA, title, artist, server=server, sourceUrl=credit)]
    try:
        payload = _request(LYRICA, "/suggestion", cancel, params={"q": title, "limit": _RESULT_LIMIT}, base_url=server)
    except LyricsProviderError as error:
        if error.status_code not in {500, 502, 503, 504}:
            raise
        raise LyricsProviderError(
            "Lyrica suggestions are unavailable. Enter the artist to look up the song directly.",
            error.retry_after, status_code=error.status_code,
        ) from None
    if payload is None and not cancel.is_set():
        raise LyricsProviderError("This Lyrica server has no suggestion endpoint. Enter the artist to continue.")
    return [
        _record(LYRICA, item.get("title"), item.get("artist"), server=server, sourceUrl=credit)
        for item in _rows(LYRICA, payload, "results") if _text(item.get("title")) and _text(item.get("artist"))
    ][:_RESULT_LIMIT]


def _fetch_lyrica(record, cancel, _contact):
    payload = _request(LYRICA, "/lyrics/", cancel, base_url=record["server"], params={
        "song": record["trackName"], "artist": record["artistName"], "timestamps": "true", "word": "true",
    })
    entry = _object(payload.get("data")) if payload else {}
    lyrics = json_timing_payload(entry.get("timed_lyrics"))
    if not lyrics:
        text = entry.get("lyrics")
        lyrics = _convert_lyrics(text, "lrc") or _convert_lyrics(text, "plain")
    return {**lyrics, "providerDetail": _text(entry.get("source"))} if lyrics else {}


def _fetch_lyrics_link(value):
    link = _text(value)
    try:
        url = urlsplit(link)
    except ValueError:
        return ""
    if url.scheme == "https" and url.netloc in {"utaten.com", "www.utaten.com", "uta-net.com", "www.uta-net.com"}:
        return link
    return ""


def _search_fetch_lyrics(title, artist, cancel, server):
    payload = _request(FETCH_LYRICS, "/api/fetchSongs", cancel, base_url=server, json_type=list, params={
        "title": title, "artist": artist, "lyricist": "", "composer": "", "opening": "", "contains": "",
    })
    if payload is None and not cancel.is_set():
        raise LyricsProviderError("Fetch-Lyrics did not find the search endpoint at this server URL (HTTP 404).")
    records = []
    for row in _objects(payload):
        info = _object(row.get("songInfo"))
        link = _fetch_lyrics_link(info.get("url"))
        if not link:
            continue
        names = [name for group in _objects(info.get("artists")) if group.get("type") == "artist"
                 for name in _text_list(group.get("artists"))]
        records.append(_record(
            FETCH_LYRICS, info.get("title") or title, ", ".join(names), server=server, sourceUrl=link,
            variant=_text(row.get("siteName")),
        ))
    return records[:_RESULT_LIMIT]


def _fetch_from_japanese_site(record, cancel, _contact):
    payload = _request(FETCH_LYRICS, "/api/fetchLyrics", cancel, base_url=record["server"],
                       params={"link": record["sourceUrl"]})
    lyrics = _convert_lyrics(payload.get("lyrics"), "plain") if payload else {}
    if lyrics:
        markup = _text(payload.get("rubyLyrics")) or _text(payload.get("lyricsRuby"))
        if markup:
            lyrics["plainDocument"] = encode_plain_ruby(lyrics["plainLyrics"], markup)
    return lyrics


_SEARCH = {
    LRC_RED: _search_lrc_red, UNISON: _search_unison, LYRICS_OVH: _search_lyrics_ovh,
    AMLL: _search_amll, VOCADB: _search_vocadb, LYRICA: _search_lyrica, FETCH_LYRICS: _search_fetch_lyrics,
}
_FETCH = {
    LRC_RED: _fetch_lrc_red, UNISON: _fetch_unison, LYRICS_OVH: _fetch_lyrics_ovh,
    AMLL: _fetch_amll, BETTER: _fetch_better, LYRICA: _fetch_lyrica, FETCH_LYRICS: _fetch_from_japanese_site,
}
FETCH_ON_PREVIEW = tuple(_FETCH)


def search(source, title, artist, cancel, *, contact="", server="", album="", duration=0):
    if cancel.is_set():
        return []
    if source == BETTER:
        return [_record(BETTER, title, artist, album, duration, sourceUrl="https://betterlyrics.org/")]
    if source == LIRIQO:
        records = _search_liriqo(title, artist, cancel, album, duration)
    elif source in SERVER_DEFAULTS:
        address = server_url(server or SERVER_DEFAULTS[source])
        if not address:
            raise LyricsProviderError(f"Enter a valid {source} server URL.")
        records = _SEARCH[source](title, artist, cancel, address)
    else:
        records = _SEARCH[source](title, artist, cancel, contact)
    return [] if cancel.is_set() else records


def fetch_lyrics(record, cancel, *, contact=""):
    source = record["source"]
    try:
        lyrics = _FETCH[source](record, cancel, contact)
    except ValueError:
        raise LyricsProviderError(f"{source} returned invalid lyrics.") from None
    if cancel.is_set():
        return {}
    if not lyrics:
        raise LyricsProviderError(f"{source} has no lyrics available for this recording.", status_code=404)
    return lyrics
