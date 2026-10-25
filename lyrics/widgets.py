"""Compatibility exports for lyric display widgets."""

from lyrics.furigana_widget import (
    FuriganaWidget,
    is_kana,
    _normalize_all_romaji_punctuation,
    _normalize_all_romaji_blocks,
    _FuriganaBridge,
    _FURIGANA_EXECUTOR,
)
from lyrics.furigana import (
    build_furigana_segments,
    get_cached_furigana_segments,
    split_reading_by_text_ranges,
    kana_to_romaji,
)

__all__ = ["FuriganaWidget", "ScrollingLyricsWidget", "is_kana"]


def __getattr__(name):
    if name == "ScrollingLyricsWidget":
        from lyrics.scrolling_widget import ScrollingLyricsWidget

        return ScrollingLyricsWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
