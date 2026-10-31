import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

import ui.main_window as main_window


def wait_for(qapp, predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_results_queries_selection_and_previews_last_only_for_dialog(qapp, monkeypatch):
    parent = QWidget()
    parent.current_audio_path = None
    monkeypatch.setenv("MUSIXMATCH_TOKEN", "test-token")
    searches, fetches = [], []
    sources = ["LRCLIB", *main_window.CHINESE_LYRICS_PROVIDERS, "Musixmatch"]

    def search(source, query):
        searches.append((source, query))
        return [{"id": number, "source": source, "trackName": f"{source} {number}",
                 **({"syncedLyrics": f"[00:01.00]{source} {number}"} if source == "LRCLIB" else {})}
                for number in [42, 43]]

    def fetch(item):
        fetches.append((item["source"], item["id"]))
        return {**item, "syncedLyrics": f"[00:01.00]{item['source']} {item['id']}"}

    monkeypatch.setattr(main_window, "search_lrclib", lambda query, cancel: search("LRCLIB", query))
    monkeypatch.setattr(main_window, "search_chinese_lyrics", lambda source, query, cancel: search(source, query))
    monkeypatch.setattr(main_window, "search_musixmatch", lambda query, key, cancel, **kwargs: search("Musixmatch", query))
    monkeypatch.setattr(main_window, "fetch_chinese_lyrics", lambda item, cancel: fetch(item))
    monkeypatch.setattr(main_window, "fetch_musixmatch_lyrics", lambda item, key, cancel, **kwargs: fetch(item))

    def interact(dialog):
        provider = dialog.findChild(QComboBox)
        query = dialog.findChild(QLineEdit)
        table, preview = dialog.findChild(QTableWidget), dialog.findChild(QTextEdit)
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        for source in sources:
            provider.setCurrentText(source)
            assert table.rowCount() == 0
            query.setText(f"query for {source}")
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
            wait_for(qapp, lambda: table.rowCount() == 2 and use.isEnabled())
            table.selectRow(1)
            wait_for(qapp, lambda: use.isEnabled() and preview.toPlainText() == f"[00:01.00]{source} 43")
        before = (len(searches), len(fetches))
        for source in sources:
            provider.setCurrentText(source)
            assert query.text() == f"query for {source}"
            assert table.rowCount() == 2
            assert table.currentRow() == 1
            assert table.item(1, 0).text() == f"{source} 43"
            assert preview.toPlainText() == f"[00:01.00]{source} 43"
            assert use.isEnabled()
        assert (len(searches), len(fetches)) == before
        # Changing credentials invalidates Musixmatch's results alone.
        auth = next(combo for combo in dialog.findChildren(QComboBox)
                    if combo.accessibleName() == "Musixmatch authentication method")
        auth.setCurrentIndex(auth.findData("api_key"))
        assert table.rowCount() == 0
        provider.setCurrentText("NetEase Cloud Music")
        assert table.rowCount() == 2
        provider.setCurrentText("Musixmatch")
        assert table.rowCount() == 0
        return 0

    def fresh_dialog(dialog):
        provider = dialog.findChild(QComboBox)
        for source in sources:
            provider.setCurrentText(source)
            assert dialog.findChild(QTableWidget).rowCount() == 0
            assert not dialog.findChild(QTextEdit).toPlainText()
        return 0

    try:
        monkeypatch.setattr(QDialog, "exec", interact)
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(searches) == len(sources)
        assert len(fetches) == 2 * (len(sources) - 1)
        monkeypatch.setattr(QDialog, "exec", fresh_dialog)
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(searches) == len(sources)
    finally:
        parent.deleteLater()
        qapp.processEvents()


def test_cancelled_search_cannot_overwrite_restored_results(qapp, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    parent = QWidget()
    parent.current_audio_path = None
    monkeypatch.setattr(main_window, "search_lrclib", lambda *args: [
        {"id": 42, "trackName": "Cached song", "syncedLyrics": "[00:01]Cached lyrics"},
    ])

    def blocked_search(*args):
        entered.set()
        assert release.wait(5)
        return [{"id": 99, "source": "QQ Music", "trackName": "Late song"}]
    monkeypatch.setattr(main_window, "search_chinese_lyrics", blocked_search)

    def interact(dialog):
        provider, query = dialog.findChild(QComboBox), dialog.findChild(QLineEdit)
        table, preview = dialog.findChild(QTableWidget), dialog.findChild(QTextEdit)
        query.setText("Cached query")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        wait_for(qapp, lambda: table.rowCount() == 1)
        provider.setCurrentText("QQ Music")
        query.setText("Slow query")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        wait_for(qapp, entered.is_set)
        provider.setCurrentText("LRCLIB")
        release.set()
        drained = []
        QTimer.singleShot(100, lambda: drained.append(True))
        wait_for(qapp, lambda: drained)
        assert table.item(0, 0).text() == "Cached song"
        assert preview.toPlainText() == "[00:01]Cached lyrics"
        assert query.text() == "Cached query"
        provider.setCurrentText("QQ Music")
        assert table.rowCount() == 0
        assert query.text() == "Slow query"
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
    finally:
        release.set()
        parent.deleteLater()
        qapp.processEvents()
