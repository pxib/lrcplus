import base64
from pathlib import Path
import threading
import time
import zlib

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

from core import chinese_lyrics
from lyrics.loader import load_lyrics
import ui.main_window as main_window


@pytest.mark.parametrize("source, word_timed", [("NetEase Cloud Music", True), ("NetEase Cloud Music", False), ("KuGou", True), ("Kuwo", False)])
def test_native_provider_preview_cache_and_save(qapp, monkeypatch, tmp_path, source, word_timed):
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Song.mp3"
    loaded, calls = [], []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    native_text = '[ti:Song]\n[ar:Artist]\n[0,1000]<0,1000,0>作词：Credit\n[1234,2000]<100,250,0>Hello <600,300,0>world'
    compressed = zlib.compress(native_text.encode())
    key = b"@Gaw^2tGQ61-\xce\xd2ni"
    encoded = base64.b64encode(b"krc1" + bytes(value ^ key[i % 16] for i, value in enumerate(compressed))).decode()

    def request(request_source, url, cancel, **kwargs):
        assert threading.get_ident() != threading.main_thread().ident
        assert request_source == source
        calls.append(url)
        if url.endswith("/search/get"):
            return {"code": 200, "result": {"songs": [{"id": 42, "name": "Song", "artists": [{"name": "Artist"}], "duration": 323400}]}}
        if url.endswith("/lyric/v1"):
            return {"code": 200, "yrc": {"lyric": '[0,1000](0,1000,0)作词：Credit\n[1234,2000](1334,250,0)Hello (1834,300,0)world' if word_timed else ""},
                    "lrc": {"lyric": "[00:00.000]作词：Credit\n[00:01.234]Hello world"}}
        if url.endswith("/search"):
            return {"status": 200, "candidates": [{"id": "42", "accesskey": "candidate-key", "song": "Song", "singer": "Artist", "duration": 323400}]}
        if url.endswith("/download"):
            return {"status": 200, "content": encoded}
        if url.endswith("searchMusicBykeyWord"):
            return {"abslist": [{"DC_TARGETID": "42", "NAME": "Song", "ARTIST": "Artist", "DURATION": "323.4"}]}
        if url.endswith("songinfoandlrc"):
            return {"status": 200, "data": {"lrclist": [{"time": "0", "lineLyric": "作词：Credit"}, {"time": "1.234", "lineLyric": "Hello world"}, {"time": "1.234", "lineLyric": "Translated line"}]}}
        raise AssertionError(url)

    def interact(dialog):
        provider = dialog.findChild(QComboBox)
        provider.setCurrentText(source)
        dialog.findChild(QLineEdit).setText("Artist - Song")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        table, preview = dialog.findChild(QTableWidget), dialog.findChild(QTextEdit)
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        deadline = time.monotonic() + 4
        while not use.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert use.isEnabled()
        assert table.item(0, 3).text() == ("Words" if word_timed else "Lines")
        assert table.item(0, 4).text() == "5:23"
        assert "Credit" not in preview.toPlainText()
        assert "[ti:" not in preview.toPlainText()
        if source == "Kuwo":
            assert any("translated lines" in label.text() for label in dialog.findChildren(QLabel))
        before = len(calls)
        provider.setCurrentText("LRCLIB")
        provider.setCurrentText(source)
        assert table.item(0, 3).text() == ("Words" if word_timed else "Lines")
        assert len(calls) == before
        assert use.isEnabled()
        use.click()
        return 1

    monkeypatch.setattr(chinese_lyrics, "_request", request)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(loaded) == 1
        path = Path(loaded[0])
        assert path.suffix == (".lrcx" if word_timed else ".lrc")
        content = path.read_text(encoding="utf-8")
        assert "Credit" not in content
        assert "candidate-key" not in content
        assert "Lyrics provided by" not in content
        lines = load_lyrics(path)
        assert lines[0].text == "Hello world"
        assert lines[0].start == 1.234
        if word_timed:
            assert lines[0].end == 3.234
            assert [(word.start, word.end) for word in lines[0].segments] == [(1.334, 1.584), (1.834, 2.134)]
        elif source == "Kuwo":
            assert [line.text for line in lines] == ["Hello world", "Translated line"]
    finally:
        parent.deleteLater()
        qapp.processEvents()
