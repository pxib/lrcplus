"""App-local fonts shared by the music library's metadata cells."""

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase


LIBRARY_FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansJP-Regular.otf"
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
