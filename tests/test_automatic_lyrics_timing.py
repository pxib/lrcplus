import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

from core.chinese_lyrics import LyricsProviderError
import core.lyrics_preview_queue as queue_module
import ui.lyrics_search_dialog as lyrics_search
from ui.main_window import Player


def wait_for(qapp, predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert predicate()


def setup_search(monkeypatch):
    monkeypatch.setattr(lyrics_search, "search_chinese_lyrics", lambda source, *args: [
        {"id": number, "source": source, "trackName": f"Song {number}", "hasSyncedLyrics": None}
        for number in range(5)
    ])


def controls(dialog):
    provider = dialog.findChild(QComboBox)
    provider.setCurrentText("NetEase Cloud Music")
    dialog.findChild(QLineEdit).setText("Artist - Song")
    next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
    return provider, dialog.findChild(QTableWidget), dialog.findChild(QTextEdit)


def test_all_rows_are_checked_without_clicks_and_failures_do_not_replace_preview(qapp, monkeypatch):
    parent = QWidget()
    parent.current_audio_path = None
    setup_search(monkeypatch)
    calls = []
    def fetch(item, cancel):
        assert threading.get_ident() != threading.main_thread().ident
        calls.append(item["id"])
        if item["id"] == 2:
            raise LyricsProviderError("Kuwo's lyric service could not provide this result (code 301).", status_code=301)
        if item["id"] == 3:
            return {**item, "plainLyrics": "Plain lyrics"}
        return {**item, "syncedLyrics": f"[00:01.000]Song {item['id']}", "lyricsFormat": "lrcx" if item["id"] == 1 else "lrc"}
    monkeypatch.setattr(lyrics_search, "fetch_chinese_lyrics", fetch)

    def interact(dialog):
        provider, table, preview = controls(dialog)
        expected = ["Lines", "Words", "Unavailable", "Plain", "Lines"]
        wait_for(qapp, lambda: table.rowCount() == 5 and [table.item(row, 3).text() for row in range(5)] == expected)
        assert table.currentRow() == 0
        assert preview.toPlainText() == "[00:01.000]Song 0"
        assert "code 301" in table.item(2, 3).toolTip()
        assert sorted(calls) == list(range(5))
        table.selectRow(1)
        assert preview.toPlainText() == "[00:01.000]Song 1"
        table.selectRow(2)
        assert "code 301" in preview.toPlainText()
        provider.setCurrentText("QQ Music")
        provider.setCurrentText("NetEase Cloud Music")
        assert [table.item(row, 3).text() for row in range(5)] == expected
        assert len(calls) == 5
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
    finally:
        parent.deleteLater()
        qapp.processEvents()


def test_selected_row_is_prioritized_and_dialog_closes_with_requests_in_flight(qapp, monkeypatch):
    parent = QWidget()
    parent.current_audio_path = None
    setup_search(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(item, cancel):
        calls.append(item["id"])
        if item["id"] == 0:
            entered.set()
            assert release.wait(5)
        return {**item, "syncedLyrics": f"[00:01]Song {item['id']}"}
    monkeypatch.setattr(lyrics_search, "fetch_chinese_lyrics", fetch)

    def interact(dialog):
        _, table, preview = controls(dialog)
        wait_for(qapp, entered.is_set)
        table.selectRow(4)
        wait_for(qapp, lambda: preview.toPlainText() == "[00:01]Song 4")
        assert calls[:2] == [0, 4]
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks
        dialog.reject()
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
        assert not release.is_set()
    finally:
        release.set()
        parent.deleteLater()
        qapp.processEvents()


def test_provider_backoff_pauses_queued_timing_checks(qapp, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(queue_module, "monotonic", lambda: now[0])
    parent = QWidget()
    queue = queue_module.LyricsPreviewQueue(parent)
    calls = []
    def fetch(item, cancel):
        calls.append(item["id"])
        if item["id"] == 0:
            raise LyricsProviderError("Rate limited", retry_after=60)
        return item
    received = []
    def finished(item, result):
        received.append(item["id"])
        if isinstance(result, LyricsProviderError):
            queue.pause(result.retry_after)
    queue.finished.connect(finished)
    try:
        queue.start(fetch, [{"id": 0}, {"id": 1}])
        wait_for(qapp, lambda: received == [0])
        now[0] = 1
        tick = []
        QTimer.singleShot(100, lambda: tick.append(True))
        wait_for(qapp, lambda: tick)
        assert calls == [0]
        now[0] = 61
        wait_for(qapp, lambda: received == [0, 1])
    finally:
        queue.close()
        parent.deleteLater()
        qapp.processEvents()
