"""Anonymous lyric searches through Chinese music services."""
import base64
import binascii
from html import unescape
from decimal import Decimal, DecimalException, ROUND_HALF_UP
import math
import re
from threading import Event
import unicodedata
from urllib.parse import quote

import requests

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL
from core.lrclib import _retry_after
from core.native_lyrics import decode_krc, parse_word_lyrics, timestamp_ms, to_lrcx


PROVIDERS = ("NetEase Cloud Music", "QQ Music", "KuGou", "Kuwo")
_TIMESTAMP = re.compile(r"\[\d+:\d+(?:\.\d+)?\]")
_METADATA_TAG = re.compile(
    r"\[(ti|ar|al|by|offset|length|au|re|ve|id|hash|sign|qq|total|language|encoding):[^\]]*\]",
    re.IGNORECASE,
)
_CREDIT = re.compile(
    r"^(?:作[词詞]|作曲|[词詞]曲?|曲|[编編]曲|填[词詞]|演唱|原唱|歌唱?|"
    r"制作人|製作人|制作|製作|[录錄]音|混音|母[带帶]|和声|和聲|"
    r"lyrics?(?:\s+by)?|music(?:\s+by)?|written\s+by|compos(?:er|ed\s+by)|"
    r"lyricist|arrang(?:er|ed\s+by)|produc(?:er|ed\s+by)|vocals?)\s*[:：]\s*\S",
    re.IGNORECASE,
)


class LyricsProviderError(RuntimeError):
    def __init__(self, message, retry_after=0, *, status_code=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


def _request(source, url, cancel, *, params=None, data=None, method=None, check_status=True):
    if cancel.is_set():
        return {}
    headers = {"User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})"}
    if source == "NetEase Cloud Music":
        headers.update({"Referer": "https://music.163.com/", "Cookie": "appver=2.0.2"})
    elif source == "QQ Music":
        headers["Referer"] = "https://y.qq.com/portal/player.html"
    elif source == "Kuwo":
        headers["Referer"] = "https://www.kuwo.cn/"
    if method is None:
        method = "post" if source == "NetEase Cloud Music" else "get"
    request = requests.post if method == "post" else requests.get
    options = {"params": params, "headers": headers, "timeout": 15}
    if data is not None:
        options["data"] = data
    try:
        response = request(url, **options)
    except requests.RequestException:
        raise LyricsProviderError(f"Could not reach {source}. Check your connection and try again.") from None
    try:
        if cancel.is_set():
            return {}
        retry_after = _retry_after(response.headers.get("Retry-After")) or 0
        if response.status_code != 200:
            if response.status_code == 429:
                retry_after = max(60, retry_after)
            raise LyricsProviderError(
                f"{source} refused the request (HTTP {response.status_code}). Please try again later.", retry_after,
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            raise LyricsProviderError(f"{source} returned an invalid response.") from None
        if not isinstance(payload, dict):
            raise LyricsProviderError(f"{source} returned an invalid response.")
        field, expected_code = ("status", 200) if source in {"KuGou", "Kuwo"} else ("code", 200 if source == "NetEase Cloud Music" else 0)
        code = payload.get(field)
        if check_status and code != expected_code:
            if code is None:
                raise LyricsProviderError(f"{source} returned an invalid response.")
            raise LyricsProviderError(
                f"{source} could not complete the request (code {code}). Please try again later.",
                max(retry_after, 60 if code == 429 else 0), status_code=code,
            )
        return payload
    finally:
        response.close()


def _text(value):
    return value if isinstance(value, str) else ""


def _heading_name(value):
    value = unicodedata.normalize("NFKC", _text(value)).casefold()
    # Provider headers often append translated titles or alternate artist names.
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", value)
    value = re.sub(r"\s+[-\u2013\u2014]\s+", " - ", value)
    return " ".join(value.split())


def _clean_rows(rows, record, metadata_text):
    """Filter plain lyric bodies while retaining their original timing data."""
    titles = {_heading_name(record.get("trackName"))} - {""}
    artists = {_heading_name(record.get("artistName"))} - {""}
    # Header tags are useful for recognizing the timed Title - Artist intro,
    # even when the search result's names differ from their translated aliases.
    for line in metadata_text.splitlines():
        body = _TIMESTAMP.sub("", line).strip()
        for tag in _METADATA_TAG.finditer(body):
            name = tag.group(1).casefold()
            value = _heading_name(tag.group(0).split(":", 1)[1][:-1])
            if value and name in {"ti", "ar"}:
                (titles if name == "ti" else artists).add(value)
    intros = {f"{title} - {artist}" for title in titles for artist in artists}
    intros.update(f"{artist} - {title}" for title in titles for artist in artists)
    kept = []
    lyrics_started = False
    for body, original in rows:
        body = body.strip()
        if body and not _METADATA_TAG.sub("", body).strip():
            continue
        if _CREDIT.match(body):
            continue
        if not lyrics_started and _heading_name(body) in intros:
            continue
        kept.append(original)
        if body:
            lyrics_started = True
    return kept


def clean_lyrics(text, record):
    """Remove provider headers and credits without discarding lyrics at 00:00."""
    lines = text.lstrip("\ufeff").splitlines()
    return "\n".join(_clean_rows([(_TIMESTAMP.sub("", line), line) for line in lines], record, text))


def _duration(value, scale=1):
    try:
        result = float(value) / scale
        return result if math.isfinite(result) and result > 0 else 0
    except (TypeError, ValueError):
        return 0


def _search_netease(query, cancel):
    payload = _request("NetEase Cloud Music", "https://music.163.com/api/search/get", cancel,
                       data={"s": query, "type": 1, "offset": 0, "limit": 30})
    result = payload.get("result", {})
    if not isinstance(result, dict):
        raise LyricsProviderError("NetEase Cloud Music returned invalid search results.")
    songs = result.get("songs", [])
    if not isinstance(songs, list):
        raise LyricsProviderError("NetEase Cloud Music returned invalid search results.")
    records = []
    for song in songs:
        if not isinstance(song, dict) or type(song.get("id")) is not int or song["id"] <= 0:
            raise LyricsProviderError("NetEase Cloud Music returned invalid search results.")
        artists = song.get("artists", [])
        album = song.get("album", {})
        records.append({
            "id": song["id"], "trackName": _text(song.get("name")),
            "artistName": ", ".join(_text(artist.get("name")) for artist in artists if isinstance(artist, dict))
                if isinstance(artists, list) else "",
            "albumName": _text(album.get("name")) if isinstance(album, dict) else "",
            "source": "NetEase Cloud Music", "hasSyncedLyrics": None,
            "duration": _duration(song.get("duration"), 1000),
            "sourceUrl": f"https://music.163.com/#/song?id={song['id']}",
        })
    return records


def _search_qqmusic(query, cancel):
    payload = _request("QQ Music", "https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg", cancel,
                       params={"key": query, "inCharset": "utf-8", "outCharset": "utf-8", "format": "json"})
    data = payload.get("data", {})
    if not isinstance(data, dict) or not isinstance(data.get("song", {}), dict):
        raise LyricsProviderError("QQ Music returned invalid search results.")
    songs = data.get("song", {}).get("itemlist", [])
    if not isinstance(songs, list):
        raise LyricsProviderError("QQ Music returned invalid search results.")
    records = []
    for song in songs:
        if not isinstance(song, dict) or not isinstance(song.get("mid"), str) or not re.fullmatch(r"[A-Za-z0-9]+", song["mid"]):
            raise LyricsProviderError("QQ Music returned invalid search results.")
        records.append({
            "id": song["mid"], "trackName": unescape(_text(song.get("name"))),
            "artistName": unescape(_text(song.get("singer"))), "albumName": "",
            "source": "QQ Music", "hasSyncedLyrics": None,
            "sourceUrl": f"https://y.qq.com/n/ryqq/songDetail/{song['mid']}",
        })
    return records


def _search_kugou(query, cancel):
    payload = _request("KuGou", "https://lyrics.kugou.com/search", cancel, params={
        "ver": 1, "man": "yes", "client": "pc", "keyword": query, "duration": 0, "hash": "",
    })
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise LyricsProviderError("KuGou returned invalid search results.")
    records = []
    for item in candidates:
        if not isinstance(item, dict) or not str(item.get("id", "")).isdigit() or not isinstance(item.get("accesskey"), str) or not item["accesskey"]:
            raise LyricsProviderError("KuGou returned invalid search results.")
        records.append({
            "id": str(item["id"]), "accessKey": item["accesskey"],
            "trackName": _text(item.get("song")), "artistName": _text(item.get("singer")),
            "albumName": "", "duration": _duration(item.get("duration"), 1000),
            "source": "KuGou", "hasSyncedLyrics": None,
            "sourceUrl": f"https://www.kugou.com/yy/html/search.html#searchType=song&searchKey={quote(query)}",
        })
    return records


def _search_kuwo(query, cancel):
    payload = _request("Kuwo", "https://www.kuwo.cn/search/searchMusicBykeyWord", cancel, check_status=False, params={
        "vipver": 1, "client": "kt", "ft": "music", "cluster": 0, "strategy": 2012,
        "encoding": "utf8", "rformat": "json", "mobi": 1, "issubtitle": 1,
        "pn": 0, "rn": 30, "all": query,
    })
    if cancel.is_set():
        return []
    songs = payload.get("abslist")
    if songs is None and str(payload.get("TOTAL")) == "0":
        return []
    if not isinstance(songs, list):
        raise LyricsProviderError("Kuwo returned invalid search results.")
    records = []
    for song in songs:
        if not isinstance(song, dict):
            raise LyricsProviderError("Kuwo returned invalid search results.")
        song_id = str(song.get("DC_TARGETID") or song.get("MUSICRID") or "").removeprefix("MUSIC_")
        if not song_id.isdigit():
            raise LyricsProviderError("Kuwo returned invalid search results.")
        records.append({
            "id": song_id, "trackName": unescape(_text(song.get("NAME") or song.get("SONGNAME"))),
            "artistName": unescape(_text(song.get("ARTIST"))), "albumName": unescape(_text(song.get("ALBUM"))),
            "duration": _duration(song.get("DURATION")), "source": "Kuwo", "hasSyncedLyrics": None,
            "sourceUrl": f"https://www.kuwo.cn/play_detail/{song_id}",
        })
    return records


def search(source, query, cancel=None):
    cancel = cancel if cancel is not None else Event()
    if cancel.is_set():
        return []
    # The player's default query uses Artist - Title; both services expect keywords.
    query = re.sub(r"\s+[-\u2013\u2014]\s+", " ", query.strip(), count=1)
    if not query:
        return []
    if source == "NetEase Cloud Music":
        return _search_netease(query, cancel)
    if source == "QQ Music":
        return _search_qqmusic(query, cancel)
    if source == "KuGou":
        return _search_kugou(query, cancel)
    if source == "Kuwo":
        return _search_kuwo(query, cancel)
    raise LyricsProviderError("Unknown lyrics source.")


def _word_result(record, text, kind):
    try:
        lines = parse_word_lyrics(text, kind)
        lines = _clean_rows([(line.text, line) for line in lines], record, text)
    except ValueError:
        raise LyricsProviderError(f"{record['source']} returned invalid word timing.") from None
    if not lines or not any(line.text.strip() for line in lines):
        raise LyricsProviderError(f"{record['source']} has no lyrics available for this track.")
    if any(line.word_timed for line in lines):
        return {**record, "syncedLyrics": to_lrcx(lines), "hasSyncedLyrics": True,
                "lyricsFormat": "lrcx", "nativeFormat": kind.upper(), "timing": "word"}
    return _line_result(record, "\n".join(f"[{timestamp_ms(line.start)}]{line.text}" for line in lines))


def _line_result(record, text):
    text = clean_lyrics(text, record)
    if not text.strip():
        raise LyricsProviderError(f"{record['source']} has no lyrics available for this track.")
    synced = bool(_TIMESTAMP.search(text))
    return {**record, "syncedLyrics" if synced else "plainLyrics": text, "hasSyncedLyrics": synced,
            "lyricsFormat": "lrc" if synced else "txt", "timing": "line" if synced else "plain"}


def _kuwo_lrc(payload):
    data = payload.get("data", {})
    rows = data.get("lrclist", []) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise LyricsProviderError("Kuwo returned invalid lyrics.")
    output = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("lineLyric"), str):
            raise LyricsProviderError("Kuwo returned invalid lyrics.")
        try:
            milliseconds = Decimal(str(row["time"])) * 1000
            if not milliseconds.is_finite() or not 0 <= milliseconds <= 10_000_000_000:
                raise ValueError()
            milliseconds = int(milliseconds.to_integral_value(rounding=ROUND_HALF_UP))
        except (DecimalException, ValueError, KeyError, OverflowError):
            raise LyricsProviderError("Kuwo returned invalid lyric timestamps.") from None
        if row["lineLyric"].strip() and row["lineLyric"].strip() != "//":
            output.append((milliseconds, unescape(row["lineLyric"])))
    # Stable ordering preserves original/translated rows sharing a timestamp.
    return "\n".join(f"[{timestamp_ms(start)}]{text}" for start, text in sorted(output, key=lambda item: item[0]))


def fetch_lyrics(record, cancel=None):
    cancel = cancel if cancel is not None else Event()
    result = dict(record)
    if cancel.is_set():
        return result
    source = record.get("source")
    if source == "NetEase Cloud Music":
        try:
            payload = _request(source, "https://music.163.com/api/song/lyric/v1", cancel, method="get", params={
                "id": record["id"], "lv": -1, "tv": -1, "rv": -1, "yv": -1,
            })
        except LyricsProviderError as error:
            if error.status_code not in {404, 405}:
                raise
            payload = {}
        if cancel.is_set():
            return result
        yrc = payload.get("yrc", {})
        if isinstance(yrc, dict) and isinstance(yrc.get("lyric"), str) and yrc["lyric"].strip() and not payload.get("nolyric") and not payload.get("uncollected"):
            try:
                word_result = _word_result(record, yrc["lyric"], "yrc")
                if word_result["lyricsFormat"] == "lrcx":
                    return word_result
            except LyricsProviderError:
                pass  # Keep usable line lyrics when YRC is absent or malformed.
        if "lrc" not in payload and not payload.get("nolyric") and not payload.get("uncollected"):
            payload = _request(source, "https://music.163.com/api/song/lyric", cancel, params={
                "id": record["id"], "tv": -1, "kv": -1, "lv": -1, "os": "pc",
            })
        lrc = payload.get("lrc", {})
        if not isinstance(lrc, dict):
            raise LyricsProviderError(f"{source} returned invalid lyrics.")
        text = _text(lrc.get("lyric"))
        if payload.get("nolyric") or payload.get("uncollected"):
            text = ""
    elif source == "QQ Music":
        payload = _request(source, "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg", cancel, params={
            "songmid": record["id"], "g_tk": 5381, "format": "json", "inCharset": "utf-8", "outCharset": "utf-8",
        })
        encoded = _text(payload.get("lyric"))
        try:
            text = unescape(base64.b64decode("".join(encoded.split()), validate=True).decode("utf-8-sig"))
        except (binascii.Error, ValueError, UnicodeError):
            raise LyricsProviderError("QQ Music returned invalid lyrics.") from None
    elif source == "KuGou":
        payload = _request(source, "https://lyrics.kugou.com/download", cancel, params={
            "ver": 1, "client": "pc", "id": record["id"], "accesskey": record["accessKey"], "fmt": "krc", "charset": "utf8",
        })
        if cancel.is_set():
            return result
        try:
            text = decode_krc(payload.get("content"))
        except ValueError:
            raise LyricsProviderError("KuGou returned an invalid or oversized KRC file.") from None
        return _word_result(record, text, "krc")
    elif source == "Kuwo":
        payload = _request(source, "https://m.kuwo.cn/newh5/singles/songinfoandlrc", cancel, params={"musicId": record["id"]})
        if cancel.is_set():
            return result
        text = _kuwo_lrc(payload)
    else:
        raise LyricsProviderError("Unknown lyrics source.")
    if cancel.is_set():
        return result
    return _line_result(record, text)
