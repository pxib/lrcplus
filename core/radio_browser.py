"""Search the public Radio Browser station directory."""

from dataclasses import dataclass

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL
from core.http_client import requests
from core.network_stream import parse_stream_url


MIRRORS = ("https://de1.api.radio-browser.info", "https://de2.api.radio-browser.info")
PAGE_SIZE = 60


@dataclass(frozen=True)
class Station:
    name: str
    url: str
    country: str = ""
    tags: str = ""
    codec: str = ""
    bitrate: int = 0


@dataclass(frozen=True)
class StationPage:
    stations: tuple[Station, ...]
    has_next: bool


def _text(row, key):
    value = row.get(key)
    return " ".join(value.split())[:240] if isinstance(value, str) else ""


def _station(row):
    if not isinstance(row, dict):
        return None
    for key in ("url_resolved", "url"):
        url = row.get(key)
        if not isinstance(url, str):
            continue
        try:
            parsed = parse_stream_url(url)
        except ValueError:
            continue
        bitrate = row.get("bitrate")
        tags = ", ".join(tag.strip() for tag in _text(row, "tags").split(",") if tag.strip())
        return Station(_text(row, "name") or parsed.host(), url.strip(), _text(row, "country"),
                       tags, _text(row, "codec"), bitrate if type(bitrate) is int and bitrate > 0 else 0)
    return None


def search_stations(name="", country="", tag="", offset=0):
    params = {"order": "clickcount", "reverse": "true", "hidebroken": "true",
              "limit": PAGE_SIZE + 1, "offset": offset}
    for key, text in (("name", name), ("country", country), ("tag", tag)):
        if text:
            params[key] = text
    headers = {"User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})"}
    for mirror in MIRRORS:
        try:
            with requests.get(f"{mirror}/json/stations/search", params=params, headers=headers,
                              timeout=(3, 5)) as response:
                response.raise_for_status()
                rows = response.json()
        except (requests.RequestException, ValueError):
            continue
        if isinstance(rows, list):
            stations = tuple(station for row in rows[:PAGE_SIZE] if (station := _station(row)) is not None)
            return StationPage(stations, len(rows) > PAGE_SIZE)

    raise RuntimeError("Could not reach the Radio Browser directory. Try searching again.")
