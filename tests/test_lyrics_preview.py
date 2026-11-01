"""Mixed lyric scripts must render their real glyphs without legacy CJK fallback."""

import pytest
import unicodedata
from PySide6.QtGui import QFont, QFontDatabase, QRawFont, QTextLayout

from core.fonts import content_font
from ui.lyrics_preview import LyricsPreview


def test_content_font_checks_unicode_codepoints_and_selects_a_covering_face(qapp):
    # PySide's str overload can report incorrect coverage. These two characters
    # distinguish it from the integer UCS-4 overload on Windows.
    for text in ("\u751f\u7269\u76ee\u77e5", "\u7231\u7ecf\u8425"):
        font = content_font(qapp.font(), text)
        raw = QRawFont.fromFont(font)
        if font.styleStrategy() & QFont.StyleStrategy.NoFontMerging:
            assert all(raw.supportsCharacter(ord(char)) for char in text)
            assert all(raw.glyphIndexesForString(text))


def test_mixed_preview_has_no_missing_glyphs_and_preserves_source_and_selection(qapp):
    if not all(QFontDatabase.hasFamily(family) for family in (
        "Microsoft YaHei UI", "Malgun Gothic", "Segoe UI Emoji",
    )):
        pytest.skip("Requires the native Windows font database")
    preview = LyricsPreview()
    # Include an astral character before another fallback run to check UTF-16
    # format offsets, as well as the Chinese/Korean scripts in real KRC lyrics.
    text = "[00:01.000]<00:01.100>\u611b\u3057\u3066\n[00:02.000]\u7231 \uc0ac\ub791\ud574 \U0001f600 \u7231\n"
    text += unicodedata.normalize("NFD", "Ti\u1ebfng Vi\u1ec7t")
    try:
        preview.setPlainText(text)
        preview._preview_fonts.rehighlight()
        assert preview.toPlainText() == text
        families = set()
        block = preview.document().begin()
        while block.isValid():
            layout = QTextLayout(block.text(), preview.font())
            layout.setFormats(block.layout().formats())
            layout.beginLayout()
            layout.createLine().setLineWidth(2000)
            layout.endLayout()
            assert layout.glyphRuns()
            for run in layout.glyphRuns():
                families.add(run.rawFont().familyName())
                assert all(glyph != 0 for glyph in run.glyphIndexes())
            block = block.next()
        if QFontDatabase.hasFamily("Microsoft YaHei UI") and QFontDatabase.hasFamily("Malgun Gothic"):
            assert "Microsoft YaHei UI" in families
            assert "Malgun Gothic" in families
            assert not families.intersection({"SimSun", "Gulim"})
        preview.selectAll()
        assert preview.textCursor().selection().toPlainText() == text
        replacement = "[00:01.000]\u611b\u3057\u3066"
        preview.setPlainText(replacement)
        preview._preview_fonts.rehighlight()
        assert preview.toPlainText() == replacement
        assert not preview.document().begin().layout().formats()
    finally:
        preview.deleteLater()
        qapp.processEvents()


def test_preview_keeps_automatic_fallback_for_other_fonts(qapp, monkeypatch):
    monkeypatch.setattr(QFontDatabase, "hasFamily", lambda family: False)
    preview = LyricsPreview()
    try:
        preview.setPlainText("\u0627\u0644\u0633\u0644\u0627\u0645")
        preview._preview_fonts.rehighlight()
        formats = preview.document().begin().layout().formats()
        assert formats
        assert all(not item.format.font().styleStrategy() & QFont.StyleStrategy.NoFontMerging
                   for item in formats)
    finally:
        preview.deleteLater()
        qapp.processEvents()
