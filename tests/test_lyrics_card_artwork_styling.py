from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

import ui.lyrics_share_card as card_module
from ui.lyrics_card_templates import paint_cover
from ui.lyrics_card_renderer import CARD_TEMPLATES, CardStyle, render_lyric_card
from ui.lyrics_card_presets import save_card_preset, style_from_preset


def cover(width=160, height=160):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#00ff00"))
    return image


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4).copy()


@pytest.mark.parametrize("scale", (1, 3))
def test_artwork_corners_and_border_follow_actual_image_bounds(qapp, scale):
    image = QImage(240 * scale, 240 * scale, QImage.Format.Format_RGB32)
    image.fill(QColor("#faf5eb"))
    painter = QPainter(image)
    painter.scale(scale, scale)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    style = CardStyle(cover_radius=32, cover_border_width=6, cover_border_color="#ff0000", export_scale=scale)
    paint_cover(painter, QRectF(20, 20, 200, 200), cover(160, 80), style)
    painter.end()
    assert image.pixelColor(20 * scale, 70 * scale) == QColor("#faf5eb")
    assert image.pixelColor(120 * scale, 40 * scale) == QColor("#faf5eb")
    assert image.pixelColor(120 * scale, 72 * scale) == QColor("#ff0000")
    assert image.pixelColor(120 * scale, 120 * scale) == QColor("#00ff00")


@pytest.mark.parametrize("scale", (1, 3))
def test_artwork_shadow_is_soft_and_extends_outside_the_unchanged_cover(qapp, scale):
    image = QImage(400 * scale, 400 * scale, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.scale(scale, scale)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    style = CardStyle(cover_radius=24, cover_shadow_enabled=True, cover_shadow_opacity=60,
                      cover_shadow_blur=8, cover_shadow_offset_x=10, cover_shadow_offset_y=10, export_scale=scale)
    paint_cover(painter, QRectF(100, 100, 200, 200), cover(), style)
    painter.end()
    values = pixels(image)[:, :, :3]
    y, x = np.where(np.any(values != 255, axis=2))
    assert x.min() < 100 * scale and x.max() > 300 * scale
    assert y.min() < 100 * scale and y.max() > 300 * scale
    cy, cx = np.where(np.all(values == (0, 255, 0), axis=2))
    assert cx.max() - cx.min() + 1 == 200 * scale
    assert cy.max() - cy.min() + 1 == 200 * scale
    grey = values[np.all(values == values[:, :, :1], axis=2) & (values[:, :, 0] < 255)]
    assert len(grey) and len(np.unique(grey[:, 0])) > 10
    assert image.pixelColor(200 * scale, 200 * scale) == QColor("#00ff00")


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("aspect", (1, 2))
def test_toggling_shadow_preserves_cover_size_in_every_template(qapp, template, aspect):
    style = CardStyle(template=template, show_album_cover=True, cover_size=240,
                      title="Title", credit="Artist", cover_radius=24, cover_border_width=4,
                      cover_shadow_blur=16, cover_shadow_offset_x=-20, cover_shadow_offset_y=20)
    artwork = cover(160 * aspect, 160)
    bounds = []
    for enabled in (False, True):
        values = pixels(render_lyric_card("Lyrics", replace(style, cover_shadow_enabled=enabled), artwork))[:, :, :3]
        y, x = np.where(np.all(values == (0, 255, 0), axis=2))
        assert len(x)
        bounds.append((x.max() - x.min(), y.max() - y.min(), len(x)))
    assert bounds[0] == bounds[1]


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("placement", ("left", "right"))
def test_artwork_placement_keeps_styled_cover_beside_metadata(qapp, template, placement):
    style = CardStyle(template=template, show_album_cover=True, cover_placement=placement,
                      title="Title", credit="Artist", cover_size=200, cover_radius=24,
                      cover_border_width=4, cover_border_color="#0000ff", cover_shadow_enabled=True,
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ff0000", "#888888"))
    values = pixels(render_lyric_card("Lyrics", style, cover()))[:, :, :3]
    cy, cx = np.where(np.all(values == (0, 255, 0), axis=2))
    ty, tx = np.where(np.all(values == (255, 255, 255), axis=2))
    ly, lx = np.where(np.all(values == (255, 0, 255), axis=2))
    assert len(cx) and len(tx) and len(lx)
    assert cx.max() < tx.min() if placement == "left" else cx.min() > tx.max()
    assert cy.max() < ly.min() or cy.min() > ly.max()
    assert render_lyric_card("Lyrics", style) == render_lyric_card(
        "Lyrics", replace(style, show_album_cover=False, cover_placement="auto"))


def test_artwork_styling_controls_presets_and_export(editor, qapp, monkeypatch, tmp_path):
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.show_album_cover.setChecked(True)
    card.cover_radius.setValue(40)
    card.cover_border_width.setValue(5)
    monkeypatch.setattr(card_module.QColorDialog, "getColor", lambda *args: QColor("#ff1234"))
    card.choose_cover_border_color()
    card.cover_shadow_enabled.setChecked(True)
    card.cover_shadow_blur.setValue(12)
    card.cover_shadow_opacity.setValue(50)
    card.cover_shadow_offset_x.setValue(-8)
    card.cover_shadow_offset_y.setValue(12)
    card.cover_placement.setCurrentIndex(card.cover_placement.findData("right"))
    assert not card.details_placement.isEnabled()
    style = card.current_style()
    save_card_preset("Artwork", style)
    card._reload_presets()
    card.cover_radius.setValue(0)
    card.cover_border_width.setValue(0)
    card.cover_shadow_enabled.setChecked(False)
    card.cover_placement.setCurrentIndex(0)
    assert card.details_placement.isEnabled()
    card.preset_combo.setCurrentText("Artwork")
    assert card.current_style() == style
    card.album_art.image = cover()
    monkeypatch.setattr(card, "_configure_album_art", lambda: None)
    card.album_art.loading = False
    card.refresh()
    target = tmp_path / "styled.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    card.export_image()
    assert QImage(str(target)).convertToFormat(card._image.format()) == card._image
    card.show_album_cover.setChecked(False)
    assert card.current_style().cover_radius == 40


def test_artwork_preset_defaults_and_validation():
    default = style_from_preset({})
    assert default.cover_placement == "auto"
    assert default.cover_radius == default.cover_border_width == 0
    assert not default.cover_shadow_enabled
    style = style_from_preset({"cover_radius": 500, "cover_border_width": -1, "cover_border_color": [],
                              "cover_shadow_enabled": "yes", "cover_shadow_blur": 99,
                              "cover_shadow_opacity": -1, "cover_shadow_offset_x": -99,
                              "cover_placement": []})
    assert style.cover_radius == 120 and style.cover_border_width == 0
    assert style.cover_border_color == "#ffffff"
    assert not style.cover_shadow_enabled and style.cover_shadow_blur == 16
    assert style.cover_shadow_opacity == 0 and style.cover_shadow_offset_x == -20
    assert style.cover_placement == "auto"
