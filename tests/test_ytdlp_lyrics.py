from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from plugins.community.ytdlp_import import lyrics, worker
from plugins.community.ytdlp_import.dialog import ImportDialog
from plugins.community.ytdlp_import.job import ImportJob
from plugins.community.ytdlp_import.plugin import DEFAULTS, YtdlpImportPlugin


def record(**changes):
    return dict(trackName="Song", artistName="Singer", albumName="Album", duration=180,
                syncedLyrics="[00:01.00]Hello", plainLyrics="Hello", **changes)


def test_matching_prefers_synced_lyrics_and_then_matching_album(monkeypatch):
    plain = record()
    plain["syncedLyrics"] = None
    other_album = dict(record(), albumName="Other")
    match = dict(record(), syncedLyrics="[00:01.00]Album version")
    monkeypatch.setattr(lyrics, "_fetch", lambda *args: [plain, other_album, match])
    assert lyrics.find_lyrics({"track": "Song", "artist": "Singer", "album": "Album", "duration": 181}) == {
        "extension": ".lrc", "text": "[00:01.00]Album version\n",
    }


@pytest.mark.parametrize("changes", [
    {"trackName": "Song (Live)"}, {"artistName": "Other"}, {"duration": 184},
    {"duration": None}, {"duration": float("nan")}, {"instrumental": True},
])
def test_uncertain_matches_are_rejected(monkeypatch, changes):
    monkeypatch.setattr(lyrics, "_fetch", lambda *args: [dict(record(), **changes)])
    assert lyrics.find_lyrics({"track": "Song", "artist": "Singer", "duration": 180}) is None


def test_search_cleans_video_labels_and_preserves_unicode(monkeypatch):
    calls = []
    monkeypatch.setattr(lyrics, "_fetch", lambda *args: calls.append(args) or [
        dict(record(), trackName="日本語", syncedLyrics=None, plainLyrics="こんにちは")])
    assert lyrics.find_lyrics({"title": "Singer - 日本語 (Official Video)", "uploader": "Channel"}) == {
        "extension": ".txt", "text": "こんにちは\n",
    }
    assert calls == [("日本語", "Singer")]


def test_missing_artist_does_not_request_network(monkeypatch):
    monkeypatch.setattr(lyrics, "_fetch", lambda *args: pytest.fail("No usable artist"))
    assert lyrics.find_lyrics({"title": "Song"}) is None


@pytest.mark.parametrize("synced", ["not timed", "[00:01.00]Hello\n" * 5001,
                                     "[00:01.00]" * 5001 + "Hello"], ids=["untimed", "too-many-lines", "too-many-timestamps"])
def test_invalid_or_oversized_synced_lyrics_fall_back_to_plain(monkeypatch, synced):
    monkeypatch.setattr(lyrics, "_fetch", lambda *args: [dict(record(), syncedLyrics=synced)])
    assert lyrics.find_lyrics({"track": "Song", "artist": "Singer"})["extension"] == ".txt"


@pytest.mark.parametrize("payload, error", [(b"[]", None), (b"{}", "invalid"),
                                           (b"x" * (lyrics.MAX_RESPONSE_BYTES + 1), "oversized")], ids=["valid", "invalid", "oversized"])
def test_network_request_is_identified_bounded_and_uses_structured_search(monkeypatch, payload, error):
    from io import BytesIO
    calls = []

    def open_request(request, timeout):
        calls.append((request, timeout))
        return BytesIO(payload)

    monkeypatch.setattr(lyrics, "urlopen", open_request)
    if error:
        with pytest.raises(ValueError, match=error):
            lyrics._fetch("日本語", "Singer")
    else:
        assert lyrics._fetch("日本語", "Singer") == []
    request, timeout = calls[0]
    assert parse_qs(urlsplit(request.full_url).query) == {"track_name": ["日本語"], "artist_name": ["Singer"]}
    assert "LRCPlus" in request.get_header("User-agent")
    assert timeout == 10


def existing_request(tmp_path):
    stage = tmp_path / ".lrcplus-import-test"
    stage.mkdir()
    audio = tmp_path / "song.m4a"
    audio.write_bytes(b"existing audio")
    return dict(action="lyrics", config={"automatically_find_lyrics": True}, stage=str(stage),
                folder=str(tmp_path), audio_path=str(audio),
                info=worker.metadata({"title": "Singer - Song (Official Video)", "uploader": "Channel", "duration": 180}))


@pytest.mark.parametrize("extension", [".lrcx", ".lrc", ".txt"])
def test_existing_lyrics_are_reused_without_network_or_audio_changes(tmp_path, monkeypatch, extension):
    request = existing_request(tmp_path)
    path = Path(request["audio_path"]).with_suffix(extension)
    path.write_text("Authored lyrics", encoding="utf-8")
    monkeypatch.setattr(worker, "find_lyrics", lambda *args: pytest.fail("Must preserve existing lyrics"))
    result = worker.find_existing_audio_lyrics(request)
    assert result["lyrics_path"] == str(path)
    assert result["lyrics_source"] == "existing"
    assert Path(request["audio_path"]).read_bytes() == b"existing audio"
    assert path.read_text(encoding="utf-8") == "Authored lyrics"


def test_existing_audio_lookup_uses_original_search_terms(tmp_path, monkeypatch):
    request = existing_request(tmp_path)
    calls = []
    monkeypatch.setattr(worker, "find_lyrics", lambda info: calls.append(info) or {
        "extension": ".lrc", "text": "[00:01.00]Hello\n"})
    result = worker.find_existing_audio_lyrics(request)
    assert calls == [{"track": "Song", "artist": "Singer", "album": "", "duration": 180}]
    assert Path(result["lyrics_path"]).read_text(encoding="utf-8") == "[00:01.00]Hello\n"
    assert Path(request["audio_path"]).read_bytes() == b"existing audio"


def test_worker_can_reuse_lyrics_in_isolated_runtime(qapp, tmp_path):
    from PySide6.QtTest import QSignalSpy
    request = existing_request(tmp_path)
    authored = Path(request["audio_path"]).with_suffix(".lrc")
    authored.write_text("[00:01.00]Authored", encoding="utf-8")
    job = ImportJob(request)
    succeeded, failed, finished = QSignalSpy(job.succeeded), QSignalSpy(job.failed), QSignalSpy(job.finished)
    try:
        job.start()
        stage = job.stage
        if not finished.count():
            assert finished.wait(10000)
        assert failed.count() == 0
        assert succeeded.at(0)[0]["lyrics_path"] == str(authored)
        assert not stage.exists()
    finally:
        job.shutdown()
        job.deleteLater()
        qapp.processEvents()


class API:
    def __init__(self):
        self.settings, self.attached, self.imports = {}, [], []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def attach_lyrics(self, audio, lyric):
        self.attached.append((audio, lyric))

    def import_audio_files(self, paths, **kwargs):
        self.imports.extend(paths)


def test_dialog_finds_missing_lyrics_without_downloading_audio_and_remembers_option(qapp, tmp_path, monkeypatch):
    api = API()
    audio = tmp_path / "song.m4a"
    audio.touch()
    api.settings["downloads"] = {"Test:song:m4a": {"path": str(audio), "url": "https://example.com/song"}}
    calls = []
    monkeypatch.setattr(ImportJob, "start", lambda self: calls.append(self.request))
    dialog = ImportDialog(api, DEFAULTS)
    try:
        dialog._succeeded("preview", {"identity": "Test:song", "title": "Song"})
        assert not dialog.automatically_find_lyrics.isChecked()
        dialog.automatically_find_lyrics.setChecked(True)
        assert dialog.download_button.text() == "Find lyrics"
        dialog.download_button.click()
        assert calls[0]["action"] == "lyrics"
        assert calls[0]["audio_path"] == str(audio)
        assert calls[0]["config"]["automatically_find_lyrics"] is True
        assert api.settings["automatically_find_lyrics"] is True
        assert not dialog.automatically_find_lyrics.isEnabled()
        assert not api.imports
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("extension", [".lrc", ".txt"])
def test_found_lyrics_attach_independently_of_subtitle_conversion_and_reuse_history(qapp, tmp_path, extension):
    api = API()
    audio, lyric = tmp_path / "song.m4a", tmp_path / ("song" + extension)
    audio.touch()
    lyric.touch()
    dialog = ImportDialog(api, dict(DEFAULTS, automatically_find_lyrics=True))
    try:
        dialog.info = {"identity": "Test:song"}
        dialog.subtitles_as_lyrics.setChecked(False)
        dialog._succeeded("download", {"path": str(audio), "lyrics_path": str(lyric), "lyrics_source": "LRCLIB"})
        assert api.attached == [(str(audio), str(lyric))]
        assert api.imports == [str(audio)]
        dialog.download()
        assert len(api.attached) == 2
        assert len(api.imports) == 2
        assert dialog.job is None
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_plugin_settings_save_and_restore_option(qapp):
    api = API()
    plugin = YtdlpImportPlugin()
    plugin.context = SimpleNamespace(api=api)
    widget = plugin.create_settings_widget()
    try:
        assert not widget.automatically_find_lyrics.isChecked()
        widget.automatically_find_lyrics.setChecked(True)
        plugin.apply_settings_widget(widget)
        assert plugin.settings()["automatically_find_lyrics"] is True
    finally:
        widget.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("convert_subtitles", [True, False])
def test_remembered_provider_lyrics_do_not_replace_explicit_subtitle_choice(qapp, tmp_path, convert_subtitles):
    api = API()
    audio, lyric, caption = [tmp_path / name for name in ("song.m4a", "song.lrc", "song.en.manual.vtt")]
    for path in (audio, lyric, caption):
        path.touch()
    selected = {"language": "en", "kind": "manual"}
    api.settings["downloads"] = {"Test:song:m4a": dict(
        path=str(audio), lyrics_path=str(lyric), lyrics_source="LRCLIB", subtitle=selected, subtitle_path=str(caption))}
    dialog = ImportDialog(api, dict(DEFAULTS, subtitles_as_lyrics=convert_subtitles))
    try:
        dialog._succeeded("preview", {"identity": "Test:song", "subtitle_tracks": [dict(selected, name="English")]})
        dialog.subtitle_combo.setCurrentIndex(1)
        if convert_subtitles:
            assert dialog.download_button.text() == "Get subtitles"
        else:
            assert dialog.download_button.text() == "Add existing"
            dialog.download()
            assert api.imports == [str(audio)]
            assert not api.attached
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
