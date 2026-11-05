from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QTextCursor, QFont
from PySide6.QtTest import QSignalSpy

from lyrics.editor_source import parse_editor_source
from lyrics.models import LyricLine, LyricSegment
import ui.lyrics_card_text as card_text
import ui.lyrics_share_card as card_module
from ui.lyrics_card_text import CardLyrics, snapshot_card_lyrics
from ui.lyrics_card_renderer import CARD_SIZES, CARD_TEMPLATES, CardStyle, _document, render_lyric_card


@pytest.fixture(autouse=True)
def no_cached_readings(monkeypatch):
    monkeypatch.setattr(card_text, "get_cached_furigana_segments", lambda _: None)


def ruby_lines():
    return parse_editor_source(
        "[format:LRCX]\n[00:01.000]**{君|きみ}**の{声|こえ}を{聞|き}かせて{\\nt}Let me hear your voice\n"
        "[00:04.000]> <u>{東京|とうきょう}の{夜|よる}</u>", ".lrcx",
    ).lines


def test_authored_ruby_and_inline_spans_are_copied_without_timing_or_mutation():
    lines = ruby_lines()
    before = deepcopy(lines)
    source = snapshot_card_lyrics(lines, include_readings=True)
    assert source.readings[:3] == ((0, 1, "きみ"), (2, 3, "こえ"), (4, 5, "き"))
    assert "Let me hear your voice" in source.text
    changed = deepcopy(lines)
    for line in changed:
        line.start += 999
        line.end = 2000
        line.has_karaoke = not line.has_karaoke
        for segment in line.segments:
            segment.start += 999
            segment.end = 1800
            segment.instant = True
    assert source == snapshot_card_lyrics(changed, include_readings=True)
    assert lines == before


def test_manual_readings_override_cached_readings_and_automatic_translations_are_skipped(monkeypatch):
    line = LyricLine(0, "東京\nTranslation", [LyricSegment(0, "東京\nTranslation", ruby_spans=[(0, 1, "ひがし")])], translation_lines=[1])
    keys = []
    def cached(key):
        keys.append(key)
        return [{"text": "東京", "reading": "とうきょう"}, {"text": "\n", "reading": None},
                {"text": "Translation", "reading": "not on translation rows"}]
    monkeypatch.setattr(card_text, "get_cached_furigana_segments", cached)
    source = snapshot_card_lyrics([line], include_readings=True)
    assert source.readings == ((0, 1, "ひがし"), (1, 2, "きょう"))
    assert keys == [r"東京{\nt}Translation"]


def test_saved_generated_readings_survive_empty_cache_and_stale_segments_are_ignored(monkeypatch):
    line = LyricLine(0, "君", [LyricSegment(0, "君", ruby="きみ", ruby_source="generated")])
    monkeypatch.setattr(card_text, "get_cached_furigana_segments", lambda _: [{"text": "君", "reading": None}])
    assert snapshot_card_lyrics([line], include_readings=True).readings == ((0, 1, "きみ"),)
    line.text = "Different"
    assert snapshot_card_lyrics([line], include_readings=True).readings == ()


def test_ruby_keeps_original_offsets_after_emoji_and_reserves_descent_on_ruby_only_rows(qapp):
    source = CardLyrics("😀東京", styles=((1, 3, True, True, False, False),), readings=((1, 3, "とうきょう"),))
    document = _document(source.text, "", 56, 800, lyric_style=CardStyle(), source=source)
    assert document.toPlainText() == "😀\ufffc\u200b"
    assert len(document._card_ruby_handler.pieces) == 1
    base, reading, size, _ = document._card_ruby_handler.pieces[0]
    assert base.toPlainText() == "東京"
    assert reading.toPlainText() == "とうきょう"
    assert size.width() >= base.idealWidth() and size.width() >= reading.idealWidth()
    only = CardLyrics("君", readings=((0, 1, "きみ"),))
    doc = _document(only.text, "", 56, 800, lyric_style=CardStyle(), source=only)
    doc.size()
    assert doc.begin().layout().lineAt(0).descent() > 0


def test_readings_do_not_create_empty_ruby_bands_on_translation_rows(qapp):
    source = snapshot_card_lyrics(ruby_lines(), include_readings=True)
    document = _document(source.text, "", 56, 900, lyric_style=CardStyle(), source=source)
    document.size()
    first = document.begin()
    translation = first.next()
    assert translation.text() == "Let me hear your voice"
    assert document.documentLayout().blockBoundingRect(translation).height() < document.documentLayout().blockBoundingRect(first).height()


def test_inline_emphasis_also_applies_to_the_corresponding_reading(qapp):
    source = CardLyrics("東京", styles=((0, 1, True, False, False, False),), readings=((0, 2, "とうきょう"),))
    document = _document(source.text, "", 56, 800, lyric_style=CardStyle(), source=source)
    reading = document._card_ruby_handler.pieces[0][1]
    cursor = QTextCursor(reading)
    cursor.setPosition(0)
    cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
    assert cursor.charFormat().fontWeight() == QFont.Weight.Bold
    cursor.setPosition(2)
    cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    assert not cursor.charFormat().font().resolve(reading.defaultFont()).bold()


def test_invisible_baseline_strut_does_not_cast_a_stroke_or_decoration(qapp):
    source = CardLyrics("君", styles=((0, 1, False, False, True, True),), readings=((0, 1, "きみ"),))
    document = _document(source.text, "", 56, 800,
                         lyric_style=CardStyle(stroke_enabled=True, underline=True, strikeout=True), source=source)
    cursor = QTextCursor(document)
    cursor.setPosition(1)
    cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
    assert cursor.charFormat().textOutline().style() == Qt.PenStyle.NoPen
    assert not cursor.charFormat().fontUnderline()
    assert not cursor.charFormat().fontStrikeOut()


def test_readings_can_be_enabled_independently_of_source_formatting(qapp):
    source = snapshot_card_lyrics(ruby_lines(), include_readings=True)
    style = CardStyle(preserve_lrcx_formatting=False)
    image = render_lyric_card(source, style)
    assert image == render_lyric_card(source.without_formatting(), style)
    assert image != render_lyric_card(source, replace(style, include_furigana=False))
    assert render_lyric_card(source, replace(style, include_furigana=False)) == render_lyric_card(source.text, style)


def test_reading_size_changes_layout_and_excessively_long_readings_are_not_clipped(qapp):
    source = CardLyrics("君", readings=((0, 1, "きみ"),))
    assert render_lyric_card(source, CardStyle(furigana_scale=30)) != render_lyric_card(source, CardStyle(furigana_scale=80))
    long = CardLyrics("君", readings=((0, 1, "き" * 100),))
    with pytest.raises(ValueError, match="too long"):
        render_lyric_card(long, CardStyle(width=400, padding=48))


@pytest.mark.parametrize("size", CARD_SIZES.values())
@pytest.mark.parametrize("template", CARD_TEMPLATES)
def test_readings_fit_with_wrapping_quotes_cover_and_text_effects(qapp, template, size):
    import numpy as np
    source = snapshot_card_lyrics(ruby_lines(), include_readings=True)
    style = CardStyle(width=size[0], height=size[1], template=template, alignment="right", bold=True, italic=True,
                      stroke_enabled=True, stroke_color="#00ff00", shadow_enabled=True, shadow_color="#0000ff",
                      show_album_cover=True, title="Song", credit="Artist", furigana_scale=65,
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    cover = QImage(100, 100, QImage.Format.Format_RGB32)
    cover.fill(QColor("#ffffff"))
    image = render_lyric_card(source, style, cover)
    assert not image.isNull()
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)
    red, green, blue = (pixels[:, :, index] for index in range(3))
    y, x = np.where((red == blue) & (red > 32) & (green == 0))
    assert len(x)
    assert x.min() >= style.padding and x.max() < style.width - style.padding
    assert y.min() >= style.padding and y.max() < style.height - style.padding


def test_preview_png_and_presets_keep_furigana_and_toggling_off_keeps_base_text(editor, tmp_path, monkeypatch):
    editor.lines = ruby_lines()
    editor._populate_lines()
    before = deepcopy(editor.lines)
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.refresh()
    assert card.include_furigana.isChecked()
    assert card._render_key[-1].readings
    baseline = card._image.copy()
    card.furigana_scale.setValue(70)
    # Wait for the debounced render even when the runner delivers timers late.
    assert QSignalSpy(card._refresh_timer.timeout).wait(3000)
    assert card._image != baseline
    output = tmp_path / "furigana.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    card.export_image()
    assert QImage(str(output)).convertToFormat(card._image.format()) == card._image
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("Readings", True))
    card.save_preset()
    card.include_furigana.setChecked(False)
    card.refresh()
    assert not card._render_key[-1].readings
    assert not card.furigana_scale.isEnabled()
    card.preset_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("Readings")
    assert card.include_furigana.isChecked() and card.furigana_scale.value() == 70
    assert editor.lines == before


def test_cached_reading_updates_invalidate_preview_without_new_text(editor, monkeypatch):
    editor.lines = [LyricLine(0, "君")]
    editor._populate_lines()
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.refresh()
    plain = card._image.copy()
    monkeypatch.setattr(card_text, "get_cached_furigana_segments", lambda _: [{"text": "君", "reading": "きみ"}])
    card.refresh()
    assert card._image != plain
    assert card._render_key[-1].readings == ((0, 1, "きみ"),)


def test_furigana_preferences_validate_saved_values():
    from ui.lyrics_card_presets import style_from_preset
    assert style_from_preset({}).include_furigana
    values = style_from_preset({"include_furigana": False, "furigana_scale": 999})
    assert not values.include_furigana and values.furigana_scale == 80
