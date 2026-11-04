from pathlib import Path
import time

import pytest
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QLineEdit, QPushButton, QTabBar, QTextEdit, QWidget

from core.lyrics_translations import with_translations
from lyrics.lrcx_parser import load_lrcx
import ui.main_window as main_window


@pytest.mark.parametrize("include", [False, True])
@pytest.mark.parametrize("word_timed", [False, True])
def test_translation_preview_toggle_cache_and_saved_format(qapp, monkeypatch, tmp_path, include, word_timed):
    parent = QWidget()
    parent.current_audio_path = tmp_path / "song.mp3"
    loaded, fetches = [], []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    monkeypatch.setattr(main_window, "search_chinese_lyrics", lambda source, *args: [
        {"id": 42, "source": source, "trackName": "Original"},
    ])

    def fetch(item, cancel):
        fetches.append(item["id"])
        return with_translations({**item, "syncedLyrics": (
            "[00:01.000]<00:01.100>Hello<00:01.600><00:03.000>" if word_timed else "[00:01.000]Hello"
        ), "lyricsFormat": "lrcx" if word_timed else "lrc"}, {1000: "Translation"})

    monkeypatch.setattr(main_window, "fetch_chinese_lyrics", fetch)

    def interact(dialog):
        checkbox = dialog.findChild(QCheckBox)
        assert checkbox.text() == "Include translations"
        assert not checkbox.isEnabled() and not checkbox.isChecked()
        provider = dialog.findChild(QComboBox)
        provider.setCurrentText("NetEase Cloud Music")
        dialog.findChild(QLineEdit).setText("Original")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        preview = dialog.findChild(QTextEdit)
        deadline = time.monotonic() + 4
        while not checkbox.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert checkbox.isEnabled()
        tabs = dialog.findChild(QTabBar)
        assert [tabs.tabText(index) for index in range(tabs.count())] == ["Original", "Translated"]
        assert tabs.isTabEnabled(1)
        assert "Translation" not in preview.toPlainText()
        checkbox.setChecked(True)
        assert "Translation" in preview.toPlainText()
        assert "Hello" in preview.toPlainText()
        assert r"{\nt}" in preview.toPlainText()
        tabs.setCurrentIndex(1)
        assert not checkbox.isEnabled() and checkbox.isChecked()
        assert preview.toPlainText() == "[00:01.000]Translation"
        assert "Hello" not in preview.toPlainText()
        provider.setCurrentText("LRCLIB")
        assert not checkbox.isEnabled()
        assert not tabs.isTabEnabled(1)
        provider.setCurrentText("NetEase Cloud Music")
        assert checkbox.isEnabled() and checkbox.isChecked()
        tabs.setCurrentIndex(1)
        assert not checkbox.isEnabled()
        assert "Translation" in preview.toPlainText()
        checkbox.click()
        assert checkbox.isChecked()
        assert "Translation" in preview.toPlainText()
        tabs.setCurrentIndex(0)
        assert checkbox.isEnabled() and checkbox.isChecked()
        assert "Hello" in preview.toPlainText() and r"{\nt}" in preview.toPlainText()
        checkbox.click()
        assert "Translation" not in preview.toPlainText()
        checkbox.setChecked(include)
        assert fetches == [42]
        dialog.show()
        qapp.processEvents()
        assert dialog.grab().save(str(tmp_path / "translation-dialog.png"))
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics").click()
        return 1

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(loaded) == 1
        saved = Path(loaded[0])
        assert saved.suffix == (".lrcx" if include or word_timed else ".lrc")
        content = saved.read_text(encoding="utf-8")
        assert (r"{\nt}" in content) == include
        if include:
            line = load_lrcx(saved, infer_ends=False)[0]
            assert line.text == "Hello\nTranslation"
            assert line.translation_lines == [1]
            assert line.has_karaoke == word_timed
            if word_timed:
                assert (line.segments[0].start, line.segments[0].end, line.end) == (1.1, 1.6, 3)
    finally:
        parent.deleteLater()


def test_translation_checkbox_stays_disabled_without_aligned_translation(qapp, monkeypatch):
    parent = QWidget()
    parent.current_audio_path = None
    monkeypatch.setattr(main_window, "search_chinese_lyrics", lambda source, *args: [
        {"id": 42, "source": source, "trackName": "Original"},
    ])
    monkeypatch.setattr(main_window, "fetch_chinese_lyrics", lambda item, cancel: {
        **item, "syncedLyrics": "[00:01]Original", "lyricsFormat": "lrc",
    })

    def interact(dialog):
        dialog.findChild(QComboBox).setCurrentText("NetEase Cloud Music")
        dialog.findChild(QLineEdit).setText("Original")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        preview = dialog.findChild(QTextEdit)
        deadline = time.monotonic() + 4
        while "Original" not in preview.toPlainText() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert "Original" in preview.toPlainText()
        assert not dialog.findChild(QCheckBox).isEnabled()
        assert not dialog.findChild(QTabBar).isTabEnabled(1)
        dialog.reject()
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
    finally:
        parent.deleteLater()


@pytest.mark.parametrize("script,expected", [("simplified", "音乐与梦想"), ("traditional", "音樂與夢想")])
def test_translation_script_preview_matches_import_without_changing_original(qapp, monkeypatch, tmp_path, script, expected):
    parent = QWidget()
    parent.current_audio_path = tmp_path / "song.mp3"
    loaded, fetches = [], []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    monkeypatch.setattr(main_window, "search_chinese_lyrics", lambda source, *args: [
        {"id": 42, "source": source, "trackName": "Original"},
    ])
    provider_text = "音樂与梦想"

    def fetch(item, cancel):
        fetches.append(item["id"])
        return with_translations({**item, "syncedLyrics": "[00:01.000]僕の夢", "lyricsFormat": "lrc"},
                                 {1000: provider_text})

    monkeypatch.setattr(main_window, "fetch_chinese_lyrics", fetch)

    def interact(dialog):
        dialog.findChild(QComboBox).setCurrentText("NetEase Cloud Music")
        dialog.findChild(QLineEdit).setText("Original")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search").click()
        checkbox = dialog.findChild(QCheckBox)
        deadline = time.monotonic() + 4
        while not checkbox.isEnabled() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert checkbox.isEnabled()
        combo = next(field for field in dialog.findChildren(QComboBox)
                     if field.accessibleName() == "Chinese translation script")
        assert combo.isEnabled() and combo.currentData() == "provided"
        dialog.findChild(QTabBar).setCurrentIndex(1)
        preview = dialog.findChild(QTextEdit)
        assert preview.toPlainText() == "[00:01.000]" + provider_text
        combo.setCurrentIndex(combo.findData(script))
        assert preview.toPlainText() == "[00:01.000]" + expected
        combo.setCurrentIndex(combo.findData("provided"))
        assert preview.toPlainText() == "[00:01.000]" + provider_text
        combo.setCurrentIndex(combo.findData(script))
        dialog.findChild(QTabBar).setCurrentIndex(0)
        assert preview.toPlainText() == "[00:01.000]僕の夢"
        checkbox.setChecked(True)
        assert preview.toPlainText() == "[00:01.000]僕の夢" + r"{\nt}" + expected
        assert fetches == [42]
        dialog.findChild(QTabBar).setCurrentIndex(1)
        assert not checkbox.isEnabled() and checkbox.isChecked()
        dialog.show()
        qapp.processEvents()
        assert dialog.grab().save(str(tmp_path / "translation-script-dialog.png"))
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics").click()
        return 1

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        line = load_lrcx(loaded[0])[0]
        assert line.text == "僕の夢\n" + expected
        assert line.translation_lines == [1]
    finally:
        parent.deleteLater()
