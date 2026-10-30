"""Conservative MusicBrainz matching and Cover Art Archive artwork."""
from __future__ import annotations

import base64
import json
import re
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID


USER_AGENT = "LRCPlus-ytdlp/1.2.0 (https://github.com/pxib/lrcplus)"
MAX_IMAGE_BYTES = 2_000_000
_last_request = 0.0


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKC", str(value)).casefold() if c.isalnum())


def search_terms(info):
    title = str(info.get("track") or info.get("title") or "").strip()
    artist = str(info.get("artist") or info.get("uploader") or "").strip()
    artist = re.sub(r"\s+-\s+Topic$", "", artist, flags=re.I)
    artists = info.get("artists")
    uploader = re.sub(r"\s+-\s+Topic$", "", str(info.get("uploader") or ""), flags=re.I).strip()
    if (isinstance(artists, list) and len(artists) > 1 and isinstance(artists[0], str)
            and normalized(uploader) == normalized(artists[0])):
        # Auto-generated music credits can include the composer as an artist.
        # The uploader corroborates the primary performer; title, album and
        # duration still have to match the MusicBrainz recording/release.
        artist = artists[0].strip()
    if not info.get("track"):
        # Strip presentation labels, while retaining live/remix/version names.
        title = re.sub(r"\s*[\[(](?:official\s+(?:music\s+)?(?:video|audio)|lyrics?|lyric video)[\])]\s*$",
                       "", title, flags=re.I).strip()
        parts = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
        if len(parts) == 2 and (not info.get("artist") or normalized(parts[0]) == normalized(artist)):
            artist, title = parts
    return title, artist, str(info.get("album") or "").strip()


def _fetch(url, *, image=False):
    global _last_request
    # One worker makes requests sequentially; include failed attempts in pacing.
    time.sleep(max(0, 1.1 - (time.monotonic() - _last_request)))
    _last_request = time.monotonic()
    headers = {"User-Agent": USER_AGENT, "Accept": "image/*" if image else "application/json"}
    with urlopen(Request(url, headers=headers), timeout=10) as response:
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Artwork service returned an oversized response.")
    if image:
        image_format(data)
        return data
    return json.loads(data)


def image_format(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    raise ValueError("Album cover is not a JPEG or PNG image.")


def _phrase(value):
    return '"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r'\\\1', value) + '"'


def find_cover(info):
    """Return cover details and bytes, or None when no reliable release exists."""
    title, artist, album = search_terms(info)
    if not normalized(title) or not normalized(artist):
        return None
    query = f"recording:{_phrase(title)} AND artist:{_phrase(artist)}"
    if album:
        query += " AND release:" + _phrase(album)
    data = _fetch("https://musicbrainz.org/ws/2/recording/?" + urlencode({
        "query": query, "fmt": "json", "limit": 10,
    }))
    releases = {}
    for recording in data.get("recordings", []):
        credits = recording.get("artist-credit") or []
        names = [str(credit.get("name") or (credit.get("artist") or {}).get("name") or "") for credit in credits]
        combined = "".join(name + str(credit.get("joinphrase") or "") for name, credit in zip(names, credits))
        if normalized(recording.get("title", "")) != normalized(title) or normalized(combined) != normalized(artist):
            continue
        duration, length = info.get("duration"), recording.get("length")
        if isinstance(duration, (int, float)) and isinstance(length, (int, float)):
            if abs(duration - length / 1000) > max(15, duration * 0.1):
                continue
        for release in recording.get("releases", []):
            if release.get("status") != "Official":
                continue
            if album and normalized(release.get("title", "")) != normalized(album):
                continue
            group = release.get("release-group") or {}
            if not album and group.get("secondary-types"):
                continue  # Avoid compilations and live/remix collections by default.
            try:
                release_id = str(UUID(release["id"]))
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
            releases[release_id] = release
    ordered = sorted(releases.items(), key=lambda item: (
        (item[1].get("release-group") or {}).get("primary-type") != "Album",
        item[1].get("date") or "9999", item[0],
    ))
    for release_id, release in ordered[:3]:
        try:
            image = _fetch(f"https://coverartarchive.org/release/{release_id}/front-500", image=True)
        except HTTPError as error:
            if error.code == 404:
                continue
            raise
        return {"release_id": release_id, "album": str(release.get("title") or ""),
                "data": base64.b64encode(image).decode("ascii")}
    return None


def embed_cover(path, cover):
    """Replace embedded artwork, leaving the audio and other tags intact."""
    data = base64.b64decode(cover["data"], validate=True)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Album cover is too large.")
    kind = image_format(data)
    if path.suffix.lower() == ".mp3":
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(mime="image/" + kind, type=3, desc="Album cover", data=data))
        tags.save(path)
    else:
        from mutagen.mp4 import MP4, MP4Cover

        audio = MP4(path)
        audio["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG if kind == "jpeg" else MP4Cover.FORMAT_PNG)]
        audio.save()
