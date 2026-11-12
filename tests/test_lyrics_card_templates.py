from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage

import ui.lyrics_card_renderer as renderer
import ui.lyrics_share_card as card_module
from ui.lyrics_card_renderer import CardStyle, render_lyric_card
from ui.lyrics_card_presets import load_card_presets
from ui.lyrics_card_text import CardLyrics


NEW_TEMPLATES = ("Polaroid", "Editorial", "Cinema", "Notebook", "Ticket", "Art Deco",
                 "Streaming", "Receipt", "Mixtape")


def solid_image(color):
    image = QImage(240, 240, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


@pytest.mark.parametrize("template", NEW_TEMPLATES)
def test_new_template_preview_export_and_preset_keep_explicit_theme(editor, monkeypatch, tmp_path, template):
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.template_combo.setCurrentText(template)
    assert card.theme_combo.currentText() == ("Midnight" if template in ("Cinema", "Art Deco", "Streaming") else "Paper")
    card.theme_combo.setCurrentText("Rose")
    card.refresh()
    assert not card._image.isNull()
    target = tmp_path / f"{template}.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(target), ""))
    card.export_image()
    assert QImage(str(target)).convertToFormat(card._image.format()) == card._image
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("New template", True))
    card.save_preset()
    assert load_card_presets()["New template"]["template"] == template
    card.template_combo.setCurrentText("Classic")
    card.theme_combo.setCurrentText("Midnight")
    card.preset_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("New template")
    assert card.current_style().template == template
    assert card.current_style().theme == "Rose"


def test_cinema_cover_backdrop_respects_solid_and_custom_image_backgrounds(qapp, tmp_path):
    style = CardStyle(template="Cinema", title="Song", credit="Artist", show_album_cover=True)
    red, blue = solid_image("red"), solid_image("blue")
    # This region excludes the cover thumbnail; only the blurred backdrop changes.
    def middle(image):
        return image.copy(0, 330, style.width, 500)
    assert middle(render_lyric_card("A lyric", style, red)) != middle(render_lyric_card("A lyric", style, blue))
    no_art = replace(style, show_album_cover=False)
    assert render_lyric_card("A lyric", no_art, red) == render_lyric_card("A lyric", no_art, blue)
    path = tmp_path / "custom-background.png"
    assert solid_image("#246852").save(str(path))
    for mode in ("Solid", "Image"):
        custom = replace(style, background_mode=mode, image_path=str(path))
        assert middle(render_lyric_card("A lyric", custom, red)) == middle(render_lyric_card("A lyric", custom, blue))


def test_editorial_uses_a_larger_heading_without_changing_lyric_font(qapp, monkeypatch):
    sizes = {}
    def capture(painter, document, x, y, color, style):
        sizes[document.toPlainText()] = document.defaultFont().pixelSize()
    monkeypatch.setattr(renderer, "_paint_lyrics", capture)
    style = CardStyle(template="Editorial", title="Title", credit="Artist", font_size=56)
    render_lyric_card("Lyrics", style)
    assert sizes["Title"] == 64
    assert sizes["Lyrics"] == 56
    assert sizes["Artist"] == 26


def test_polaroid_places_song_details_and_lyrics_below_the_photo(qapp):
    style = CardStyle(template="Polaroid", title="Title", credit="Artist", show_album_cover=True,
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ff0000", "#888888"))
    image = render_lyric_card("Lyrics", style, solid_image("#00ff00"))
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    values = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)
    def rows(color):
        y, _ = np.where(np.all(values[:, :, :3] == color, axis=2))
        assert len(y)
        return y
    cover, title, artist, lyric = (rows(color) for color in ((0, 255, 0), (255, 255, 255),
                                                           (255, 0, 0), (255, 0, 255)))
    assert cover.max() < title.min() < title.max() < artist.min() < artist.max() < lyric.min()


@pytest.mark.parametrize("placement", ("below_title", "beside_cover"))
@pytest.mark.parametrize("template", ("Ticket", "Streaming"))
def test_footer_templates_keep_metadata_and_artwork_below_lyrics(qapp, monkeypatch, placement, template):
    positions = {}
    def capture(painter, document, x, y, color, style):
        positions[document.toPlainText()] = (y, y + document.size().height())
    monkeypatch.setattr(renderer, "_paint_lyrics", capture)
    style = CardStyle(template=template, title="Title", credit="Artist", show_album_cover=True,
                      details_placement=placement, vertical_position=100)
    render_lyric_card("Lyrics", style, solid_image("green"))
    assert positions["Lyrics"][1] < positions["Title"][0] < positions["Artist"][0]


@pytest.mark.parametrize("template", ("Notebook", "Ticket", "Art Deco", "Streaming", "Receipt", "Mixtape"))
def test_added_templates_paint_distinct_surfaces_and_keep_custom_background(qapp, tmp_path, template):
    background = solid_image("#246852")
    path = tmp_path / "background.png"
    assert background.save(str(path))
    style = CardStyle(template=template, background_mode="Image", image_path=str(path), image_dim=0)
    image = render_lyric_card("A lyric", style)
    assert image.pixelColor(0, 0) == QColor("#246852")
    assert image != render_lyric_card("A lyric", replace(style, template="Classic"))
    # Each template must draw its own decoration outside the content area.
    decoration = (style.padding, style.padding, style.width - 2 * style.padding, 144) if template == "Mixtape" else (16, 16, 32, style.height - 32)
    assert image.copy(*decoration) != renderer.card_background(
        style, renderer.theme_colors(style.theme, style.template)).copy(*decoration)


@pytest.mark.parametrize("position", (0, 50, 100))
@pytest.mark.parametrize("spacing", (100, 160))
@pytest.mark.parametrize("excerpt", ("wrapped", "fitted", "formatted"))
def test_notebook_rules_follow_every_rendered_lyric_baseline(qapp, monkeypatch, position, spacing, excerpt):
    if excerpt == "formatted":
        text = "Night sky\nA smaller translation\nOne more line"
        source = CardLyrics(text, translations=((10, 31),), readings=((0, 5, "night"),))
    else:
        source = ("A long lyric that wraps onto another row and keeps going\n" *
                  (12 if excerpt == "fitted" else 2)).strip()
    baselines = []
    paint = renderer._paint_lyrics
    def capture(painter, document, x, y, color, style):
        block = document.begin()
        while block.isValid():
            layout = block.layout()
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                baselines.append(y + layout.position().y() + line.y() + line.ascent())
            block = block.next()
        paint(painter, document, x, y, color, style)
    monkeypatch.setattr(renderer, "_paint_lyrics", capture)
    style = CardStyle(template="Notebook", width=1080, height=1350, font_size=96,
                      line_spacing=spacing, vertical_position=position)
    image = render_lyric_card(source, style)
    background = renderer.card_background(style, renderer.theme_colors(style.theme, style.template))
    # Sample just left of the text box, where only the paper rules are painted.
    x = style.padding + 1
    rules = [y for y in range(int(baselines[0]), min(style.height, int(baselines[-1]) + 5))
             if image.pixelColor(x, y) != background.pixelColor(x, y)]
    assert len(baselines) > 1
    for baseline in baselines:
        assert any(abs(row - (baseline + 2)) <= 1 for row in rules)
    assert all(min(abs(row - (baseline + 2)) for baseline in baselines) <= 1 for row in rules)
