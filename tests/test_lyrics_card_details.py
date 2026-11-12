from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage

import ui.lyrics_card_renderer as renderer
from ui.lyrics_card_renderer import CARD_SIZES, CARD_TEMPLATES, CardStyle, render_lyric_card
from ui.lyrics_card_presets import style_from_preset


def capture_text_boxes(monkeypatch):
    boxes = {}
    original = renderer._paint_lyrics

    def capture(painter, document, x, y, color, style):
        left, top, right, bottom = renderer._effect_insets(style)
        bounds = QRectF(x - left, y - top,
            document.textWidth() + left + right, document.size().height() + top + bottom)
        boxes[document.toPlainText()] = bounds.intersected(painter.clipBoundingRect()) if painter.hasClipping() else bounds
        original(painter, document, x, y, color, style)

    monkeypatch.setattr(renderer, "_paint_lyrics", capture)
    return boxes


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4).copy()


@pytest.mark.parametrize("size", CARD_SIZES.values())
@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("placement", ["below_title", "beside_cover"])
def test_metadata_wraps_below_title_without_colliding_with_cover_or_lyrics(qapp, monkeypatch, size, template, placement):
    boxes = capture_text_boxes(monkeypatch)
    style = CardStyle(width=size[0], height=size[1], template=template, details_placement=placement,
        title="夜の街で — City lights", credit="Artist 花隈千冬", album="Long album name for wrapping",
        show_album_cover=True, cover_size=240, padding=120, stroke_enabled=True,
        shadow_enabled=True, shadow_blur=8, shadow_offset_x=-10, shadow_offset_y=10)
    cover = QImage(240, 240, QImage.Format.Format_RGB32)
    cover.fill(QColor("#12ed45"))
    lyric = "A lyric line\nSecond lyric"
    image = pixels(render_lyric_card(lyric, style, cover))
    cy, cx = np.where(np.all(image[:, :, :3] == (18, 237, 69), axis=2))
    cover_rect = QRectF(cx.min(), cy.min(), cx.max() - cx.min() + 1, cy.max() - cy.min() + 1)
    title = boxes[style.title]
    details = boxes[f"{style.credit} · {style.album}"]
    assert title.bottom() <= details.top()
    for text_box in boxes.values():
        assert not text_box.intersects(cover_rect)
        assert QRectF(0, 0, style.width, style.height).contains(text_box)
    assert not boxes[lyric].intersects(title)
    assert not boxes[lyric].intersects(details)
    if placement == "beside_cover":
        assert title.left() > cover_rect.right()
        assert details.left() > cover_rect.right()


@pytest.mark.parametrize("template", CARD_TEMPLATES)
def test_missing_or_disabled_cover_uses_below_title_layout(qapp, template):
    style = CardStyle(template=template, title="Song", credit="Artist", album="Album",
                      details_placement="beside_cover", show_album_cover=True)
    baseline = render_lyric_card("Lyrics", replace(style, details_placement="below_title"))
    assert render_lyric_card("Lyrics", style) == baseline
    assert render_lyric_card("Lyrics", style, QImage()) == baseline
    assert render_lyric_card("Lyrics", replace(style, show_album_cover=False)) == baseline


@pytest.mark.parametrize("field", ["title", "credit", "album"])
@pytest.mark.parametrize("template", CARD_TEMPLATES)
def test_each_metadata_field_receives_stroke_and_shadow(qapp, monkeypatch, field, template):
    boxes = capture_text_boxes(monkeypatch)
    text = "Metadata text"
    style = CardStyle(template=template, **{field: text}, background_mode="Solid",
        stroke_enabled=True, stroke_color="#00ff00", stroke_width=4,
        shadow_enabled=True, shadow_color="#ff0000", shadow_opacity=100,
        shadow_blur=0, shadow_offset_x=24, shadow_offset_y=24,
        colors=("#000000", "#000000", "#000000", "#ffffff", "#ffffff", "#888888"))
    image = render_lyric_card("Lyrics", style)
    region = pixels(image.copy(boxes[text].toAlignedRect()))[:, :, :3]
    assert np.all(region == (0, 255, 0), axis=2).sum() > 20
    assert np.all(region == (255, 0, 0), axis=2).sum() > 20
    assert np.all(region == (255, 255, 255), axis=2).sum() > 20


def test_details_presets_validate_placement_and_never_restore_album_text():
    assert style_from_preset({}).details_placement == "below_title"
    assert style_from_preset({"details_placement": []}).details_placement == "below_title"
    style = style_from_preset({"details_placement": "beside_cover", "album": "Another song's album"})
    assert style.details_placement == "beside_cover"
    assert not style.album


def test_details_controls_require_cover_option_and_preserve_album_on_preset_load(editor, qapp, monkeypatch):
    import ui.lyrics_share_card as card_module
    from ui.lyrics_card_presets import load_card_presets
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    beside = card.details_placement.model().item(card.details_placement.findData("beside_cover"))
    assert not beside.isEnabled()
    card.show_album_cover.setChecked(True)
    assert beside.isEnabled()
    card.details_placement.setCurrentText("Beside cover")
    card.album_edit.setText("First album")
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("Details", True))
    card.save_preset()
    stored = load_card_presets()["Details"]
    assert stored["details_placement"] == "beside_cover"
    assert "album" not in stored
    card.album_edit.setText("Current album")
    card.details_placement.setCurrentText("Below title")
    card.show_album_cover.setChecked(False)
    card.preset_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("Details")
    assert card.current_style().details_placement == "beside_cover"
    assert card.current_style().album == "Current album"
    assert card.show_album_cover.isChecked()
    assert beside.isEnabled()


def test_metadata_typography_changes_fonts_sizes_and_colors_independently(qapp, monkeypatch):
    from PySide6.QtGui import QFontDatabase
    families = QFontDatabase.families()
    title_family, credit_family = families[0], families[-1]
    captured = {}
    def capture(painter, document, x, y, color, style):
        captured[document.toPlainText()] = (document.defaultFont().family(),
                                           document.defaultFont().pixelSize(), color)
    monkeypatch.setattr(renderer, "_paint_lyrics", capture)
    style = CardStyle(title="Title", credit="Artist", font_size=56,
                      title_font_family=title_family, title_font_size=48, title_color="#ff1234",
                      credit_font_family=credit_family, credit_font_size=8, credit_color="#12ff34")
    render_lyric_card("Lyrics", style)
    assert captured["Title"] == (title_family, 48, "#ff1234")
    assert captured["Artist"] == (credit_family, 8, "#12ff34")
    assert captured["Lyrics"][1:] == (56, renderer.theme_colors(style.theme, style.template)[2])


def test_typography_presets_validate_new_fields():
    style = style_from_preset({"title_font_family": "A" * 250, "credit_font_family": [],
                              "title_font_size": 500, "credit_font_size": True,
                              "title_color": "bad-color", "credit_color": "red",
                              "width": 1080, "height": 1920})
    assert len(style.title_font_family) == 200
    assert style.credit_font_family == ""
    assert style.title_font_size == 96
    assert style.credit_font_size == 0
    assert style.title_color == ""
    assert style.credit_color == "#ff0000"
    assert (style.width, style.height) == (1080, 1920)
