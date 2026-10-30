from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QPushButton, QTableWidget, QWidget

import ui.main_window as main_window


def test_lyrics_search_query_prefers_artist_and_title_metadata(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "misleading-filename.mp3"
    monkeypatch.setattr(
        main_window,
        "read_track",
        lambda _path: SimpleNamespace(
            artist="Tagged Artist",
            title="Tagged Song",
        ),
    )

    assert main_window._lyrics_search_query(audio_path) == (
        "Tagged Artist - Tagged Song"
    )


def test_lyrics_search_query_falls_back_to_filename_without_tags(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "filename-title.mp3"
    monkeypatch.setattr(
        main_window,
        "read_track",
        lambda _path: SimpleNamespace(artist="", title=audio_path.stem),
    )

    assert main_window._lyrics_search_query(audio_path) == "filename-title"


def test_lyrics_search_query_falls_back_when_metadata_cannot_be_read(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "filename-title.mp3"

    def unreadable_track(_path):
        raise OSError("unreadable audio file")

    monkeypatch.setattr(main_window, "read_track", unreadable_track)

    assert main_window._lyrics_search_query(audio_path) == "filename-title"


@pytest.mark.parametrize("outcome", ["results", "error", "cancel"])
def test_lrclib_search_does_not_block_gui(qapp, monkeypatch, outcome):
    entered, release = threading.Event(), threading.Event()
    now = [100.0]
    monkeypatch.setattr(main_window, "monotonic", lambda: now[0])
    worker_threads = []
    parent = QWidget()
    parent.current_audio_path = None

    def get(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        if outcome == "error":
            raise OSError("Network unavailable")
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: [{"trackName": "Song", "syncedLyrics": "[00:01]Hello"}],
        )

    def interact(dialog):
        query = dialog.findChild(QLineEdit)
        query.setText("Song")
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Search")
        table = dialog.findChild(QTableWidget)
        button.click()
        assert entered.wait(2)
        assert len(worker_threads) == 1
        assert worker_threads[0] != threading.get_ident()
        assert not button.isEnabled()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks
        if outcome == "cancel":
            # Closing a dialog must return even if its HTTP call is still busy.
            dialog.reject()
            return 0
        release.set()
        deadline = time.monotonic() + 5
        while not any(
            label.text() == "1 result(s). Select one to inspect the lyrics."
            or "Search failed:" in label.text()
            for label in dialog.findChildren(QLabel)
        ) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not button.isEnabled()
        assert button.text() == "Search (5s)"
        query.returnPressed.emit()
        assert len(worker_threads) == 1
        if outcome == "results":
            assert table.rowCount() == 1
            assert table.item(0, 0).text() == "Song"
        else:
            assert any("Search failed: Network unavailable" in label.text()
                       for label in dialog.findChildren(QLabel))
        now[0] += 4.9
        query.returnPressed.emit()
        assert not button.isEnabled()
        assert button.text() == "Search (1s)"
        assert len(worker_threads) == 1
        now[0] += 0.1
        deadline = time.monotonic() + 2
        while not button.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert button.isEnabled()
        assert button.text() == "Search"
        return 0

    monkeypatch.setattr(main_window.requests, "get", get)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(worker_threads) == 1
    finally:
        release.set()
        parent.deleteLater()
        qapp.processEvents()
