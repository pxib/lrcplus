"""App-local fonts for the music library and lyric search content."""

from functools import lru_cache
from pathlib import Path
import unicodedata

from PySide6.QtGui import QFont, QFontDatabase, QRawFont


LIBRARY_FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansJP-Regular.otf"
)

# Prefer modern outline fonts over Windows' much larger legacy fallback path.
CONTENT_FALLBACK_FAMILIES = (
    "Microsoft YaHei UI", "Malgun Gothic", "Noto Sans CJK SC",
    "Noto Sans CJK KR", "PingFang SC", "Apple SD Gothic Neo",
)


@lru_cache(maxsize=1)
def _library_font_family():
    # Register once with Qt, without changing the system's installed fonts.
    font_id = QFontDatabase.addApplicationFont(str(LIBRARY_FONT_PATH))
    if font_id < 0:
        return None
    families = QFontDatabase.applicationFontFamilies(font_id)
    return families[0] if families else None


def library_font(base_font):
    """Keep the view's size and style, using Noto's Japanese/Latin coverage."""
    font = QFont(base_font)
    family = _library_font_family()
    if family:
        font.setFamily(family)
    return font


def content_font(base_font, text):
    """Use Noto's own glyphs when covered, allowing fallback for other text.

    Windows can otherwise load large system fonts for Simplified Chinese even
    when Noto has those glyphs. Check coverage before disabling font merging so
    text containing unsupported characters still renders normally.
    """
    font = library_font(base_font)
    strategy = font.styleStrategy() & ~QFont.StyleStrategy.NoFontMerging
    font.setStyleStrategy(strategy)
    family = _library_font_family()
    if family:
        raw_font = QRawFont.fromFont(font)
        # Use UCS-4 integers; PySide's string overload can misreport coverage.
        characters = {ord(char) for char in unicodedata.normalize("NFC", text)
                      if not char.isspace()}
        if (raw_font.isValid() and raw_font.familyName() == family
                and all(raw_font.supportsCharacter(char) for char in characters)):
            font.setStyleStrategy(strategy | QFont.StyleStrategy.NoFontMerging)
            return font
        for fallback in CONTENT_FALLBACK_FAMILIES:
            if not QFontDatabase.hasFamily(fallback):
                continue
            candidate = QFont(font)
            candidate.setFamily(fallback)
            raw_font = QRawFont.fromFont(candidate)
            if (raw_font.isValid()
                    and all(raw_font.supportsCharacter(char) for char in characters)):
                candidate.setStyleStrategy(strategy | QFont.StyleStrategy.NoFontMerging)
                return candidate
    return font
