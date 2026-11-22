import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget, QWidget

from core import public_lyrics
from core.settings import get_lyrics_attachment
from lyrics.loader import load_lyrics
from lyrics.provider_credit import add_provider_credit, read_provider_credit
from ui.lyrics_search_dialog import LyricsSearchDialogMixin


_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1s" end="3s">'
    '<span begin="0.25s" end="0.75s">Hello </span>'
    '<span begin="1s" end="1.5s">world</span></p></div></body></tt>'
)
_ISRC = "USABC2600001"
_VIDEO = "abcdefghijk"


class _Response:
    headers = {}

    def __init__(self, data, status=200):
        self.status_code = status
        self.content = (data if isinstance(data, str) else json.dumps(data)).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def iter_content(self, *, chunk_size):
        yield self.content


@pytest.mark.parametrize("source, kind, timing, suffix", [
    ("lrc.red", "ttml", "Word-synced", ".lrcx"),
    ("lrc.red", "fallback", "Line-synced", ".lrc"),
    ("Unison", "ttml", "Word-synced", ".lrcx"),
    ("Unison", "plain", "Plain text", ".txt"),
    ("lyrics.ovh", "plain", "Plain text", ".txt"),
])
def test_public_provider_search_preview_and_import(qapp, monkeypatch, tmp_path, source, kind, timing, suffix):
    contact = "test@example.com"
    monkeypatch.delenv("LRCRED_CONTACT_EMAIL", raising=False)
    calls = []
    base = public_lyrics.BASE_URLS[source]

    def get(url, *, params, headers, **options):
        assert url.startswith(base + "/")
        assert (contact in headers["User-Agent"]) == (source == "lrc.red")
        path = url.removeprefix(base)
        calls.append(path)
        if path == "/search.json":
            assert params == {"q": "Artist Song"}
            return _Response({"hits": [{"isrc": _ISRC, "title": "Song", "artist": "Artist", "duration": 180}]})
        if path == f"/s/{_ISRC}.ttml":
            return _Response(_TTML) if kind == "ttml" else _Response("", 404)
        if path == f"/s/{_ISRC}.lrc":
            return _Response("[offset:100]\n[00:01.100]Hello world")
        if path == "/lyrics/search":
            assert params["q"] == "Song Artist"
            return _Response({"success": True, "data": [
                {"id": 42, "videoId": _VIDEO, "song": "Song", "artist": "Artist"},
            ]})
        if path == "/lyrics/42":
            return _Response({"success": True, "data": {
                "id": 42, "videoId": _VIDEO, "format": kind,
                "lyrics": _TTML if kind == "ttml" else "Hello world",
                "submitter": {"displayName": "A & B", "keyId": "curator-fixture"},
            }})
        assert path == "/v1/Artist/Song"
        return _Response({"lyrics": "Hello world"})

    def interact(dialog):
        picker = dialog.findChild(QComboBox)
        picker.setCurrentText(source)
        dialog.findChild(QLineEdit, "lyricsSearchTitle").setText("Song")
        dialog.findChild(QLineEdit, "lyricsSearchArtist").setText("Artist")
        search = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search")
        if source == "lrc.red":
            search.click()
            assert not calls
            field = next(field for field in dialog.findChildren(QLineEdit) if field.accessibleName() == "lrc.red contact email")
            field.setText(contact)
        search.click()
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        deadline = monotonic() + 4
        while not use.isEnabled() and monotonic() < deadline:
            qapp.processEvents()
            sleep(.005)
        assert use.isEnabled()
        assert dialog.findChild(QTableWidget).item(0, 3).text() == timing
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        if source == "Unison":
            assert any(f'{base}/curator/curator-fixture">Submitted by A &amp; B</a>' in text for text in labels)
        if source == "lrc.red":
            assert any(f'{base}/s/{_ISRC}">Lyrics from lrc.red</a>' in text for text in labels)
        before = list(calls)
        picker.setCurrentText("LRCLIB")
        picker.setCurrentText(source)
        assert use.isEnabled() and calls == before
        use.click()
        return 1

    monkeypatch.setattr(public_lyrics.requests, "get", get)
    monkeypatch.setattr(QDialog, "exec", interact)
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Song.mp3"
    loaded = []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    try:
        LyricsSearchDialogMixin.search_lrclib_lyrics(parent)
        saved, = map(Path, loaded)
        assert saved.suffix == suffix
        assert Path(get_lyrics_attachment(parent.current_audio_path)) == saved
        line = load_lyrics(saved)[0]
        assert line.text == "Hello world"
        if kind == "ttml":
            assert (line.start, line.end) == (1.0, 3.0)
            assert [(segment.start, segment.end) for segment in line.segments] == [(1.25, 1.75), (2.0, 2.5)]
        elif kind == "fallback":
            assert line.start == 1.0
        assert len(read_provider_credit(saved)) == {"lrc.red": 1, "Unison": 2, "lyrics.ovh": 0}[source]
    finally:
        parent.deleteLater()


def test_lyrics_ovh_title_only_search_resolves_and_encodes_artist(monkeypatch):
    calls = []

    def get(url, **options):
        calls.append(url)
        if "/suggest/" in url:
            row = {"title": "A + B", "artist": {"name": "AC/DC"}, "album": {"title": "Album"}, "duration": 150}
            return _Response({"data": [row, row]})
        return _Response({"lyrics": "Hello world"})

    monkeypatch.setattr(public_lyrics.requests, "get", get)
    cancel = Event()
    record, = public_lyrics.search("lyrics.ovh", "A + B", "", cancel)
    assert record["albumName"] == "Album"
    assert public_lyrics.fetch_lyrics(record, cancel) == {"plainLyrics": "Hello world"}
    assert calls == ["https://api.lyrics.ovh/suggest/A%20%2B%20B", "https://api.lyrics.ovh/v1/AC%2FDC/A%20%2B%20B"]


def test_playback_shows_saved_source_links_without_treating_credit_as_lyrics(qapp, tmp_path):
    from core.lyrics_controller import LyricsController

    path = tmp_path / "Song.txt"
    source_url = f"https://unison.betterlyrics.org/song/{_VIDEO}"
    submitter_url = "https://unison.betterlyrics.org/curator/example"
    credits = (f"Lyrics from Unison {source_url}", f"Submitted by Example {submitter_url}")
    path.write_text(add_provider_credit("Hello world", credits, synced=False), encoding="utf-8")
    label = QLabel()
    player = SimpleNamespace(lyrics_credit_label=label, _present_loaded_lyrics=lambda **options: None)
    try:
        LyricsController.load_lrc_file(player, path)
        assert [line.text for line in player.lyric_lines] == ["Hello world"]
        assert not label.isHidden()
        assert f'href="{source_url}"' in label.text()
        assert f'href="{submitter_url}"' in label.text()
        path.write_text("Next song", encoding="utf-8")
        LyricsController.load_lrc_file(player, path)
        assert label.isHidden()
    finally:
        label.deleteLater()


@pytest.mark.parametrize("source, expected_timing", [
    (public_lyrics.AMLL, "Word-synced"),
    (public_lyrics.BETTER, "Word-synced"),
    (public_lyrics.LIRIQO, "Word-synced"),
    (public_lyrics.VOCADB, "Plain text"),
    (public_lyrics.LYRICA, "Line-synced"),
    (public_lyrics.FETCH_LYRICS, "Plain text"),
])
def test_additional_provider_search_and_import(qapp, monkeypatch, tmp_path, source, expected_timing):
    server = "http://127.0.0.1:7689"
    base = public_lyrics.BASE_URLS.get(source, server)
    calls = []
    lyrics_link = "https://utaten.com/lyric/demo/"
    timed = [{"text": "Hello world", "start": 1000, "end": 3000, "id": 21}]
    words = [{"text": "Hello", "start": 1250, "end": 1750}, {"text": "world", "start": 2000, "end": 2500}]
    liriqo_entry = {"provider": "apple_music_liriqo", "source": "Apple Music", "timed": timed, "words": {"21": words}}

    def get(url, *, params, **options):
        assert url.startswith(base + "/")
        path = url.removeprefix(base)
        calls.append(path)
        if path == "/v1/lyrics/search":
            return _Response({"data": {"items": [{"id": 42, "musicNames": ["Song"], "artistNames": ["Artist"],
                                                    "authorUsernames": ["contributor"]}]}})
        if path == "/v1/lyrics/get":
            assert params == {"id": 42}
            return _Response({"data": {"id": 42, "lyrics": _TTML}})
        if path == "/getLyrics":
            assert params == {"s": "Song", "a": "Artist", "al": "Album", "d": 120}
            return _Response({}, 401)
        if path == "/qq/getLyrics":
            return _Response({"lyrics": '<QrcInfos><Lyric_1 LyricContent="[ti:Song]\n'
                                        '[1000,2000]Hello (1250,500)world(2000,500)" /></QrcInfos>'})
        if path == "/lyrics":
            assert params["title"] == "Song" and params["artist"] == "Artist"
            return _Response({"primary": liriqo_entry, "tracks": [liriqo_entry, {
                "provider": "lrclib", "source": "LRCLIB", "plain": "Another version",
            }]})
        if path == "/songs":
            return _Response({"items": [{"id": 84, "name": "Song", "artistString": "Artist", "lyrics": [
                {"id": 51, "translationType": "Original", "cultureCodes": ["ja"], "value": "Original lyrics"},
                {"id": 52, "translationType": "Translation", "cultureCodes": ["en"], "value": "Hello world"},
            ]}]})
        if path == "/lyrics/":
            assert params["artist"] == "Artist" and params["word"] == "true"
            return _Response({"status": "success", "data": {"source": "lrclib", "timed_lyrics": [
                {"text": "Hello world", "start_time": 1000, "end_time": 3000},
            ]}})
        if path == "/api/fetchSongs":
            return _Response([{
                "siteName": "UtaTen", "songInfo": {"title": "Song", "url": lyrics_link,
                                                   "artists": [{"type": "artist", "artists": ["Artist"]}]},
            }])
        assert path == "/api/fetchLyrics" and params == {"link": lyrics_link}
        return _Response({"lyrics": "漢字", "rubyLyrics": "<ruby>漢字<rt>かんじ</rt></ruby>"})

    def interact(dialog):
        dialog.findChild(QComboBox).setCurrentText(source)
        dialog.findChild(QLineEdit, "lyricsSearchTitle").setText("Song")
        dialog.findChild(QLineEdit, "lyricsSearchArtist").setText("Artist")
        edits = {field.accessibleName(): field for field in dialog.findChildren(QLineEdit)}
        if source == public_lyrics.BETTER:
            edits["Search album"].setText("Album")
            dialog.findChild(QSpinBox).setValue(120)
        if source in public_lyrics.SERVER_DEFAULTS:
            edits["Lyrics server URL"].setText(server)
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        deadline = monotonic() + 4
        while not use.isEnabled() and monotonic() < deadline:
            qapp.processEvents()
            sleep(.005)
        assert use.isEnabled()
        table = dialog.findChild(QTableWidget)
        if source == public_lyrics.VOCADB:
            assert table.rowCount() == 2 and "Translation" in table.item(1, 0).text()
            table.selectRow(1)
        if source == public_lyrics.LIRIQO:
            assert table.rowCount() == 2 and "Apple Music" in table.item(0, 0).text()
        assert table.item(table.currentRow(), 3).text() == expected_timing
        use.click()
        return 1

    monkeypatch.setattr(public_lyrics.requests, "get", get)
    monkeypatch.setattr(QDialog, "exec", interact)
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Song.mp3"
    loaded = []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    try:
        LyricsSearchDialogMixin.search_lrclib_lyrics(parent)
        path, = map(Path, loaded)
        line = load_lyrics(path)[0]
        assert line.text == ("漢字" if source == public_lyrics.FETCH_LYRICS else "Hello world")
        assert len(read_provider_credit(path)) == (2 if source == public_lyrics.AMLL else 1)
        if source == public_lyrics.FETCH_LYRICS:
            assert path.suffix == ".txt" and line.segments[0].ruby_spans == [(0, 2, "かんじ")]
        if expected_timing != "Plain text":
            assert (line.start, line.end) == (1, 3)
        if expected_timing == "Word-synced":
            assert [(word.start, word.end) for word in line.segments] == [(1.25, 1.75), (2, 2.5)]
        if source == public_lyrics.BETTER:
            assert calls == ["/getLyrics", "/qq/getLyrics"]
    finally:
        parent.deleteLater()


def test_lyrica_suggestions_can_be_bypassed_with_an_artist(monkeypatch):
    def get(url, **options):
        if url.endswith("/suggestion"):
            return _Response({}, 502)
        assert url.endswith("/lyrics/")
        return _Response({"status": "success", "data": {"lyrics": "Hello world"}})

    monkeypatch.setattr(public_lyrics.requests, "get", get)
    cancel = Event()
    with pytest.raises(public_lyrics.LyricsProviderError, match="Enter the artist"):
        public_lyrics.search(public_lyrics.LYRICA, "Song", "", cancel)
    result, = public_lyrics.search(public_lyrics.LYRICA, "Song", "Artist", cancel)
    assert public_lyrics.fetch_lyrics(result, cancel)["plainLyrics"] == "Hello world"
