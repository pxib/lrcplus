from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import ui.lyrics_share_card as card_module
from ui.lyrics_card_renderer import CARD_SIZES, CARD_TEMPLATES, CardStyle, render_lyric_card
from ui.lyrics_card_presets import load_card_presets, style_from_preset
from ui.lyrics_card_text import CardLyrics
from ui.lyrics_card_vertical import _Shaper, fit_vertical_lyrics, graphemes, orientation


def example():
    first, second, third = "君の声を探して。", "星空の下で", "Translation"
    star = len(first) + 1
    translation = star + len(second) + 1
    text = "\n".join((first, second, third))
    return CardLyrics(text, styles=((0, len(first), True, False, True, True),),
                      quotes=((star, star + len(second)),), translations=((translation, len(text)),),
                      readings=((0, 1, "きみ"), (2, 3, "こえ"), (star, star + 2, "ほしぞら")))


@pytest.mark.parametrize("mode,first_on_right", [("vertical_rl", True), ("vertical_lr", False)])
def test_columns_follow_selected_direction_and_explicit_newlines(qapp, mode, first_on_right):
    layout = fit_vertical_lyrics("天地\n玄黄", CardStyle(writing_mode=mode), 400, 300)
    cells = {glyph.text: point for glyph, point in layout.glyphs}
    assert (cells["天"].x() > cells["玄"].x()) == first_on_right
    assert cells["天"].y() == cells["玄"].y()
    assert cells["地"].y() > cells["天"].y()
    assert cells["黄"].y() > cells["玄"].y()
    assert all(not glyph.sideways for glyph, _ in layout.glyphs)


def test_graphemes_preserve_combining_marks_emoji_and_utf16_offsets(qapp):
    text = "か\u3099👩‍👩‍👧‍👦A\u0301"
    assert [value for _, _, value in graphemes(text)] == ["か\u3099", "👩‍👩‍👧‍👦", "A\u0301"]
    layout = fit_vertical_lyrics(text, CardStyle(writing_mode="vertical_rl"), 500, 700)
    assert [glyph.text for glyph, _ in layout.glyphs] == ["か\u3099", "👩‍👩‍👧‍👦", "A\u0301"]
    assert [glyph.sideways for glyph, _ in layout.glyphs] == [False, False, True]
    assert orientation("한") == "U"
    assert orientation("漢") == "U"


@pytest.mark.parametrize("text", ["、", "。", "「", "ー", "ぁ"])
def test_bundled_font_uses_vertical_punctuation_and_kana_alternates(qapp, text):
    shaper = _Shaper("")
    _, _, normal = shaper.raw_path(text, shaper.font(56, False, False))
    _, _, vertical = shaper.raw_path(text, shaper.font(56, False, False, True))
    assert vertical != normal
    _, _, sideways = shaper.shape(text, 56, False, False, orientation(text))
    assert not sideways


def test_ruby_runs_vertically_beside_base_and_decorations_cross_its_padding(qapp):
    source = CardLyrics("君の", styles=((0, 2, False, False, True, True),), readings=((0, 1, "きみのこえ"),))
    layout = fit_vertical_lyrics(source.text, CardStyle(writing_mode="vertical_rl"), 300, 400, source)
    base = [(glyph, point) for glyph, point in layout.glyphs if glyph.role == "base"]
    ruby = [(glyph, point) for glyph, point in layout.glyphs if glyph.role == "ruby"]
    assert "".join(glyph.text for glyph, _ in ruby) == "きみのこえ"
    assert all(point.x() > base[0][1].x() + base[0][0].pixels for _, point in ruby)
    assert [point.y() for _, point in ruby] == sorted(point.y() for _, point in ruby)
    assert base[0][1].y() > 0  # Long reading creates padding above the base.
    for y in range(1, int(base[-1][1].y() + base[-1][0].advance)):
        assert layout.path.contains(QPointF(58, y))
        assert layout.path.contains(QPointF(29, y))


def test_sideways_words_preserve_shaping_and_wrap_at_word_boundaries(qapp):
    layout = fit_vertical_lyrics("天地 Love music", CardStyle(writing_mode="vertical_rl", font_size=40), 600, 220)
    cells = {glyph.text: (glyph, point) for glyph, point in layout.glyphs}
    assert cells["Love"][0].sideways and cells["music"][0].sideways
    assert cells["Love"][0].advance > cells["Love"][0].pixels
    assert cells["Love"][0].pixels == cells["music"][0].pixels


def test_wrapping_keeps_closing_punctuation_with_preceding_character(qapp):
    layout = fit_vertical_lyrics("天地。玄黄", CardStyle(writing_mode="vertical_rl"), 500, 112)
    cells = {glyph.text: point for glyph, point in layout.glyphs}
    assert cells["地"].x() == cells["。"].x()
    assert cells["。"].y() > cells["地"].y()


def test_auto_fit_handles_overflow_and_crlf_without_splitting_ruby(qapp):
    style = CardStyle(writing_mode="vertical_rl", font_size=96)
    fitted = fit_vertical_lyrics("天地玄黄" * 16, style, 260, 260)
    assert fitted.defaultFont().pixelSize() < 96
    assert fitted.size().width() <= 260 and fitted.size().height() <= 260
    for source in (CardLyrics("君", readings=((0, 1, "あ" * 1000),)), CardLyrics("君\n" * 200)):
        with pytest.raises(ValueError, match="vertical lyrics or furigana are too long"):
            fit_vertical_lyrics(source.text, style, 300, 300, source)
    plain = fit_vertical_lyrics("天地\n玄黄", style, 500, 500)
    windows = fit_vertical_lyrics("天地\r\n玄黄", style, 500, 500)
    assert plain.path == windows.path
    invalid = CardLyrics("君の声", readings=((0, 0, "きみ"),))
    layout = fit_vertical_lyrics(invalid.text, style, 500, 500, invalid)
    assert all(glyph.role == "base" for glyph, _ in layout.glyphs)


def test_effects_never_change_vertical_fitting_or_positions(qapp):
    source = example()
    style = CardStyle(writing_mode="vertical_rl", font_size=96, bold=True, italic=True)
    def geometry(current):
        layout = fit_vertical_lyrics(source.text, current, 500, 400, source)
        return (layout.defaultFont().pixelSize(), layout.size(), layout.path,
                [(glyph.text, glyph.pixels, point) for glyph, point in layout.glyphs])
    baseline = geometry(style)
    assert geometry(replace(style, stroke_enabled=True, stroke_width=12)) == baseline
    assert geometry(replace(style, shadow_enabled=True, shadow_blur=30, shadow_offset_x=-40,
                            shadow_offset_y=40, stroke_enabled=True, stroke_width=12)) == baseline


@pytest.mark.parametrize("mode", ["vertical_rl", "vertical_lr"])
@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("size", CARD_SIZES.values())
def test_vertical_cards_fit_every_template_with_ruby_styles_artwork_and_effects(qapp, template, mode, size):
    style = CardStyle(width=size[0], height=size[1], template=template, writing_mode=mode,
                      alignment="right", bold=True, italic=True, padding=120,
                      stroke_enabled=True, shadow_enabled=True, watermark_enabled=True,
                      title="Song", credit="Artist", album="Album", show_album_cover=True,
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    cover = QImage(160, 160, QImage.Format.Format_RGB32)
    cover.fill(QColor("#234567"))
    image = render_lyric_card(example(), style, cover)
    assert (image.width(), image.height()) == size
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    values = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(size[1], size[0], 4)
    y, x = np.where(np.all(values[:, :, :3] == (255, 0, 255), axis=2))
    assert len(x)
    assert x.min() >= style.padding and x.max() < style.width - style.padding
    assert y.min() >= style.padding and y.max() < style.height - style.padding


def test_vertical_controls_export_and_presets_preserve_song_and_legacy_defaults(editor, monkeypatch, tmp_path):
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.refresh()
    horizontal = card._image.copy()
    card.writing_mode.setCurrentIndex(card.writing_mode.findData("vertical_rl"))
    card.refresh()
    assert card._image != horizontal
    card.copy_image()
    assert QApplication.clipboard().image() == card._image
    path = tmp_path / "vertical.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(path), ""))
    card.export_image()
    assert QImage(str(path)).convertToFormat(card._image.format()) == card._image
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("Vertical", True))
    card.save_preset()
    assert load_card_presets()["Vertical"]["writing_mode"] == "vertical_rl"
    title = card.title_edit.text()
    card.writing_mode.setCurrentIndex(0)
    card.preset_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("Vertical")
    assert card.current_style().writing_mode == "vertical_rl"
    assert card.title_edit.text() == title
    assert style_from_preset({}).writing_mode == "horizontal"
    assert style_from_preset({"writing_mode": []}).writing_mode == "horizontal"
