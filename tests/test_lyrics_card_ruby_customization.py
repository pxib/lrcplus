from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

import ui.lyrics_share_card as card_module
from ui.lyrics_card_decorations import _glyphs
from ui.lyrics_card_renderer import CARD_TEMPLATES, CardStyle, _document, _fitted_document, _paint_lyrics, render_lyric_card
from ui.lyrics_card_presets import load_card_presets, style_from_preset
from ui.lyrics_card_ruby import RUBY_INDEX, RUBY_OBJECT
from ui.lyrics_card_text import CardLyrics
from ui.lyrics_card_vertical import fit_vertical_lyrics


POSITIONS = (("horizontal", "above"), ("horizontal", "below"),
             ("vertical_rl", "right"), ("vertical_lr", "left"))


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4).copy()


def layout_and_boxes(style):
    source = CardLyrics("君", readings=((0, 1, "きみ"),))
    if style.writing_mode == "horizontal":
        layout = _fitted_document(source.text, style, 56, 300, 300, styled=True, source=source)
        layout.size()
        handler = layout._card_ruby_handler
        _, _, fmt, _, left, right, baseline = next(g for g in _glyphs(layout) if g[2].objectType() == RUBY_OBJECT)
        index = int(fmt.property(RUBY_INDEX))
        base, ruby, _, _ = handler.pieces[index]
        base_y, ruby_y = handler.child_positions(index, baseline)
        boxes = {"base": QRectF(left + (right - left - base.size().width()) / 2, base_y,
                                base.size().width(), base.size().height()),
                 "ruby": QRectF(left + (right - left - ruby.size().width()) / 2, ruby_y,
                                ruby.size().width(), ruby.size().height())}
    else:
        layout = fit_vertical_lyrics(source.text, style, 300, 300, source)
        boxes = {"base": layout.base_path.boundingRect(), "ruby": layout.ruby_path.boundingRect()}
    return layout, boxes


def paint(style):
    layout, boxes = layout_and_boxes(style)
    image = QImage(512, 512, QImage.Format.Format_RGBA8888)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _paint_lyrics(painter, layout, 100, 100, QColor("white"), style)
    painter.end()
    return image, {role: rect.translated(100, 100) for role, rect in boxes.items()}


@pytest.mark.parametrize("mode,position", POSITIONS)
def test_ruby_color_position_and_extra_gap(qapp, mode, position):
    style = CardStyle(width=512, height=512, writing_mode=mode, ruby_position=position,
                      ruby_gap=4, ruby_color="#ff0000")
    image, _ = paint(style)
    values = pixels(image)[:, :, :3]
    ry, rx = np.where(np.all(values == (255, 0, 0), axis=2))
    by, bx = np.where(np.all(values == (255, 255, 255), axis=2))
    assert len(rx) and len(bx)
    if position == "above":
        assert ry.max() < by.min()
    elif position == "below":
        assert ry.min() > by.max()
    elif position == "left":
        assert rx.max() < bx.min()
    else:
        assert rx.min() > bx.max()
    _, small = layout_and_boxes(style)
    _, large = layout_and_boxes(replace(style, ruby_gap=14))
    def gap(boxes):
        if position == "above":
            return boxes["base"].top() - boxes["ruby"].bottom()
        if position == "below":
            return boxes["ruby"].top() - boxes["base"].bottom()
        if position == "left":
            return boxes["base"].left() - boxes["ruby"].right()
        return boxes["ruby"].left() - boxes["base"].right()
    assert gap(large) - gap(small) == pytest.approx(10)


@pytest.mark.parametrize("position", ["above", "below"])
def test_horizontal_ruby_keeps_base_baseline_with_unannotated_text(qapp, position):
    source = CardLyrics("君の声を探して 君の声を探して", readings=((0, 1, "きみ"), (9, 10, "きみ")))
    document = _document(source.text, "", 56, 260, lyric_style=CardStyle(ruby_position=position), source=source)
    document.size()
    handler = document._card_ruby_handler
    objects = [g for g in _glyphs(document) if g[2].objectType() == RUBY_OBJECT]
    assert len(objects) == 2
    for _, _, fmt, _, _, _, baseline in objects:
        index = int(fmt.property(RUBY_INDEX))
        base = handler.pieces[index][0]
        base_y, _ = handler.child_positions(index, baseline)
        assert base_y + base.begin().layout().lineAt(0).ascent() == pytest.approx(baseline)


@pytest.mark.parametrize("mode,position", POSITIONS)
@pytest.mark.parametrize("effect", ["stroke", "shadow"])
@pytest.mark.parametrize("inherit", [True, False])
def test_reading_can_opt_out_of_effects_while_base_keeps_them(qapp, mode, position, effect, inherit):
    style = CardStyle(width=512, height=512, writing_mode=mode, ruby_position=position,
                      ruby_gap=40, ruby_color="#ff0000", stroke_enabled=effect == "stroke",
                      stroke_color="#00ff00", stroke_width=3, shadow_enabled=effect == "shadow",
                      shadow_color="#0000ff", shadow_blur=0, shadow_opacity=100,
                      shadow_offset_x=14, shadow_offset_y=14,
                      **{f"ruby_inherit_{effect}": inherit})
    image, boxes = paint(style)
    rgb = (0, 255, 0) if effect == "stroke" else (0, 0, 255)
    def effect_pixels(rect):
        if effect == "shadow":
            rect = rect.united(rect.translated(14, 14))
        rect = rect.adjusted(-6, -6, 6, 6).toAlignedRect()
        return np.all(pixels(image.copy(rect))[:, :, :3] == rgb, axis=2).sum()
    assert effect_pixels(boxes["base"]) > 10
    assert (effect_pixels(boxes["ruby"]) > 10) == inherit


@pytest.mark.parametrize("mode,position", POSITIONS)
@pytest.mark.parametrize("effect", ["stroke", "shadow"])
def test_effect_opt_out_keeps_reading_pixels_unchanged(qapp, mode, position, effect):
    style = CardStyle(width=512, height=512, writing_mode=mode, ruby_position=position,
                      ruby_gap=40, ruby_color="#ff0000", ruby_inherit_stroke=False,
                      ruby_inherit_shadow=False, stroke_width=3, shadow_blur=0,
                      shadow_offset_x=14, shadow_offset_y=14)
    baseline, boxes = paint(style)
    changed, _ = paint(replace(style, **{effect + "_enabled": True}))
    region = boxes["ruby"].adjusted(-5, -5, 5, 5).toAlignedRect()
    assert baseline.copy(region) == changed.copy(region)


@pytest.mark.parametrize("mode,position", POSITIONS)
def test_ruby_appearance_preserves_geometry_and_auto_matches_default_side(qapp, mode, position):
    source = CardLyrics("君の声を探して\n星空の下で", readings=((0, 1, "きみ"), (2, 3, "こえ")))
    style = CardStyle(writing_mode=mode, ruby_position=position, font_size=96)
    def geometry(current):
        layout = (_fitted_document(source.text, current, current.font_size, 300, 250, styled=True, source=source)
                  if mode == "horizontal" else fit_vertical_lyrics(source.text, current, 300, 250, source))
        return (layout.defaultFont().pixelSize(), layout.size(), layout.textWidth())
    assert geometry(style) == geometry(replace(style, ruby_color="#ff0000",
        ruby_inherit_stroke=False, ruby_inherit_shadow=False, stroke_enabled=True,
        stroke_width=12, shadow_enabled=True, shadow_blur=30))
    default = "above" if mode == "horizontal" else "right"
    assert render_lyric_card(source, replace(style, ruby_position="auto")) == render_lyric_card(source, replace(style, ruby_position=default))


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("mode,position", POSITIONS)
def test_new_ruby_positions_fit_all_templates_with_effects_and_watermark(qapp, template, mode, position):
    source = CardLyrics("君の声を探して\n星空の下で", styles=((0, 15, True, False, True, True),),
                        readings=((0, 1, "きみ"), (2, 3, "こえ"), (8, 10, "ほしぞら")))
    style = CardStyle(template=template, writing_mode=mode, ruby_position=position,
                      ruby_gap=12, ruby_color="#ff0000", ruby_inherit_stroke=False,
                      ruby_inherit_shadow=False, stroke_enabled=True, shadow_enabled=True,
                      watermark_enabled=True, title="Song", credit="Artist", padding=120)
    image = render_lyric_card(source, style)
    values = pixels(image)[:, :, :3]
    y, x = np.where(np.all(values == (255, 0, 0), axis=2))
    assert len(x)
    assert x.min() >= style.padding and x.max() < style.width - style.padding
    assert y.min() >= style.padding and y.max() < style.height - style.padding


@pytest.mark.parametrize("mode,position", [("horizontal", "below"), ("vertical_rl", "left")])
def test_ruby_controls_update_preview_export_and_presets(editor, monkeypatch, tmp_path, mode, position):
    from lyrics.editor_source import parse_editor_source
    editor.lines = parse_editor_source("[format:LRCX]\n[00:01.000]{君|きみ}の声", ".lrcx").lines
    editor._populate_lines()
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.writing_mode.setCurrentIndex(card.writing_mode.findData(mode))
    assert card.ruby_position.count() == 3
    card.ruby_position.setCurrentIndex(card.ruby_position.findData(position))
    card.ruby_gap.setValue(9)
    card.ruby_custom_color.setChecked(True)
    monkeypatch.setattr(card_module.QColorDialog, "getColor", lambda *_: QColor("#ff5577"))
    card.choose_ruby_color()
    card.ruby_inherit_stroke.setChecked(False)
    card.ruby_inherit_shadow.setChecked(False)
    card.refresh()
    expected = card.current_style()
    assert expected.ruby_color == "#ff5577"
    target = tmp_path / "ruby.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(target), ""))
    card.export_image()
    assert QImage(str(target)).convertToFormat(card._image.format()) == card._image
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("Ruby", True))
    card.save_preset()
    stored = load_card_presets()["Ruby"]
    for key in ("ruby_position", "ruby_gap", "ruby_color", "ruby_inherit_stroke", "ruby_inherit_shadow"):
        assert stored[key] == getattr(expected, key)
    card.ruby_position.setCurrentIndex(0)
    card.ruby_gap.setValue(0)
    card.ruby_custom_color.setChecked(False)
    card.preset_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("Ruby")
    assert card.current_style() == expected
    card.include_furigana.setChecked(False)
    assert not card.ruby_gap.isEnabled() and not card.ruby_position.isEnabled()
    card.include_furigana.setChecked(True)
    card.writing_mode.setCurrentIndex(card.writing_mode.findData("vertical_rl" if mode == "horizontal" else "horizontal"))
    assert card.ruby_position.currentData() == "auto"


def test_ruby_preset_defaults_and_invalid_values_are_safe():
    defaults = style_from_preset({})
    assert (defaults.ruby_position, defaults.ruby_gap, defaults.ruby_color) == ("auto", 0, "")
    assert defaults.ruby_inherit_stroke and defaults.ruby_inherit_shadow
    invalid = style_from_preset({"ruby_position": [], "ruby_gap": True, "ruby_color": "bad-color",
                                 "ruby_inherit_stroke": "false", "ruby_inherit_shadow": None})
    assert (invalid.ruby_position, invalid.ruby_gap, invalid.ruby_color) == ("auto", 0, "")
    assert invalid.ruby_inherit_stroke and invalid.ruby_inherit_shadow
    assert style_from_preset({"ruby_gap": 999}).ruby_gap == 40
    assert style_from_preset({"writing_mode": "horizontal", "ruby_position": "left"}).ruby_position == "auto"
