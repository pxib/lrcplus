import base64
from io import BytesIO
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QSignalSpy

from plugins.community.ytdlp_import import worker
from plugins.community.ytdlp_import.dialog import ImportDialog
from plugins.community.ytdlp_import.job import ImportJob
from plugins.community.ytdlp_import.plugin import DEFAULTS


@pytest.fixture
def youtube(monkeypatch):
    state = SimpleNamespace(entries=[], calls=[], images=[], events=[], image=b"image")

    class YoutubeDL:
        def __init__(self, options):
            state.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, query, *, download):
            state.calls.append((query, download))
            return {"entries": iter(state.entries)}

        def urlopen(self, request):
            state.images.append(request)
            if isinstance(state.image, Exception):
                raise state.image
            return BytesIO(state.image)

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    monkeypatch.setitem(sys.modules, "yt_dlp.networking", SimpleNamespace(
        Request=lambda url, **kwargs: SimpleNamespace(url=url, **kwargs)))
    monkeypatch.setattr(worker, "options", lambda config: {"playlistend": 1})
    monkeypatch.setattr(worker, "emit", lambda event, **kwargs: state.events.append((event, kwargs)))
    return state


def test_search_fetches_bounded_flat_results_with_optional_artwork(youtube):
    youtube.entries = [None, {"id": "bad"}, {"id": "live0000000", "is_live": True},
                       {"id": "soon0000000", "live_status": "is_upcoming"},
                       {"id": "abcdefghijk", "title": "日本語 Song", "channel": "Artist",
                        "duration": 183, "thumbnails": [{"url": "https://example.com/cover.jpg"}]},
                       {"id": "other000000", "uploader": "Channel"}]
    results = worker.search("  日本語 Song  ", {})["results"]
    assert youtube.calls == [("ytsearch10:日本語 Song", False)]
    assert youtube.options["playlistend"] == 10
    assert youtube.options["extract_flat"] is True
    assert youtube.options["skip_download"] is True
    assert len(results) == 2
    assert results[0] == {"url": "https://www.youtube.com/watch?v=abcdefghijk",
                          "title": "日本語 Song", "artist": "Artist", "duration": 183,
                          "thumbnail": base64.b64encode(b"image").decode("ascii")}
    assert youtube.images[0].extensions == {"timeout": 3}
    assert youtube.events[0] == ("search_result", {"result": results[0]})


@pytest.mark.parametrize("image", [OSError("offline"), b"x" * 120_001], ids=["offline", "oversized"])
def test_search_keeps_tracks_when_artwork_fails_or_is_oversized(youtube, image):
    youtube.image = image
    youtube.entries = [{"id": "abcdefghijk", "thumbnail": "https://example.com/cover.jpg"}]
    assert "thumbnail" not in worker.search("song", {})["results"][0]


def test_search_does_not_consume_more_than_ten_entries(youtube):
    youtube.entries = [{"id": "abcdefghijk"}] * 20
    assert len(worker.search("song", {})["results"]) == 10


@pytest.mark.parametrize("query", ["", "   ", "x" * 501])
def test_search_rejects_empty_or_excessive_queries(youtube, query):
    with pytest.raises(ValueError, match="1 to 500"):
        worker.search(query, {})
    assert not youtube.calls


@pytest.mark.parametrize("label", ["song and lyrics", "Song, Lyrics, and Music Video"])
def test_spotify_track_lookup_uses_public_song_title_and_credits(monkeypatch, label):
    track_id = "6oh7sJRadSp6vjb5Qy4mVx"

    def open_page(request, *, timeout):
        assert request.full_url == f"https://open.spotify.com/track/{track_id}"
        assert request.get_header("Accept-language") == "en"
        assert timeout > 0
        return BytesIO(f"<title>Live &amp; Loud - {label} by Artist A, Artist B | Spotify</title>".encode())

    monkeypatch.setattr(worker, "urlopen", open_page)
    assert worker.spotify_search_query(f"https://open.spotify.com/intl-de/track/{track_id}?si=share") == {
        "query": "Artist A, Artist B Live & Loud",
    }
    assert worker.spotify_track_url(f"spotify:track:{track_id}") == f"https://open.spotify.com/track/{track_id}"
    assert worker.spotify_track_url(f"https://open.spotify.com.example.com/track/{track_id}") is None
    assert worker.spotify_track_url(f"https://open.spotify.com/album/{track_id}") is None


def test_spotify_lookup_without_artist_prompts_for_manual_search(monkeypatch):
    monkeypatch.setattr(worker, "urlopen", lambda *args, **kwargs: BytesIO(b"<title>Spotify</title>"))
    with pytest.raises(ValueError, match="Could not read the song and artist"):
        worker.spotify_search_query("spotify:track:6oh7sJRadSp6vjb5Qy4mVx")


def test_spotify_playlist_reports_pages_with_duplicate_and_unavailable_entries(monkeypatch):
    playlist_id = "37i9dQZF1DWWQRwui0ExPn"
    canonical_url = f"https://open.spotify.com/playlist/{playlist_id}"
    events = []

    class PlaylistTrack:
        name = "Track"
        artists = SimpleNamespace(items=[SimpleNamespace(profile=SimpleNamespace(name="Artist"))])

    class Catalogue:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_playlist(self, url):
            assert url == canonical_url
            return SimpleNamespace(name="Mix", total_count=3)

        async def get_playlist_items(self, url):
            assert url == canonical_url
            yield [SimpleNamespace(item=PlaylistTrack()), SimpleNamespace(item=None)]
            yield [SimpleNamespace(item=PlaylistTrack())]

    monkeypatch.setitem(sys.modules, "spotapi", SimpleNamespace(Catalogue=Catalogue))
    monkeypatch.setitem(sys.modules, "spotapi.models", SimpleNamespace(PlaylistTrack=PlaylistTrack))
    monkeypatch.setattr(worker, "emit", lambda event, **data: events.append((event, data)))

    assert worker.spotify_playlist(f"spotify:playlist:{playlist_id}") == {"title": "Mix", "loaded": 3, "searchable": 2}
    song = {"title": "Track", "artist": "Artist", "query": "Artist Track"}
    assert events == [
        ("spotify_playlist_page", {"entries": [song, {"title": "Unavailable song at position 2"}], "loaded": 2, "total": 3}),
        ("spotify_playlist_page", {"entries": [song], "loaded": 3, "total": 3}),
    ]
    assert worker.spotify_playlist_url(f"https://open.spotify.com/intl-en/user/spotify/playlist/{playlist_id}?si=share") == canonical_url


def test_spotify_playlist_dependency_is_loaded_only_when_needed(monkeypatch):
    monkeypatch.setitem(sys.modules, "spotapi", None)
    with pytest.raises(ValueError, match="Install or repair SpotAPI in Settings > Dependencies"):
        worker.spotify_playlist("spotify:playlist:37i9dQZF1DWWQRwui0ExPn")


def test_isolated_worker_can_start_an_async_thread():
    script = """
import asyncio
import runpy
import sys

runpy.run_path(sys.argv[1])
assert asyncio.run(asyncio.to_thread(lambda: True))
"""
    result = subprocess.run([sys.executable, "-I", "-c", script, worker.__file__],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def dialog(qapp):
    settings = {}
    api = SimpleNamespace(get_setting=lambda key, default=None: settings.get(key, default),
                          set_setting=lambda key, value: settings.__setitem__(key, value))
    window = ImportDialog(api, DEFAULTS)
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_spotify_link_fills_editable_search_without_running_it(dialog, monkeypatch):
    actions = []
    monkeypatch.setattr(dialog, "_start", actions.append)
    dialog.url.setText("spotify:track:6oh7sJRadSp6vjb5Qy4mVx")
    assert dialog.preview_button.text() == "Fill search"
    dialog.preview()
    assert actions == ["spotify_search_query"]
    dialog._succeeded("spotify_search_query", {"query": "Artist Song"})
    dialog._finished()
    assert dialog.url.text() == "Artist Song"
    assert dialog.preview_button.text() == "Search"
    assert dialog.url.isEnabled()
    assert actions == ["spotify_search_query"]
    dialog.preview()
    assert actions == ["spotify_search_query", "search"]


def test_query_url_routing_and_selection_preserve_results(dialog, monkeypatch):
    actions = []
    monkeypatch.setattr(dialog, "_start", actions.append)
    dialog.url.setText("artist song")
    dialog.preview()
    assert actions == ["search"]
    assert dialog.preview_button.text() == "Search"
    entry = {"title": "Song", "artist": "Artist", "duration": 183,
             "url": "https://www.youtube.com/watch?v=abcdefghijk"}
    dialog._succeeded("search", {"results": [entry]})
    item = dialog.search_results.item(0)
    assert item.text() == "Song\nArtist · 3:03"
    assert not dialog.download_button.isEnabled()
    dialog.search_results.itemActivated.emit(item)
    assert actions == ["search", "preview"]
    assert dialog.preview_url == entry["url"]
    assert dialog.search_results.count() == 1
    assert dialog.preview_button.text() == "Preview"
    dialog._succeeded("preview", {"identity": "Youtube:abcdefghijk", "title": "Song"})
    dialog._finished()
    assert dialog.download_button.isEnabled()
    dialog.url.setText("another song")
    assert dialog.info is None
    assert dialog.search_results.count() == 0
    assert not dialog.download_button.isEnabled()


def test_empty_results_retry_and_busy_controls(dialog, monkeypatch):
    dialog._succeeded("search", {"results": []})
    assert "No finished tracks" in dialog.status.text()
    assert dialog.search_results.isHidden()
    dialog._set_busy(True)
    assert not dialog.url.isEnabled()
    assert not dialog.search_results.isEnabled()
    assert dialog.cancel_button.isEnabled()
    dialog._set_busy(False)
    dialog.url.setText("song")
    actions = []
    monkeypatch.setattr(dialog, "_start", actions.append)
    dialog._last_action = "search"
    dialog._failed("Search unavailable")
    dialog._retry()
    assert actions == ["search"]
    dialog.url.setText("file:///song")
    dialog.preview()
    assert actions == ["search"]
    assert "http" in dialog.status.text()


def test_search_worker_protocol_and_cancellation(qapp, tmp_path, monkeypatch):
    helper = tmp_path / "search_worker.py"
    helper.write_text(
        "import json, os, time\n"
        "if os.name != 'nt': os.setsid()\n"
        "request = json.load(__import__('sys').stdin)\n"
        "assert request['action'] == 'search' and request['query'] == 'song'\n"
        "print('LRCPLUS_YTDLP ' + json.dumps({'event': 'search_result', 'result': "
        "{'title': 'Song', 'url': 'https://www.youtube.com/watch?v=abcdefghijk'}}), flush=True)\n"
        "time.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr("plugins.community.ytdlp_import.job.worker_command",
                        lambda: [sys.executable, "-u", str(helper)])
    job = ImportJob({"action": "search", "query": "song", "config": {}})
    finished, cancelled, succeeded = QSignalSpy(job.finished), QSignalSpy(job.cancelled), QSignalSpy(job.succeeded)
    job.event_received.connect(lambda event: job.cancel())
    try:
        job.start()
        if not finished.count():
            assert finished.wait(10000)
        assert cancelled.count() == 1
        assert succeeded.count() == 0
        assert job.stage is None
    finally:
        job.shutdown()
        job.deleteLater()
        qapp.processEvents()
