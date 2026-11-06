import unicodedata

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyleOptionViewItem

from ui.content_list import configure_content_list


@pytest.mark.parametrize("text", [
    "Japanese \u65e5\u672c\u8a9e.mp3",
    "\uc694\uc544\uc18c\ube44 \u591c\u306b\u99c6\u3051\u308b \U0001f49b.mp3",
    "\u7231 \uc0ac\ub791\ud574 \U0001f600 \u7231.mp3",
    unicodedata.normalize("NFD", "Ti\u1ebfng Vi\u1ec7t.mp3"),
    "\u0627\u0644\u0633\u0644\u0627\u0645.mp3",
])
def test_metadata_list_preserves_mixed_script_glyphs_and_original_data(qapp, text):
    if any(ord(char) > 0xFFFF or 0xAC00 <= ord(char) <= 0xD7AF
           or 0x600 <= ord(char) <= 0x6FF for char in text):
        if not QFontDatabase.hasFamily("Segoe UI Emoji"):
            pytest.skip("Requires native Windows fallback fonts")
    view = QListWidget()
    configure_content_list(view)
    item = QListWidgetItem(text)
    item.setData(Qt.ItemDataRole.UserRole, "original/path")
    view.addItem(item)
    try:
        view.resize(640, 120)
        view.show()
        qapp.processEvents()
        view.grab()
        delegate = view.itemDelegate()
        option = QStyleOptionViewItem()
        option.initFrom(view)
        delegate.initStyleOption(option, view.model().index(0, 0))
        layout, line = delegate._layout(text, option.font, None,
                                        Qt.TextElideMode.ElideNone, option.direction)
        assert line.isValid()
        assert all(glyph != 0 for run in layout.glyphRuns() for glyph in run.glyphIndexes())
        assert not {run.rawFont().familyName() for run in layout.glyphRuns()} & {"SimSun", "Gulim"}
        assert item.text() == text
        view.setCurrentRow(0)
        assert view.currentItem().data(Qt.ItemDataRole.UserRole) == "original/path"
    finally:
        view.close()
        view.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("mode", [Qt.TextElideMode.ElideLeft,
                                  Qt.TextElideMode.ElideMiddle,
                                  Qt.TextElideMode.ElideRight])
def test_long_metadata_is_elided_without_changing_source(qapp, mode):
    view = QListWidget()
    configure_content_list(view)
    text = "A long song name " * 30 + ".mp3"
    view.addItem(text)
    try:
        delegate = view.itemDelegate()
        layout, line = delegate._layout(text, view.font(), 160, mode, Qt.LayoutDirection.LeftToRight)
        assert "…" in layout.text()
        assert line.naturalTextWidth() <= 160
        assert view.item(0).text() == text
    finally:
        view.deleteLater()
        qapp.processEvents()
