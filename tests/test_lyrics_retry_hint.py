import time

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QTextEdit, QWidget

import ui.lyrics_search_dialog as lyrics_search
from ui.main_window import Player


@pytest.mark.parametrize("source", ["LRCLIB", *lyrics_search.CHINESE_LYRICS_PROVIDERS, "Musixmatch"])
@pytest.mark.parametrize("failure", ["service", "network", "empty", "lyrics"])
def test_search_offers_recovery_for_the_failure(qapp, monkeypatch, source, failure):
    parent = QWidget()
    parent.current_audio_path = None
    now = [100.0]
    searches = []
    monkeypatch.setattr(lyrics_search, "monotonic", lambda: now[0])
    monkeypatch.setenv("MUSIXMATCH_TOKEN", "manual-token")
    error_class = (lyrics_search.LRCLibError if source == "LRCLIB" else
                   lyrics_search.MusixmatchError if source == "Musixmatch" else lyrics_search.LyricsProviderError)

    def search(*args, **kwargs):
        searches.append(args[1] if source in lyrics_search.CHINESE_LYRICS_PROVIDERS else args[0])
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
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        buttons["Search"].click()
        expected = {
            "service": "Provider unavailable",
            "network": "Network unavailable",
            "empty": "No lyrics found.",
            "lyrics": "Try another result.",
        }[failure]
        def has_message():
            return (expected in dialog.findChild(QTextEdit).toPlainText()
                    or any(expected in label.text() for label in dialog.findChildren(QLabel)))
        deadline = time.monotonic() + 4
        while not has_message() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert has_message()
        assert query.text() == "Artist - Song"
        title_only = buttons["Search title only"]
        assert title_only.isHidden() == (failure != "empty")
        assert buttons["Try another source"].isHidden() == (failure == "lyrics")
        if failure == "empty":
            assert not title_only.isEnabled()
            now[0] += 5
            deadline = time.monotonic() + 4
            while not title_only.isEnabled() and time.monotonic() < deadline:
                qapp.processEvents()
                time.sleep(.005)
            assert title_only.isEnabled()
            title_only.click()
            deadline = time.monotonic() + 4
            while len(searches) != 2 and time.monotonic() < deadline:
                qapp.processEvents()
                time.sleep(.005)
            assert searches == ["Artist - Song", "Song"]
            assert query.text() == "Song"
        elif failure in {"network", "service"}:
            assert buttons["Search"].text() == "Retry (5s)"
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        Player.search_lrclib_lyrics(parent)
    finally:
        parent.deleteLater()
        qapp.processEvents()
