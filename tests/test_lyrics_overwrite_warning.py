import time

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QMessageBox, QPushButton, QTableWidget, QWidget

import ui.main_window as main_window


CASES = [
    (source, existing, ".lrc", accept, False)
    for source in ("LRCLIB", *main_window.CHINESE_LYRICS_PROVIDERS, "Musixmatch")
    for existing, accept in (("none", True), ("destination", False), ("destination", True))
] + [
    ("LRCLIB", existing, suffix, accept, True)
    for existing, suffix in (("other_format", ".txt"), ("other_format", ".lrcx"),
                             ("attached", ".lrc"), ("loaded", ".lrc"))
    for accept in (False, True)
]


@pytest.mark.parametrize("source,existing,suffix,accept,double_click", CASES)
def test_provider_lyrics_require_confirmation_before_replacing_existing_lyrics(
    qapp, monkeypatch, tmp_path, source, existing, suffix, accept, double_click,
):
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Song.mp3"
    parent.current_lyrics_path = None
    parent.lyric_lines = []
    destination = parent.current_audio_path.with_suffix(suffix)
    original = "[00:01]Original lyrics\n"
    existing_path = None
    if existing == "destination":
        existing_path = destination
    elif existing == "other_format":
        existing_path = parent.current_audio_path.with_suffix(".lrc")
    elif existing == "attached":
        existing_path = tmp_path / "custom-lyrics.lrcx"
        parent.current_lyrics_path = existing_path
    elif existing == "loaded":
        parent.lyric_lines = [object()]
    if existing_path:
        existing_path.write_text(original, encoding="utf-8")

    loaded, cleared, warnings = [], [], []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: cleared.append(True)
    content = "New lyrics" if suffix == ".txt" else "[00:01]New lyrics"
    item = {"id": 42, "source": source, "trackName": "Song",
            "plainLyrics" if suffix == ".txt" else "syncedLyrics": content}
    if suffix == ".lrcx":
        item["lyricsFormat"] = "lrcx"
    for name in ("search_lrclib", "search_chinese_lyrics", "search_musixmatch"):
        monkeypatch.setattr(main_window, name, lambda *args, **kwargs: [dict(item)])
    monkeypatch.setenv("MUSIXMATCH_TOKEN", "test-token")

    def warn(dialog, title, text, buttons, default):
        assert isinstance(dialog, QDialog)
        assert title == "Overwrite Lyrics"
        assert "Song.mp3" in text and source in text
        assert buttons == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        assert default == QMessageBox.StandardButton.Cancel
        assert not loaded and not cleared
        if existing_path:
            assert existing_path.read_text(encoding="utf-8") == original
        assert not destination.with_name(destination.name + ".bak").exists()
        warnings.append(text)
        return QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.Cancel

    def interact(dialog):
        dialog.findChild(QComboBox).setCurrentText(source)
        dialog.findChild(QLineEdit).setText("Song")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        deadline = time.monotonic() + 4
        while not use.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert use.isEnabled()
        if double_click:
            table = dialog.findChild(QTableWidget)
            table.itemDoubleClicked.emit(table.item(0, 0))
        else:
            use.click()
        if existing != "none" and not accept:
            assert dialog.result() == QDialog.DialogCode.Rejected
            assert use.isEnabled()
        else:
            assert dialog.result() == QDialog.DialogCode.Accepted
        return dialog.result()

    monkeypatch.setattr(QMessageBox, "warning", warn)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(warnings) == (existing != "none")
        if accept:
            assert loaded == [str(destination)]
            assert cleared == [True]
            assert parent.current_lyrics_path == destination
            assert content in destination.read_text(encoding="utf-8")
            if existing == "destination":
                assert destination.with_name(destination.name + ".bak").read_text(encoding="utf-8") == original
        else:
            assert not loaded and not cleared
            assert destination.exists() == (existing == "destination")
        if existing_path and (not accept or existing != "destination"):
            assert existing_path.read_text(encoding="utf-8") == original
    finally:
        parent.deleteLater()
        qapp.processEvents()
