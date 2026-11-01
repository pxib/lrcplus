"""Search content avoids unnecessary fonts without losing unsupported glyphs."""

import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QFontDatabase, QRawFont, QTextLayout
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

import ui.main_window as main_window


@pytest.mark.parametrize("source", ["KuGou", "Kuwo", "QQ Music"])
@pytest.mark.parametrize("text, fallback", [
    ("\u65e5\u672c\u8a9e\u306e\u6b4c", False),
    ("\u0627\u0644\u0633\u0644\u0627\u0645", True),
])
def test_search_fonts_follow_text_coverage_and_preserve_cached_lyrics(
    qapp, monkeypatch, tmp_path, source, text, fallback,
):
    parent = QWidget()
    parent.current_audio_path = tmp_path / "song.mp3"
    parent.clear_lyrics_search_index = lambda: None
    loaded = []
    parent.load_lrc_file = loaded.append
    lyrics = f"[00:01.000]{text}\n[00:02.000]Second line"
    fetched = []
    monkeypatch.setattr(main_window, "search_chinese_lyrics", lambda *args: [
        {"id": 42, "source": source, "trackName": text,
         "albumName": text, "artistName": text},
    ])

    def fetch(item, cancel):
        fetched.append(item["id"])
        return {**item, "syncedLyrics": lyrics, "lyricsFormat": "lrc"}

    monkeypatch.setattr(main_window, "fetch_chinese_lyrics", fetch)

    def check_font(font):
        assert font.family() == "Noto Sans JP"
        assert bool(font.styleStrategy() & QFont.StyleStrategy.NoFontMerging) == (not fallback)
        if not fallback:
            raw = QRawFont.fromFont(font)
            assert all(raw.supportsCharacter(ord(char)) for char in text)

    def check_preview(preview):
        preview._preview_fonts.rehighlight()
        block = preview.document().begin()
        while block.isValid():
            layout = QTextLayout(block.text(), preview.font())
            layout.setFormats(block.layout().formats())
            layout.beginLayout()
            layout.createLine().setLineWidth(2000)
            layout.endLayout()
            assert layout.glyphRuns()
            # The offscreen Windows plugin has no installed system fonts.
            # Arabic glyph checks run with the native platform below.
            if not fallback or QFontDatabase.hasFamily("Segoe UI"):
                assert all(glyph != 0 for run in layout.glyphRuns() for glyph in run.glyphIndexes())
            block = block.next()

    def interact(dialog):
        provider = dialog.findChild(QComboBox)
        provider.setCurrentText(source)
        query = dialog.findChild(QLineEdit)
        query.setText(text)
        check_font(query.font())
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Search").click()
        table = dialog.findChild(QTableWidget)
        preview = dialog.findChild(QTextEdit)
        deadline = time.monotonic() + 4
        while preview.toPlainText() != lyrics and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert preview.toPlainText() == lyrics
        for column in range(3):
            check_font(table.item(0, column).font())
        check_preview(preview)
        provider.setCurrentText("LRCLIB")
        provider.setCurrentText(source)
        assert preview.toPlainText() == lyrics
        check_preview(preview)
        check_font(table.item(0, 0).font())
        assert fetched == [42]
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Use Selected Lyrics").click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        assert len(loaded) == 1
        assert (tmp_path / "song.lrc").read_text(encoding="utf-8") == lyrics + "\n"
    finally:
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
