"""Statistics metadata avoids Windows' large legacy font fallback."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QStyleOptionViewItem

from ui.content_list import ContentListDelegate
from ui.main_window import Player


def test_statistics_metadata_uses_glyph_checked_rendering_and_numeric_sort(qapp):
    window = Player()
    try:
        page = window.statistics_page
        text = "\u65e5\u672c\u8a9e Ti\u1ebfng Vi\u1ec7t"
        if QFontDatabase.hasFamily("Malgun Gothic"):
            text += " \uc0ac\ub791\ud574"
        stamp = datetime.now().astimezone().isoformat()
        page._apply({
            "summary": {"listened_ms": 12000, "plays": 12, "songs": 2},
            "daily": [],
            "songs": [dict(title=text, artist=text, album=text, plays=n,
                           listened_ms=n * 1000, last_played=stamp) for n in (2, 10)],
            "artists": [dict(name=text, plays=12, listened_ms=12000)],
            "albums": [dict(name=text, artist=text, plays=12, listened_ms=12000)],
            "stations": [dict(name=text, country=text, plays=12, listened_ms=12000, last_played=stamp)],
        })
        for table in page.tables.values():
            assert table.font().family() == window.library_table.font().family()
            assert table.font().styleStrategy() & QFont.StyleStrategy.NoFontMerging
            delegate = table.itemDelegate()
            assert isinstance(delegate, ContentListDelegate)
            option = QStyleOptionViewItem()
            delegate.initStyleOption(option, table.model().index(0, 0))
            layout, _ = delegate._layout(option.text, option.font, 1000,
                                         Qt.TextElideMode.ElideNone, option.direction)
            runs = layout.glyphRuns()
            assert runs and all(glyph for run in runs for glyph in run.glyphIndexes())
            assert not {run.rawFont().familyName() for run in runs} & {"Gulim", "Batang"}
        songs = page.tables["songs"]
        assert songs.item(0, 3).text() == "10"
        songs.sortItems(3, Qt.SortOrder.AscendingOrder)
        assert songs.item(0, 3).text() == "2"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
