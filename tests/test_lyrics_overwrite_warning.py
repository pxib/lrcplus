import time

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QLineEdit, QMessageBox, QPushButton, QTableWidget, QWidget

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
CASES += [
    (source, "destination", suffix, action, False)
    for source in ("LRCLIB", *main_window.CHINESE_LYRICS_PROVIDERS, "Musixmatch")
    for suffix in (".lrc", ".lrcx", ".txt")
    for action in ("save_as", "cancel_save_as")
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
    alternate = tmp_path / f"Song alternate{suffix}"
    saves = accept is True or accept == "save_as"
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

    def warn(box):
        assert isinstance(box.parent(), QDialog)
        assert box.windowTitle() == "Overwrite Lyrics"
        assert "Song.mp3" in box.text() and source in box.text()
        assert box.standardButtons() == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        assert box.defaultButton() == box.button(QMessageBox.StandardButton.Cancel)
        assert box.escapeButton() == box.button(QMessageBox.StandardButton.Cancel)
        assert not loaded and not cleared
        if existing_path:
            assert existing_path.read_text(encoding="utf-8") == original
        assert not destination.with_name(destination.name + ".bak").exists()
        warnings.append(box.text())
        if isinstance(accept, str):
            next(button for button in box.buttons() if button.text() == "Save as...").click()
        else:
            box.button(QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.Cancel).click()
        return box.result()

    def choose_path(dialog, title, filename, file_filter):
        assert title == "Save Lyrics As"
        assert filename == str(destination)
        assert file_filter == f"Lyrics (*{suffix})"
        assert existing_path.read_text(encoding="utf-8") == original
        assert not loaded and not cleared
        return (str(alternate.with_suffix("")) if accept == "save_as" else "", file_filter)

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
        if existing != "none" and not saves:
            assert dialog.result() == QDialog.DialogCode.Rejected
            assert use.isEnabled()
        else:
            assert dialog.result() == QDialog.DialogCode.Accepted
        return dialog.result()

    monkeypatch.setattr(QMessageBox, "exec", warn)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_path)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(warnings) == (existing != "none")
        saved_path = alternate if accept == "save_as" else destination
        if saves:
            assert loaded == [str(saved_path)]
            assert cleared == [True]
            assert parent.current_lyrics_path == saved_path
            assert content in saved_path.read_text(encoding="utf-8")
            if existing == "destination" and accept is True:
                assert destination.with_name(destination.name + ".bak").read_text(encoding="utf-8") == original
        else:
            assert not loaded and not cleared
            assert destination.exists() == (existing == "destination")
        if existing_path and (accept is not True or existing != "destination"):
            assert existing_path.read_text(encoding="utf-8") == original
        if isinstance(accept, str):
            assert not destination.with_name(destination.name + ".bak").exists()
            assert alternate.exists() == (accept == "save_as")
    finally:
        parent.deleteLater()
        qapp.processEvents()
