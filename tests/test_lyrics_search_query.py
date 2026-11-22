from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

import ui.lyrics_search_dialog as lyrics_search
from ui.main_window import Player
import core.lrclib as lrclib


def test_lyrics_search_fields_prefer_artist_and_title_metadata(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "misleading-filename.mp3"
    monkeypatch.setattr(
        lyrics_search,
        "read_track",
        lambda _path: SimpleNamespace(
            artist="Tagged Artist",
            title="Tagged Song",
        ),
    )

    assert lyrics_search._lyrics_search_fields(audio_path) == ("Tagged Song", "Tagged Artist")


def test_lyrics_search_fields_fall_back_to_filename_without_tags(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "filename-title.mp3"
    monkeypatch.setattr(
        lyrics_search,
        "read_track",
        lambda _path: SimpleNamespace(artist="", title=audio_path.stem),
    )

    assert lyrics_search._lyrics_search_fields(audio_path) == ("filename-title", "")


def test_lyrics_search_fields_fall_back_when_metadata_cannot_be_read(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "filename-title.mp3"

    def unreadable_track(_path):
        raise OSError("unreadable audio file")

    monkeypatch.setattr(lyrics_search, "read_track", unreadable_track)

    assert lyrics_search._lyrics_search_fields(audio_path) == ("filename-title", "")


def test_combined_paste_and_history_restore_both_fields(qapp, monkeypatch):
    from core.search_history import load_lyrics_search_history, save_search_history

    save_search_history("lyrics", ["Old Artist - Old Song"])
    parent = QWidget()
    parent.current_audio_path = None
    clipboard = qapp.clipboard()
    previous_clipboard = clipboard.text()
    queries = []
    monkeypatch.setattr(lyrics_search, "search_lrclib", lambda query, _cancel: queries.append(query) or [])

    def interact(dialog):
        title = dialog.findChild(QLineEdit, "lyricsSearchTitle")
        artist = dialog.findChild(QLineEdit, "lyricsSearchArtist")
        dialog.show()
        title.setFocus()
        qapp.processEvents()
        QTest.keyClicks(title, "Old")
        qapp.processEvents()
        popup = title.completer().popup()
        assert popup.isVisible()
        popup.setCurrentIndex(popup.model().index(0, 0))
        QTest.keyClick(popup, Qt.Key.Key_Return)
        assert (title.text(), artist.text()) == ("Old Song", "Old Artist")

        clipboard.setText("Pasted Artist - Song - Part 2")
        title.selectAll()
        title.paste()
        assert (title.text(), artist.text()) == ("Song - Part 2", "Pasted Artist")
        artist.clear()
        clipboard.setText("Song - Part 2")
        title.selectAll()
        QTest.keyClicks(title, "Song - Part 2")
        assert (title.text(), artist.text()) == ("Song - Part 2", "")
        title.returnPressed.emit()
        assert load_lyrics_search_history() == [("Song - Part 2", ""), ("Old Song", "Old Artist")]

        deadline = time.monotonic() + 4
        while not queries and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert queries == ["Song - Part 2"]

        model = title.completer().model()
        title._recall(model.index(0, 0).data(Qt.ItemDataRole.UserRole))
        assert (title.text(), artist.text()) == ("Song - Part 2", "")
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
    finally:
        clipboard.setText(previous_clipboard)
        parent.deleteLater()
        qapp.processEvents()


def test_search_preselects_the_closest_recording_and_shows_its_save_path(qapp, monkeypatch, tmp_path):
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Recording.mp3"
    monkeypatch.setattr(lyrics_search, "read_track", lambda path: SimpleNamespace(
        title="Song", artist="Artist", duration=222,
    ))
    monkeypatch.setattr(lyrics_search, "search_lrclib", lambda *args: [
        {"trackName": "Song (Live)", "artistName": "Artist", "duration": 222, "plainLyrics": "Live lyrics"},
        {"trackName": "Song", "artistName": "Artist", "duration": 220, "syncedLyrics": "[00:01]Similar lyrics"},
        {"trackName": "Ｓｏｎｇ", "artistName": "ARTIST", "duration": 222, "syncedLyrics": "[00:02]Studio lyrics"},
        {"trackName": "Song", "artistName": "Other Artist", "duration": 222, "plainLyrics": "Cover lyrics"},
        {"trackName": "Song", "artistName": "Artist", "syncedLyrics": "[00:01]Unknown duration"},
    ])

    def interact(dialog):
        track = dialog.findChild(QLabel, "lyricsSearchTrack")
        assert track.text() == "Matching lyrics for Song · Artist · 3:42"
        preview = dialog.findChild(QTextEdit)
        assert preview.placeholderText() == "Search for this track to see matching lyrics."
        table = dialog.findChild(QTableWidget)
        title = dialog.findChild(QLineEdit, "lyricsSearchTitle")
        artist = dialog.findChild(QLineEdit, "lyricsSearchArtist")
        assert (title.text(), artist.text()) == ("Song", "Artist")
        artist.returnPressed.emit()
        deadline = time.monotonic() + 4
        while table.rowCount() != 5 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert table.currentRow() == 2
        assert [table.item(row, 5).text() for row in range(5)] == [
            "", "Close match", "Close match", "", "",
        ]
        assert preview.toPlainText() == "[00:02]Studio lyrics"
        destination = dialog.findChild(QLabel, "lyricsSearchDestination")
        assert destination.text() == "Save to Recording.lrc"
        table.selectRow(0)
        assert destination.text() == "Save to Recording.txt"

        # The wizard can leave this dialog open while playback changes tracks.
        parent.current_audio_path = tmp_path / "Different.mp3"
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        use.click()
        assert not use.isEnabled()
        assert not list(tmp_path.glob("*.txt"))
        assert any("The track changed" in label.text() for label in dialog.findChildren(QLabel))
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
    finally:
        parent.deleteLater()


def test_lrclib_lyricsfile_search_saves_and_loads_word_timing(qapp, monkeypatch, tmp_path):
    from core.settings import get_lyrics_attachment
    from lyrics.loader import load_lyrics

    source = """\
version: '1.0'
metadata: {title: Song, artist: Artist}
lines:
  - text: Go now
    start_ms: 1000
    words:
      - {text: 'Go ', start_ms: 1000}
      - {text: now, start_ms: 1400}
"""
    parent = QWidget()
    audio = tmp_path / "Song.mp3"
    destination = audio.with_suffix(".lrcx")
    parent.current_audio_path = audio
    parent.clear_lyrics_search_index = lambda: None
    loaded = []
    parent.load_lrc_file = lambda path: loaded.extend(load_lyrics(path))
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs: SimpleNamespace(
        status_code=200, raise_for_status=lambda: None, close=lambda: None,
        json=lambda: [{"trackName": "Song", "artistName": "Artist", "lyricsfile": source}],
    ))

    def interact(dialog):
        table = dialog.findChild(QTableWidget)
        dialog.findChild(QLineEdit, "lyricsSearchTitle").setText("Song")
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        buttons["Search"].click()
        deadline = time.monotonic() + 4
        while not table.rowCount() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert table.rowCount() == 1
        assert table.item(0, 3).text() == "Word-synced"
        assert dialog.findChild(QLabel, "lyricsSearchDestination").text() == "Save to Song.lrcx"
        buttons["Use Selected Lyrics"].click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
        assert parent.current_lyrics_path == destination
        assert get_lyrics_attachment(audio) == str(destination)
        line, = loaded
        assert line.has_karaoke
        assert [(word.text, word.start) for word in line.segments] == [("Go ", 1.0), ("now", 1.4)]
    finally:
        parent.deleteLater()


@pytest.mark.parametrize("outcome", ["results", "unknown", "empty", "error", "cancel"])
def test_lrclib_search_does_not_block_gui(qapp, monkeypatch, outcome):
    entered, release = threading.Event(), threading.Event()
    now = [100.0]
    monkeypatch.setattr(lyrics_search, "monotonic", lambda: now[0])
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
            status_code=200, headers={}, close=lambda: None,
            raise_for_status=lambda: None,
            json=lambda: [] if outcome == "empty" else [
                {
                    "trackName": "Song",
                    "syncedLyrics": "[00:01]Hello" if outcome == "results" else "",
                    "hasSyncedLyrics": None if outcome == "unknown" else True,
                }
            ],
        )

    def interact(dialog):
        query = dialog.findChild(QLineEdit)
        query.setText("Song")
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Search")
        table = dialog.findChild(QTableWidget)
        loading = dialog.findChild(QLabel, "lyricsSearchLoading")
        result = dialog.findChild(QLabel, "lyricsSearchResult")
        button.click()
        from core.search_history import load_lyrics_search_history
        assert load_lyrics_search_history() == [("Song", "")]
        assert entered.wait(2)
        assert not loading.isHidden()
        assert loading.movie().state() == loading.movie().MovieState.Running
        assert result.isHidden()
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
            label.text() == "1 result"
            or label.text().startswith("No lyrics found.")
            or "Search failed:" in label.text()
            for label in dialog.findChildren(QLabel)
        ) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not button.isEnabled()
        action = "Retry" if outcome == "error" else "Search"
        assert button.text() == f"{action} (5s)"
        assert loading.isHidden()
        query.returnPressed.emit()
        assert len(worker_threads) == 1
        if outcome in {"results", "unknown", "empty"}:
            assert not result.isHidden()
            assert result.accessibleName() == (
                "No lyrics found" if outcome == "empty" else "Lyrics found"
            )
            if outcome in {"results", "unknown"}:
                assert table.rowCount() == 1
                assert table.item(0, 0).text() == "Song"
                timing = table.item(0, 3)
                if outcome == "unknown":
                    assert timing.text() == "Unknown"
                    assert not timing.icon().isNull()
                    assert timing.toolTip() == "Timing information is unknown."
                else:
                    assert timing.text() == "Line-synced"
            else:
                assert table.rowCount() == 0
        else:
            assert not result.isHidden()
            assert result.accessibleName() == "Lyrics search error"
            assert any("Search failed: Network unavailable" in label.text()
                       for label in dialog.findChildren(QLabel))
        now[0] += 4.9
        query.returnPressed.emit()
        assert not button.isEnabled()
        assert button.text() == f"{action} (1s)"
        assert len(worker_threads) == 1
        now[0] += 0.1
        deadline = time.monotonic() + 2
        while not button.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert button.isEnabled()
        assert button.text() == action
        return 0

    monkeypatch.setattr(lrclib.requests, "get", get)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
        assert len(worker_threads) == 1
    finally:
        release.set()
        parent.deleteLater()
        qapp.processEvents()
