import base64
from copy import deepcopy
import shutil
import subprocess
import sys
from types import ModuleType
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from plugins.community.ytdlp_import import artwork, worker
from plugins.community.ytdlp_import.dialog import ImportDialog
from plugins.community.ytdlp_import.job import ImportJob
from plugins.community.ytdlp_import.plugin import DEFAULTS


IMAGE = b"\xff\xd8\xff" + b"test cover"
ALBUM_ID = "11111111-1111-4111-8111-111111111111"
SINGLE_ID = "22222222-2222-4222-8222-222222222222"


def recording():
    return {"title": "日本語", "length": 180000, "artist-credit": [{"artist": {"name": "Singer"}}],
            "releases": [
                {"id": SINGLE_ID, "title": "Single", "status": "Official", "date": "2019",
                 "release-group": {"primary-type": "Single"}},
                {"id": ALBUM_ID, "title": "Album", "status": "Official", "date": "2020",
                 "release-group": {"primary-type": "Album"}},
            ]}


def mock_catalog(monkeypatch, recordings):
    calls = []

    def fetch(url, *, image=False):
        calls.append(url)
        return IMAGE if image else {"recordings": recordings}

    monkeypatch.setattr(artwork, "_fetch", fetch)
    return calls


def test_album_matching_uses_unicode_and_prefers_album_over_single(monkeypatch):
    calls = mock_catalog(monkeypatch, [recording()])
    cover = artwork.find_cover({"track": "日本語", "artist": "Singer", "duration": 182})
    assert cover["release_id"] == ALBUM_ID
    assert base64.b64decode(cover["data"]) == IMAGE
    assert calls[-1] == f"https://coverartarchive.org/release/{ALBUM_ID}/front-500"


@pytest.mark.parametrize("change", [
    {"title": "日本語 (Live)"}, {"artist-credit": [{"artist": {"name": "Other Singer"}}]},
    {"length": 300000}, {"releases": []},
])
def test_unreliable_recordings_never_fetch_artwork(monkeypatch, change):
    item = dict(recording(), **change)
    calls = mock_catalog(monkeypatch, [item])
    assert artwork.find_cover({"track": "日本語", "artist": "Singer", "duration": 180}) is None
    assert len(calls) == 1


def test_source_album_is_required_even_when_another_release_has_art(monkeypatch):
    calls = mock_catalog(monkeypatch, [recording()])
    assert artwork.find_cover({"track": "日本語", "artist": "Singer", "album": "Missing album"}) is None
    assert len(calls) == 1
    query = parse_qs(urlsplit(calls[0]).query)["query"][0]
    assert 'release:"Missing album"' in query


def test_missing_front_cover_tries_another_matching_release(monkeypatch):
    calls = mock_catalog(monkeypatch, [recording()])
    original = artwork._fetch

    def fetch(url, **kwargs):
        if ALBUM_ID in url:
            raise HTTPError(url, 404, "No cover", {}, None)
        return original(url, **kwargs)

    monkeypatch.setattr(artwork, "_fetch", fetch)
    assert artwork.find_cover({"track": "日本語", "artist": "Singer"})["release_id"] == SINGLE_ID


@pytest.mark.parametrize("info, expected", [
    ({"title": "Singer - 日本語 (Official Music Video)", "uploader": "Channel"}, ("日本語", "Singer", "")),
    ({"title": "日本語", "uploader": "Singer - Topic"}, ("日本語", "Singer", "")),
    ({"track": "日本語 (Live)", "artist": "Singer", "album": "Live album"}, ("日本語 (Live)", "Singer", "Live album")),
])
def test_search_terms_keep_versions_and_remove_video_presentation(info, expected):
    assert artwork.search_terms(info) == expected


def test_primary_artist_is_used_when_music_uploader_confirms_it(monkeypatch):
    info = {"title": "日本語", "track": "日本語", "artist": "Singer, Composer",
            "artists": ["Singer", "Composer"], "uploader": "Singer - Topic", "album": "Album", "duration": 180}
    calls = mock_catalog(monkeypatch, [recording()])
    assert artwork.search_terms(info) == ("日本語", "Singer", "Album")
    assert artwork.find_cover(info)["release_id"] == ALBUM_ID
    query = parse_qs(urlsplit(calls[0]).query)["query"][0]
    assert 'artist:"Singer"' in query


def test_multiple_artists_are_kept_when_uploader_does_not_confirm_primary():
    info = {"track": "Song", "artist": "Singer, Guest", "artists": ["Singer", "Guest"], "uploader": "Label"}
    assert artwork.search_terms(info) == ("Song", "Singer, Guest", "")


def test_fetch_identifies_app_limits_responses_and_spaces_requests(monkeypatch):
    requests, sleeps = [], []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, count):
            assert count == artwork.MAX_IMAGE_BYTES + 1
            return b"{}"

    monkeypatch.setattr(artwork, "urlopen", lambda request, timeout: requests.append((request, timeout)) or Response())
    monkeypatch.setattr(artwork.time, "monotonic", lambda: 10)
    monkeypatch.setattr(artwork.time, "sleep", sleeps.append)
    monkeypatch.setattr(artwork, "_last_request", 9.5)
    assert artwork._fetch("https://musicbrainz.org/ws/2/recording/") == {}
    assert sleeps[0] == pytest.approx(0.6)
    assert "github.com/pxib/lrcplus" in requests[0][0].get_header("User-agent")
    assert requests[0][1] == 10


@pytest.mark.parametrize("outcome", [None, TimeoutError("offline"), {"album": "Album", "release_id": ALBUM_ID,
                                                                   "data": base64.b64encode(IMAGE).decode()}])
def test_preview_falls_back_to_source_or_uses_matched_cover(monkeypatch, outcome):
    calls = []

    class YoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            return {"id": "song", "title": "Song", "thumbnail": "https://example.com/source.jpg"}

        def urlopen(self, url):
            from io import BytesIO
            calls.append(url)
            return BytesIO(b"source thumbnail")

    module = ModuleType("yt_dlp")
    module.YoutubeDL = YoutubeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setattr(worker, "options", lambda config: {})

    def find(info):
        if isinstance(outcome, Exception):
            raise outcome
        return deepcopy(outcome)

    monkeypatch.setattr(worker, "find_cover", find)
    result = worker.preview("https://example.com/song", {"use_musicbrainz_cover": True})
    if isinstance(outcome, dict):
        assert result["musicbrainz_cover"]["release_id"] == ALBUM_ID
        assert base64.b64decode(result["thumbnail"]) == IMAGE
        assert not calls
    else:
        assert base64.b64decode(result["thumbnail"]) == b"source thumbnail"
        assert "using source artwork" in result["artwork_message"]


@pytest.mark.parametrize("codec", ["mp3", "m4a"])
def test_embedded_cover_replaces_art_and_preserves_metadata_and_audio(tmp_path, codec):
    pytest.importorskip("mutagen")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed to generate a real audio fixture")
    path = tmp_path / ("song." + codec)
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1",
                    "-metadata", "title=Keep title", str(path)], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    cover = {"data": base64.b64encode(IMAGE).decode()}
    artwork.embed_cover(path, cover)
    artwork.embed_cover(path, cover)
    if codec == "mp3":
        from mutagen.mp3 import MP3
        audio = MP3(path)
        assert str(audio["TIT2"]) == "Keep title"
        assert [tag.data for tag in audio.tags.getall("APIC")] == [IMAGE]
    else:
        from mutagen.mp4 import MP4
        audio = MP4(path)
        assert audio["\xa9nam"] == ["Keep title"]
        assert audio["covr"] == [IMAGE]
    assert audio.info.length > 0


class API:
    def __init__(self):
        self.settings = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


def test_dialog_download_passes_previewed_cover_and_remembers_option(qapp, tmp_path, monkeypatch):
    requests = []
    monkeypatch.setattr(ImportJob, "start", lambda self: requests.append(self.request))
    api = API()
    dialog = ImportDialog(api, dict(DEFAULTS, download_folder=str(tmp_path)))
    try:
        dialog.url.setText("https://example.com/song")
        assert not dialog.use_musicbrainz_cover.isChecked()
        dialog.use_musicbrainz_cover.setChecked(True)
        dialog._succeeded("preview", {"identity": "Test:song", "title": "Song",
                                      "musicbrainz_cover": {"album": "Album", "release_id": ALBUM_ID},
                                      "thumbnail": base64.b64encode(IMAGE).decode()})
        assert "Album" in dialog.artwork_hint.text()
        dialog.download()
        assert requests[0]["musicbrainz_cover"]["data"] == base64.b64encode(IMAGE).decode()
        assert api.settings["use_musicbrainz_cover"] is True
        assert not dialog.use_musicbrainz_cover.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_changing_cover_option_requires_another_preview(qapp):
    dialog = ImportDialog(API(), DEFAULTS)
    try:
        dialog._succeeded("preview", {"identity": "Test:song", "title": "Song"})
        assert dialog.download_button.isEnabled()
        dialog.use_musicbrainz_cover.setChecked(True)
        assert dialog.info is None
        assert not dialog.download_button.isEnabled()
        assert "Preview" in dialog.status.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
