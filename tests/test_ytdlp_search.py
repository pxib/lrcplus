import base64
from io import BytesIO
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


@pytest.fixture
def dialog(qapp):
    api = SimpleNamespace(get_setting=lambda key, default=None: default)
    window = ImportDialog(api, DEFAULTS)
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


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
