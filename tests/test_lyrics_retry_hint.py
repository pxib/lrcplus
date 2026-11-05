import time

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QTextEdit, QWidget

import ui.lyrics_search_dialog as lyrics_search
from ui.main_window import Player


@pytest.mark.parametrize("source", ["LRCLIB", *lyrics_search.CHINESE_LYRICS_PROVIDERS, "Musixmatch"])
@pytest.mark.parametrize("failure", ["service", "network", "empty", "lyrics"])
def test_every_provider_failure_suggests_searching_with_title_only(qapp, monkeypatch, source, failure):
    parent = QWidget()
    parent.current_audio_path = None
    monkeypatch.setenv("MUSIXMATCH_TOKEN", "manual-token")
    error_class = (lyrics_search.LRCLibError if source == "LRCLIB" else
                   lyrics_search.MusixmatchError if source == "Musixmatch" else lyrics_search.LyricsProviderError)

    def search(*args, **kwargs):
        if failure == "service":
            raise error_class("Provider unavailable")
        if failure == "network":
            raise OSError("Network unavailable")
        if failure == "empty":
            return []
        return [{"id": 42, "source": source, "trackName": "Song"}]

    def fetch(*args, **kwargs):
        raise error_class("No lyrics available")

    for name in ["search_lrclib", "search_chinese_lyrics", "search_musixmatch"]:
        monkeypatch.setattr(lyrics_search, name, search)
    for name in ["fetch_chinese_lyrics", "fetch_musixmatch_lyrics"]:
        monkeypatch.setattr(lyrics_search, name, fetch)

    def interact(dialog):
        dialog.findChild(QComboBox).setCurrentText(source)
        query = dialog.findChild(QLineEdit)
        query.setText("Artist - Song")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        expected = "Try removing the artist name and searching with just the song title."
        def has_hint():
            return (expected in dialog.findChild(QTextEdit).toPlainText()
                    or any(expected in label.text() for label in dialog.findChildren(QLabel)))
        deadline = time.monotonic() + 4
        while not has_hint() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert has_hint()
        assert query.text() == "Artist - Song"
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
    finally:
        parent.deleteLater()
        qapp.processEvents()
