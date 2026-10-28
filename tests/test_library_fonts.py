import unicodedata

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QRawFont

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
    missing = {f"U+{ord(c):04X}" for c in text
               if not c.isspace() and not raw_font.supportsCharacter(c)}
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

    window = Player()
    qapp.processEvents()
    try:
        assert window.library_table.font().family() == "Noto Sans JP"
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
