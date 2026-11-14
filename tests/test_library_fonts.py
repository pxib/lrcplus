import unicodedata

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont, QFontDatabase, QRawFont, QTextLayout

from core import fonts


VIETNAMESE = "aăâeêioôơuưyAĂÂEÊIOÔƠUƯY"
TONES = "\u0300\u0301\u0303\u0309\u0323"


@pytest.mark.parametrize("text", [
    "日本語の歌 — Tiếng Việt: Những giấc mơ",
    "".join(unicodedata.normalize("NFC", letter + tone)
            for letter in VIETNAMESE for tone in TONES),
    unicodedata.normalize("NFD", "Tiếng Việt: Những giấc mơ"),
    "đĐ — Beyoncé, Déjà Vu",
])
def test_bundled_font_covers_japanese_and_vietnamese_without_fallback(qapp, text):
    font = fonts.library_font(QFont())
    assert font.family() == "Noto Sans JP"
    raw_font = QRawFont.fromFont(font)
    assert raw_font.familyName() == "Noto Sans JP"
    # Qt composes decomposed Vietnamese accents during shaping.
    missing = {f"U+{ord(c):04X}" for c in unicodedata.normalize("NFC", text)
               if not c.isspace() and not raw_font.supportsCharacter(ord(c))}
    assert not missing


def test_library_font_preserves_size_and_style(qapp):
    original = QFont("Yu Gothic UI", 13)
    original.setBold(True)
    original.setItalic(True)
    font = fonts.library_font(original)
    assert font.family() == "Noto Sans JP"
    assert font.pointSize() == 13
    assert font.bold() and font.italic()
    assert original.family() == "Yu Gothic UI"


def test_reopening_library_reuses_font_registration(qapp, monkeypatch):
    fonts.library_font(QFont())

    def register_again(path):
        pytest.fail("Reopening the library must not register another font copy")

    monkeypatch.setattr(fonts.QFontDatabase, "addApplicationFont", register_again)
    for _ in range(5):
        assert fonts.library_font(QFont()).family() == "Noto Sans JP"


def test_missing_font_preserves_system_font(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "LIBRARY_FONT_PATH", tmp_path / "missing.otf")
    fonts._library_font_family.cache_clear()
    try:
        original = QFont("Yu Gothic UI", 13)
        assert fonts.library_font(original) == original
    finally:
        fonts._library_font_family.cache_clear()


def test_player_library_uses_bundled_font(qapp):
    from ui.main_window import Player
    from ui.content_list import ContentListDelegate
    from ui.library_lyrics_search import LyricMatchDelegate
    from ui.player_dialogs import AlbumCoverDelegate

    window = Player()
    qapp.processEvents()
    try:
        assert window.library_table.font().family() == "Noto Sans JP"
        delegate = window.library_table.itemDelegate()
        assert isinstance(delegate, ContentListDelegate)
        assert isinstance(window.library_table.itemDelegateForColumn(0), AlbumCoverDelegate)
        assert isinstance(window.library_table.itemDelegateForColumn(
            window.library_lyrics_column), LyricMatchDelegate)
        if QFontDatabase.hasFamily("Malgun Gothic"):
            font = QFont(window.library_table.font())
            font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
            layout, _ = delegate._layout("\uc0ac\ub791\ud574", font,
                                         300, Qt.TextElideMode.ElideNone,
                                         Qt.LayoutDirection.LeftToRight)
            runs = layout.glyphRuns()
            assert runs and all(glyph for run in runs for glyph in run.glyphIndexes())
            assert not {run.rawFont().familyName() for run in runs} & {"Gulim", "Batang"}
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_covered_chinese_uses_bundled_glyphs_without_system_fallback(qapp):
    text = "\u65e5\u672c\u8a9e\u306e\u6b4c - \u5f35\u5b66\u53cb"
    original = QFont("Yu Gothic UI", 13)
    original.setBold(True)
    font = fonts.content_font(original, text)
    assert font.pointSize() == 13 and font.bold()
    assert original.family() == "Yu Gothic UI"
    assert font.styleStrategy() & QFont.StyleStrategy.NoFontMerging
    layout = QTextLayout(text, font)
    layout.beginLayout()
    layout.createLine().setLineWidth(2000)
    layout.endLayout()
    assert layout.glyphRuns()
    assert all(run.rawFont().familyName() == "Noto Sans JP" for run in layout.glyphRuns())
    assert all(glyph != 0 for run in layout.glyphRuns() for glyph in run.glyphIndexes())


@pytest.mark.parametrize("text", ["\u0627\u0644\u0633\u0644\u0627\u0645", "\U0001f600"])
def test_unsupported_characters_restore_font_fallback(qapp, text):
    font = fonts.content_font(QFont(), "Song")
    assert font.styleStrategy() & QFont.StyleStrategy.NoFontMerging
    assert any(not QRawFont.fromFont(font).supportsCharacter(ord(char)) for char in text)
    fallback = fonts.content_font(font, text)
    assert not fallback.styleStrategy() & QFont.StyleStrategy.NoFontMerging
    assert fallback.family() == "Noto Sans JP"


def test_content_font_allows_system_fallback_if_bundled_font_is_missing(qapp, monkeypatch):
    base = fonts.content_font(QFont(), "Song")
    monkeypatch.setattr(fonts, "_library_font_family", lambda: None)
    font = fonts.content_font(base, "\u8fd8\u662f\u89c9\u5f97\u4f60\u6700\u597d")
    assert not font.styleStrategy() & QFont.StyleStrategy.NoFontMerging
