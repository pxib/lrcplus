from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFontDatabase, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from lyrics import furigana
from plugins.community.anki_flashcards.plugin import FlashcardDialog, format_lyric_with_readings


def test_analyze_lyric_words_maps_words_to_source_lines(monkeypatch):
    def fake_analyze(text):
        assert text == "私 は好き\n私は好き"
        return [
            {"surface": "私", "reading": "わたし"},
            {"surface": "は", "reading": "は"},
            {"surface": "好き", "reading": "すき"},
            {"surface": "私", "reading": "わたし"},
            {"surface": "は", "reading": "は"},
            {"surface": "好き", "reading": "すき"},
        ]

    monkeypatch.setattr(furigana, "_analyze", fake_analyze)

    words = furigana.analyze_lyric_words([
        {"text": "私 は好き", "start_ms": 1200},
        {"text": "私は好き", "start_ms": 3400},
    ])

    assert [word["text"] for word in words] == [
        "私", "は", "好き", "私", "は", "好き",
    ]
    assert [word["line_index"] for word in words] == [0, 0, 0, 1, 1, 1]
    assert [word["start_ms"] for word in words] == [
        1200, 1200, 1200, 3400, 3400, 3400,
    ]
    assert words[3]["context"] == "私は好き"


def test_format_lyric_with_readings_appends_furigana_inline():
    formatted = format_lyric_with_readings(
        "猫が好き！",
        [
            {"text": "猫", "reading": "ねこ"},
            {"text": "好き", "reading": "すき"},
        ],
    )

    assert formatted == (
        '猫<span style="font-size: 15px; color: #68716e;">（ねこ）</span>'
        'が好き<span style="font-size: 15px; color: #68716e;">（すき）</span>！'
    )


@pytest.fixture
def flashcards(qapp, monkeypatch):
    monkeypatch.setattr("plugins.community.anki_flashcards.plugin.WordAnalysisWorker.start", lambda self: None)
    lyrics = [
        {"text": "\u732b\u304c\u597d\u304d\nTranslation", "start_ms": 1000},
        {"text": "Another line", "start_ms": 3000},
    ]
    words = [
        {"text": "\u732b", "reading": "\u30cd\u30b3", "line_index": 0},
        {"text": "\u597d\u304d", "reading": "\u30b9\u30ad", "line_index": 0},
        {"text": "Another", "reading": "", "line_index": 1},
    ]
    dialog = FlashcardDialog(SimpleNamespace(), "song.mp3", lyrics)
    dialog.populate_words(words)
    try:
        yield dialog
    finally:
        dialog.reject()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_flashcards_preserve_navigation_selection_and_export_context(flashcards, monkeypatch, tmp_path):
    dialog = flashcards
    assert dialog.all_lyrics_list.count() == 0
    assert dialog.line_selector.itemData(0, Qt.ItemDataRole.ToolTipRole) == dialog.lyrics[0]["text"]
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    dialog.next_line()
    assert dialog.line_selector.currentIndex() == 1
    dialog.previous_line()
    assert dialog.table.item(0, 0).checkState() == Qt.CheckState.Checked
    destination = tmp_path / "cards.tsv"
    monkeypatch.setattr("plugins.community.anki_flashcards.plugin.QFileDialog.getSaveFileName",
                        lambda *args: (str(destination), ""))
    dialog.export_selected("song.mp3")
    import csv
    with destination.open(encoding="utf-8", newline="") as source:
        assert list(csv.reader(source, delimiter="\t")) == [[
            "\u732b", "\u30cd\u30b3", dialog.lyrics[0]["text"],
        ]]


def test_all_lyrics_are_built_on_demand_and_stay_navigable(flashcards, qapp):
    dialog = flashcards
    dialog.show()
    qapp.processEvents()
    assert dialog.all_lyrics_list.count() == 0
    dialog.toggle_all_lyrics()
    assert dialog.all_lyrics_list.count() == len(dialog.lyrics)
    dialog.select_listed_line(dialog.all_lyrics_list.item(1))
    assert dialog.line_selector.currentIndex() == 1
    dialog.toggle_all_lyrics()
    dialog.toggle_all_lyrics()
    assert dialog.all_lyrics_list.count() == len(dialog.lyrics)


def test_word_checkbox_still_handles_mouse_input(flashcards, qapp):
    dialog = flashcards
    dialog.show()
    qapp.processEvents()
    table = dialog.table
    option = QStyleOptionViewItem()
    option.initFrom(table)
    option.rect = table.visualItemRect(table.item(0, 0))
    option.widget = table
    table.itemDelegate().initStyleOption(option, table.model().index(0, 0))
    rect = table.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator,
                                       option, table)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert table.item(0, 0).checkState() == Qt.CheckState.Checked
    assert dialog.selected_cards


@pytest.mark.parametrize("text", [
    "\u732b <cat> & reading",
    "\u7231 \uc0ac\ub791\ud574 \U0001f49b",
    "\u0627\u0644\u0633\u0644\u0627\u0645",
])
def test_flashcard_rich_labels_preserve_text_sizes_and_supported_glyphs(flashcards, qapp, text):
    if any(ord(char) > 0xFFFF or 0x600 <= ord(char) <= 0x6FF for char in text):
        if not QFontDatabase.hasFamily("Segoe UI Emoji"):
            pytest.skip("Requires native Windows fallback fonts")
    dialog = flashcards
    dialog.lyrics = [{"text": text, "start_ms": 0}]
    dialog.populate_words([{"text": text, "reading": "\u30cd\u30b3", "line_index": 0}])
    for label in (dialog.lyric_label, dialog.line_reading_label):
        document = QTextDocument()
        document.setDefaultFont(label.font())
        document.setHtml(label.text())
        document.setTextWidth(800)
        document.size()  # Shape the actual rich-text document.
        assert "\u30cd\u30b3" in document.toPlainText()
        families = set()
        block = document.begin()
        while block.isValid():
            for run in block.layout().glyphRuns():
                families.add(run.rawFont().familyName())
                assert all(glyph != 0 for glyph in run.glyphIndexes())
            block = block.next()
        assert not families & {"SimSun", "Gulim"}
    document = QTextDocument()
    document.setDefaultFont(dialog.lyric_label.font())
    document.setHtml(dialog.lyric_label.text())
    assert text in document.toPlainText()
    sizes = set()
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            font = iterator.fragment().charFormat().font()
            sizes.add(font.pixelSize() if font.pixelSize() > 0 else round(font.pointSizeF(), 1))
            iterator += 1
        block = block.next()
    assert len(sizes) >= 2  # Main text and smaller inline readings stay distinct.
