from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtGui import QFont, QImage, QColor, QTextCursor

from lyrics.models import LyricLine, LyricSegment
from lyrics.editor_source import parse_editor_source
from ui.lyrics_card_text import CardLyrics, snapshot_card_lyrics, utf16_offsets, quote_bar_rects
from ui.lyrics_card_renderer import CardStyle, CARD_TEMPLATES, _document, render_lyric_card
import ui.lyrics_share_card as card_module


def rich_lines():
    return parse_editor_source(
        "[format:LRCX]\n[00:01.000]> 😀 **Bold** and *italic*{\\nt}<u>Translated</u>\n"
        "[00:03.000]~~Yesterday~~ today\n", ".lrcx",
    ).lines


def text_format(document, text, fragment):
    start = text.index(fragment)
    offsets = utf16_offsets(text)
    cursor = QTextCursor(document)
    cursor.setPosition(offsets[start])
    cursor.setPosition(offsets[start + len(fragment)], QTextCursor.MoveMode.KeepAnchor)
    return cursor.charFormat()


def test_native_styles_translations_and_quotes_survive_unicode_offsets(qapp):
    source = snapshot_card_lyrics(rich_lines())
    document = _document(source.text, "", 56, 900, lyric_style=CardStyle(), source=source)
    assert document.toPlainText() == "😀 Bold and italic\nTranslated\nYesterday today"
    assert text_format(document, source.text, "Bold").fontWeight() == QFont.Weight.Bold
    assert text_format(document, source.text, "italic").fontItalic()
    assert text_format(document, source.text, "Translated").fontUnderline()
    assert text_format(document, source.text, "Translated").font().pixelSize() == 42
    assert text_format(document, source.text, "Yesterday").fontStrikeOut()
    first = document.begin()
    assert first.blockFormat().leftMargin() > 0
    assert first.next().blockFormat().leftMargin() == first.blockFormat().leftMargin()
    assert first.next().next().blockFormat().leftMargin() < first.blockFormat().leftMargin()
    assert document.documentLayout().blockBoundingRect(first.next()).height() < document.documentLayout().blockBoundingRect(first).height()
    bars = quote_bar_rects(document)
    assert len(bars) == 1
    assert bars[0].height() >= document.documentLayout().blockBoundingRect(first).height()


def test_card_emphasis_combines_with_source_emphasis(qapp):
    source = snapshot_card_lyrics(rich_lines())
    style = CardStyle(bold=True, underline=True)
    document = _document(source.text, "", 56, 900, lyric_style=style, source=source)
    font = text_format(document, source.text, "italic").font().resolve(document.defaultFont())
    assert font.bold() and font.italic() and font.underline()


def test_snapshot_is_immutable_and_independent_of_karaoke_and_timestamps(qapp):
    lines = rich_lines()
    before = deepcopy(lines)
    first = snapshot_card_lyrics(lines)
    altered = deepcopy(lines)
    for index, line in enumerate(altered):
        line.start = 999 + index
        line.end = 2000
        line.has_karaoke = True
        line.segments = [LyricSegment(1234, char, end=1500, instant=True) for char in line.text]
    second = snapshot_card_lyrics(altered)
    assert first == second
    style = CardStyle(stroke_enabled=True, shadow_enabled=True)
    assert render_lyric_card(first, style) == render_lyric_card(second, style)
    assert lines == before
    lines[0].style_spans.clear()
    assert first != snapshot_card_lyrics(lines)


def test_trimming_adjusts_formats_without_moving_them_onto_neighboring_text(qapp):
    source = snapshot_card_lyrics([LyricLine(0, "  😀Word \n", style_spans=[(3, 7, True, False, False, False)])]).trimmed()
    assert source.text == "😀Word"
    document = _document(source.text, "", 56, 900, lyric_style=CardStyle(), source=source)
    assert text_format(document, source.text, "Word").fontWeight() == QFont.Weight.Bold
    assert text_format(document, source.text, "😀").fontWeight() != QFont.Weight.Bold


@pytest.mark.parametrize("template", CARD_TEMPLATES)
def test_formatted_cards_fit_with_effects_cover_and_right_alignment(qapp, template):
    import numpy as np
    lines = [LyricLine(0, "夜の歌\nTranslation", style_spans=[(0, 3, True, True, False, True)],
                       is_blockquote=True, translation_lines=[1])]
    source = snapshot_card_lyrics(lines)
    style = CardStyle(template=template, alignment="right", stroke_enabled=True, shadow_enabled=True,
                      shadow_offset_x=-12, shadow_offset_y=18, show_album_cover=True,
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    cover = QImage(100, 100, QImage.Format.Format_RGB32)
    cover.fill(QColor("#ffffff"))
    image = render_lyric_card(source, style, cover)
    assert image != render_lyric_card(source, replace(style, preserve_lrcx_formatting=False), cover)
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)
    red, green, blue = (pixels[:, :, index] for index in range(3))
    y, x = np.where((red == blue) & (red > 32) & (green == 0))
    assert len(x)
    assert x.min() >= style.padding and x.max() < style.width - style.padding
    assert y.min() >= style.padding and y.max() < style.height - style.padding


def open_card(editor):
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    return editor.share_card


def test_format_only_edits_invalidate_preview_and_toggle_restores_plain_text(editor, tmp_path, monkeypatch):
    card = open_card(editor)
    card.refresh()
    baseline = card._image.copy()
    document = deepcopy(editor.lines)
    editor.lines[0].style_spans = [(0, 5, True, False, False, True)]
    card.refresh()
    assert card._image != baseline
    output = tmp_path / "formatted.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    card.export_image()
    assert QImage(str(output)).convertToFormat(card._image.format()) == card._image
    card.preserve_lrcx_formatting.setChecked(False)
    card.refresh()
    assert card._image == baseline
    assert editor.lines[0].text == document[0].text
    assert editor.lines[0].style_spans == [(0, 5, True, False, False, True)]


def test_checked_excerpt_keeps_its_own_formatting_and_document_order(editor, monkeypatch):
    from PySide6.QtCore import Qt
    editor.lines = [LyricLine(1, "Skip"), LyricLine(2, "Selected", style_spans=[(0, 8, False, True, False, False)], is_blockquote=True)]
    editor._populate_lines()
    card = open_card(editor)
    editor.clear_share_line_selection()
    editor.line_list.item(1).setCheckState(Qt.CheckState.Checked)
    captured = []
    original = card_module.render_lyric_card
    def capture(contents, style, *args):
        captured.append(contents)
        return original(contents, style, *args)
    monkeypatch.setattr(card_module, "render_lyric_card", capture)
    card.copy_image()
    assert isinstance(captured[-1], CardLyrics)
    assert captured[-1].text == "Selected"
    assert captured[-1].styles == ((0, 8, False, True, False, False),)
    assert captured[-1].quotes == ((0, 8),)


def test_unsaved_source_styles_are_used_without_saving_the_song(editor):
    card = open_card(editor)
    editor.source_editor.setPlainText("[format:LRCX]\n[00:01.000]> **Unsaved**{\\nt}Translation")
    editor.reload_model_from_source()
    editor.select_all_share_lines()
    card.copy_image()
    source = card._render_key[-1]
    assert source.has_formatting
    assert source.text == "Unsaved\nTranslation"
    assert card._image == render_lyric_card(source, card.current_style())
    assert not editor.lrc_path.exists()


def test_formatting_preference_round_trips_without_storing_song_text(editor, monkeypatch):
    from ui.lyrics_card_presets import load_card_presets, style_from_preset
    card = open_card(editor)
    assert card.preserve_lrcx_formatting.isChecked()
    card.preserve_lrcx_formatting.setChecked(False)
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("Plain preset", True))
    card.save_preset()
    assert not load_card_presets()["Plain preset"]["preserve_lrcx_formatting"]
    card.preserve_lrcx_formatting.setChecked(True)
    card.preset_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("Plain preset")
    assert not card.preserve_lrcx_formatting.isChecked()
    assert style_from_preset({}).preserve_lrcx_formatting
