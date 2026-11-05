from pathlib import Path
import threading
import time

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

from core import synclrc
from lyrics.loader import load_lyrics
from ui.lyrics_search_dialog import LyricsSearchDialogMixin


@pytest.mark.parametrize("kind,fetch", [("karaoke", False), ("synced", False), ("plain", False), ("karaoke", True)])
def test_synclrc_preview_cache_and_import(qapp, monkeypatch, tmp_path, kind, fetch):
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Song.mp3"
    loaded, calls = [], []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    fields = {kind: {"karaoke": "[00:01.234]<00:01.334>Hello <00:01.834>world<00:02.134>",
                     "synced": "[00:01.234]Hello world", "plain": "Hello world"}[kind]}
    record = {"id": "a" * 32, "track": "Song", "artist": "Artist", "duration": 186}

    def request(path, params, cancel):
        assert threading.get_ident() != threading.main_thread().ident
        calls.append(path)
        if path == "/search":
            return {"results": [record if fetch else {**record, "lyrics": fields}]}
        assert path == "/lyrics/" + record["id"]
        return {**record, **fields}

    def interact(dialog):
        provider = dialog.findChild(QComboBox)
        assert provider.findText("SyncLRC") >= 0
        provider.setCurrentText("SyncLRC")
        dialog.findChild(QLineEdit).setText("Artist - Song")
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Search").click()
        use = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Use Selected Lyrics")
        deadline = time.monotonic() + 4
        while not use.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert use.isEnabled()
        table = dialog.findChild(QTableWidget)
        assert table.item(0, 3).text() == {"karaoke": "Words", "synced": "Lines", "plain": "Plain"}[kind]
        assert table.item(0, 4).text() == "3:06"
        assert "Hello" in dialog.findChild(QTextEdit).toPlainText()
        provider.setCurrentText("LRCLIB")
        provider.setCurrentText("SyncLRC")
        assert use.isEnabled()
        assert calls == (["/search", "/lyrics/" + record["id"]] if fetch else ["/search"])
        use.click()
        return 1

    monkeypatch.setattr(synclrc, "_request", request)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        LyricsSearchDialogMixin.search_lrclib_lyrics(parent)
        saved, = map(Path, loaded)
        assert saved.suffix == {"karaoke": ".lrcx", "synced": ".lrc", "plain": ".txt"}[kind]
        if kind != "plain":
            line, = load_lyrics(saved)
            assert line.start == 1.234
            assert line.text == "Hello world"
            if kind == "karaoke":
                assert [seg.start for seg in line.segments] == [1.334, 1.834]
                assert line.end == 2.134
        else:
            assert saved.read_text(encoding="utf-8") == "Hello world\n"
    finally:
        parent.deleteLater()
        qapp.processEvents()
