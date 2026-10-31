import base64
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

from core import chinese_lyrics
import ui.main_window as main_window


@pytest.mark.parametrize("source", ("NetEase Cloud Music", "QQ Music"))
@pytest.mark.parametrize("outcome", ["synced", "plain", "search_error", "preview_error", "cancel_search", "cancel_preview", "switch", "reselect"])
def test_anonymous_provider_search_preview_and_save(qapp, monkeypatch, tmp_path, source, outcome):
    entered_search, entered_preview, release = threading.Event(), threading.Event(), threading.Event()
    calls = []
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Song.mp3"
    loaded = []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    monkeypatch.setenv("MUSIXMATCH_TOKEN", "private-musixmatch-token")

    def request(url, **kwargs):
        assert threading.get_ident() != threading.main_thread().ident
        assert "private-musixmatch-token" not in json.dumps(kwargs)
        assert url.startswith("https://music.163.com/" if source == "NetEase Cloud Music" else "https://c.y.qq.com/")
        is_search = url.endswith("/search/get") or url.endswith("smartbox_new.fcg")
        calls.append((url, kwargs))
        if is_search:
            entered_search.set()
            if outcome == "cancel_search":
                assert release.wait(5)
            if source == "NetEase Cloud Music":
                payload = {"code": 200, "result": {"songs": [
                    {"id": number, "name": title, "artists": [{"name": "歌手"}]} for number, title in [(42, "歌詞 A"), (43, "歌詞 B")]
                ]}}
            else:
                payload = {"code": 0, "data": {"song": {"itemlist": [
                    {"mid": f"song{number}", "name": title, "singer": "歌手"} for number, title in [(42, "歌詞 A"), (43, "歌詞 B")]
                ]}}}
        else:
            entered_preview.set()
            assert release.wait(5)
            second = str(kwargs["params"].get("id") or kwargs["params"].get("songmid")).endswith("43")
            text = ("" if outcome == "plain" else "[00:01.00]") + ("歌詞 B" if second else "歌詞 A")
            text = "[ti:Provider title]\n[ar:歌手]\n[00:00.00]作词：Credit name\n" + text
            payload = ({"code": 200, "lrc": {"lyric": text}} if source == "NetEase Cloud Music" else
                       {"code": 0, "lyric": base64.b64encode(text.encode("utf-8")).decode("ascii")})
        failed = (outcome == "search_error" and is_search) or (outcome == "preview_error" and not is_search)
        return SimpleNamespace(status_code=503 if failed else 200, headers={}, close=lambda: None, json=lambda: payload)

    def wait_for(predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert predicate()

    def interact(dialog):
        provider = dialog.findChild(QComboBox)
        provider.setCurrentText(source)
        # The Musixmatch credential controls stay hidden for anonymous sources.
        credential = next(field for field in dialog.findChildren(QLineEdit)
                          if field.accessibleName() == "Musixmatch authentication token")
        assert credential.isHidden() or credential.parentWidget().isHidden()
        query = dialog.findChild(QLineEdit)
        query.setText("歌手 - 歌詞")
        search = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search")
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        table, preview = dialog.findChild(QTableWidget), dialog.findChild(QTextEdit)
        search.click()
        wait_for(entered_search.is_set)
        if outcome == "cancel_search":
            assert not use.isEnabled()
            dialog.reject()
            return 0
        if outcome == "search_error":
            wait_for(lambda: any("HTTP 503" in label.text() for label in dialog.findChildren(QLabel)))
            assert table.rowCount() == 0
            assert not use.isEnabled()
            return 0
        wait_for(entered_preview.is_set)
        assert table.rowCount() == 2
        assert table.item(0, 3).text() == "Unknown"
        assert not use.isEnabled()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks
        if outcome == "cancel_preview":
            dialog.reject()
            return 0
        if outcome == "switch":
            provider.setCurrentText("LRCLIB")
            release.set()
            drained = []
            QTimer.singleShot(100, lambda: drained.append(True))
            wait_for(lambda: drained)
            assert table.rowCount() == 0
            assert not preview.toPlainText()
            assert not use.isEnabled()
            return 0
        if outcome == "reselect":
            table.selectRow(1)
        release.set()
        if outcome == "preview_error":
            wait_for(lambda: "HTTP 503" in preview.toPlainText())
            assert not use.isEnabled()
            return 0
        wait_for(use.isEnabled)
        assert ("歌詞 B" if outcome == "reselect" else "歌詞 A") in preview.toPlainText()
        assert "[ti:" not in preview.toPlainText()
        assert "Credit name" not in preview.toPlainText()
        assert table.item(table.currentRow(), 3).text() == ("Plain" if outcome == "plain" else "Lines")
        assert any(f"Lyrics provided by {source}" in label.text() for label in dialog.findChildren(QLabel))
        use.click()
        return 1

    monkeypatch.setattr(chinese_lyrics.requests, "get", request)
    monkeypatch.setattr(chinese_lyrics.requests, "post", request)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        if outcome in {"synced", "plain", "reselect"}:
            assert len(loaded) == 1
            destination = Path(loaded[0])
            assert destination.suffix == (".txt" if outcome == "plain" else ".lrc")
            content = destination.read_text(encoding="utf-8")
            assert ("歌詞 B" if outcome == "reselect" else "歌詞 A") in content
            assert "Lyrics provided by" not in content
            assert "[re:" not in content
            assert "[ti:" not in content
            assert "Credit name" not in content
        else:
            assert not loaded
    finally:
        release.set()
        parent.deleteLater()
        qapp.processEvents()
