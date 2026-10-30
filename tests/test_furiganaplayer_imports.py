"""The legacy player must work with the dependencies shipped by LRCPlus."""

import builtins
import runpy
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QSignalSpy


PLAYER_PATH = Path(__file__).resolve().parents[1] / "assets" / "apps" / "furiganaplayer.py"


def load_without_optional_packages(monkeypatch, *, standalone=False):
    original_import = builtins.__import__
    blocked = {"pygame", "janome", "requests"}
    if standalone:
        blocked.add("lyrics")

    def import_module(name, *args, **kwargs):
        if name.split(".", 1)[0] in blocked:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)
    return runpy.run_path(str(PLAYER_PATH), run_name="furiganaplayer_import_test")


def test_import_and_plain_text_fallback_need_only_qt(monkeypatch):
    module = load_without_optional_packages(monkeypatch, standalone=True)
    progress = []
    assert module["build_furigana_segments"]("Hello", progress.append) == [
        {"text": "Hello", "reading": None},
    ]
    assert progress == [[{"text": "Hello", "reading": None}]]
    assert module["build_furigana_segments"]("Hello", should_continue=lambda: False) == []


def test_readings_reuse_app_provider(monkeypatch):
    import lyrics.furigana as readings

    expected = [{"text": "Japan", "reading": "nihon"}]
    calls = []

    def generate(text, **kwargs):
        calls.append(text)
        return expected

    monkeypatch.setattr(readings, "build_furigana_segments", generate)
    module = load_without_optional_packages(monkeypatch)
    assert module["build_furigana_segments"]("Japan") == expected
    assert calls == ["Japan"]


def test_dictionary_lookup_works_without_requests(monkeypatch):
    import io
    import json

    module = load_without_optional_packages(monkeypatch, standalone=True)
    calls = []

    def open_request(request, timeout):
        calls.append((json.loads(request.data), request.method, timeout))
        return io.BytesIO(json.dumps({"results": [{"reading": {
            "furigana": "[Japan|nihon]",
        }}]}).encode("utf-8"))

    lookup = module["jotoba_lookup"]
    monkeypatch.setitem(lookup.__globals__, "urlopen", open_request)
    assert lookup("Japan") == [{"text": "Japan", "reading": "nihon"}]
    assert calls == [({"query": "Japan", "language": "English", "no_english": True}, "POST", 3)]
    lookup("Japan")
    assert len(calls) == 1


def test_standalone_playback_uses_qt_and_readings_arrive(qapp, monkeypatch, tmp_path):
    module = load_without_optional_packages(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "song.lrc").write_text("[00:01.00]Hello\n", encoding="utf-8")
    window = module["Player"]()
    try:
        plays = []
        monkeypatch.setattr(window.media_player, "play", lambda: plays.append(True))
        window.play_song()
        assert plays == [True]
        assert window.media_player.source() == QUrl.fromLocalFile(str(tmp_path / "song.mp3"))
        assert window.media_player.audioOutput() is window.audio_output

        monkeypatch.setattr(window.media_player, "playbackState", lambda: QMediaPlayer.PlayingState)
        monkeypatch.setattr(window.media_player, "position", lambda: 1500)
        window.lyric_widget.segment_provider = lambda text: [{"text": text, "reading": "hi"}]
        ready = QSignalSpy(window.lyric_widget._bridge.ready)
        window.update_lyrics()
        if not ready.count():
            assert ready.wait(1000)
        qapp.processEvents()
        assert window.current_line == 0
        assert window.lyric_widget.segments == [{"text": "Hello", "reading": "hi"}]
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
