from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from lyrics.models import LyricLine
import ui.lyrics_share_card as card_module
from ui.lyrics_share_card import CARD_SIZES, CARD_THEMES, CARD_TEMPLATES, CardStyle, render_lyric_card, save_card_png
from ui.lyrics_card_renderer import card_background, theme_colors


@pytest.mark.parametrize("size", CARD_SIZES.values())
@pytest.mark.parametrize("theme", CARD_THEMES)
@pytest.mark.parametrize("template", CARD_TEMPLATES)
def test_card_renders_unicode_and_multiline_in_all_formats(qapp, size, theme, template, tmp_path):
    style = CardStyle(width=size[0], height=size[1], theme=theme, template=template, title="夜の歌", credit="Artist · Nghệ sĩ")
    image = render_lyric_card("Under the stars\n星空の下で\nDưới những vì sao\n<keep this text>", style)
    path = tmp_path / "card.png"
    save_card_png(image, path)
    loaded = QImage(str(path))
    assert (loaded.width(), loaded.height()) == size
    assert loaded.pixelColor(0, 0).alpha() == 255
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert image != render_lyric_card("Different lyrics", style)


@pytest.mark.parametrize("template", CARD_TEMPLATES)
def test_long_excerpt_shrinks_but_overflow_and_empty_lyrics_are_rejected(qapp, template):
    style = CardStyle(template=template)
    assert not render_lyric_card("A lyric line\n" * 20, replace(style, font_size=96)).isNull()
    with pytest.raises(ValueError, match="too long"):
        render_lyric_card("A lyric line\n" * 200, style)
    with pytest.raises(ValueError, match="Select some lyrics"):
        render_lyric_card(" \n ", style)


def test_card_styles_change_the_output(qapp):
    style = CardStyle()
    baseline = render_lyric_card("A short lyric", style)
    for changed in (replace(style, centered=True), replace(style, title="Title"),
                    replace(style, credit="Artist"), replace(style, font_size=80),
                    replace(style, template="Spotlight"), replace(style, template="Record sleeve")):
        assert render_lyric_card("A short lyric", changed) != baseline


def open_card(editor):
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    editor.share_card.refresh()
    return editor.share_card


def test_no_selection_keeps_card_empty_and_does_not_copy_or_export(editor, monkeypatch):
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    card = editor.share_card
    sentinel = QImage(4, 4, QImage.Format.Format_RGB32)
    sentinel.fill(QColor("red"))
    QApplication.clipboard().setImage(sentinel)
    previous = QApplication.clipboard().image()
    monkeypatch.setattr(card_module, "render_lyric_card", lambda *_: pytest.fail("Rendered without a selection"))
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: pytest.fail("Exported without a selection"))
    card.refresh()
    card.copy_image()
    card.export_image()
    assert editor._checked_share_line_indices() == set()
    assert card._image.isNull() and card.preview.image.isNull()
    assert not card.copy_button.isEnabled() and not card.save_button.isEnabled()
    assert "Select lyric lines" in card.status.text()
    assert "No lyrics selected" in editor.share_selection_label.text()
    assert QApplication.clipboard().image() == previous


def test_selection_preview_and_clipboard_use_document_order(editor, monkeypatch):
    editor.lines = [LyricLine(1, "First"), LyricLine(2, "Second"), LyricLine(3, "Third")]
    editor._populate_lines()
    card = open_card(editor)
    rendered = []
    original = card_module.render_lyric_card

    def capture(text, style):
        rendered.append(text)
        return original(text, style)

    monkeypatch.setattr(card_module, "render_lyric_card", capture)
    editor.sidebar_sort_combo.setCurrentIndex(2)
    editor.clear_share_line_selection()
    editor.line_list.item(0).setCheckState(Qt.CheckState.Checked)
    editor.line_list.item(2).setCheckState(Qt.CheckState.Checked)
    card.copy_image()
    assert rendered[-1] == "First\nThird"
    assert QApplication.clipboard().image() == card._image
    editor.clear_share_line_selection()
    card.refresh()
    assert rendered[-1] == "First\nThird"
    assert card._image.isNull()
    assert not card.copy_button.isEnabled()
    assert not card.save_button.isEnabled()
    editor.select_all_share_lines()
    card.refresh()
    assert rendered[-1] == "First\nSecond\nThird"


def test_card_export_adds_png_suffix_and_cancel_preserves_file(editor, tmp_path, monkeypatch):
    card = open_card(editor)
    output = tmp_path / "excerpt"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    card.export_image()
    saved = output.with_suffix(".png")
    assert QImage(str(saved)).convertToFormat(card._image.format()) == card._image
    original = saved.read_bytes()
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: ("", ""))
    card.theme_combo.setCurrentText("Paper")
    card.export_image()
    assert saved.read_bytes() == original


def test_failed_export_reports_error_without_success_status(editor, monkeypatch):
    card = open_card(editor)
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: ("unwritable.png", ""))
    def fail(*_):
        raise OSError("Disk full")
    monkeypatch.setattr(card_module, "save_card_png", fail)
    messages = []
    monkeypatch.setattr(card_module.QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    card.export_image()
    assert "Disk full" in messages[0]
    assert "Saved image" not in card.status.text()


def test_overflow_clears_stale_preview_and_disables_export(editor):
    card = open_card(editor)
    assert card.save_button.isEnabled()
    editor.lines = [LyricLine(0, "Too many lines\n" * 200)]
    editor._populate_lines()
    editor.select_all_share_lines()
    card.refresh()
    assert card._image.isNull()
    assert card.preview.image.isNull()
    assert not card.copy_button.isEnabled()
    assert not card.save_button.isEnabled()
    assert "too long" in card.status.text()


def test_card_uses_unsaved_source_without_saving_document(editor, monkeypatch):
    card = open_card(editor)
    editor.source_editor.setPlainText("[00:02.000]Unsaved source lyric")
    editor.reload_model_from_source()
    editor.select_all_share_lines()
    card.copy_image()
    assert card._render_key[0] == "Unsaved source lyric"
    assert editor.document_dirty
    assert not editor.lrc_path.exists()
    # Invalid source must not copy the previously rendered card.
    previous = QApplication.clipboard().image()
    monkeypatch.setattr(card_module.QMessageBox, "warning", lambda *_: None)
    editor.source_editor.setPlainText("invalid timestamp")
    card.copy_image()
    assert QApplication.clipboard().image() == previous
    assert card._image.isNull()


def test_song_metadata_prefills_title_and_artist(make_lyrics_editor, tmp_path):
    path = tmp_path / "metadata.lrc"
    path.write_text("[ti:Song title]\n[ar:Artist name]\n[al:Album name]\n[00:01.000]A lyric\n", encoding="utf-8")
    editor = make_lyrics_editor(path=path, populate=False)
    assert editor.share_card.title_edit.text() == "Song title"
    assert editor.share_card.credit_edit.text() == "Artist name"
    assert editor.share_card.album_edit.text() == "Album name"


def test_preview_refreshes_for_selection_and_style_changes(editor):
    card = open_card(editor)
    baseline = card._image.copy()
    editor.clear_share_line_selection()
    editor.line_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert QSignalSpy(card._refresh_timer.timeout).wait(3000)
    assert card._render_key[0] == "Hello"
    assert card._image != baseline
    selected = card._image.copy()
    card.theme_combo.setCurrentText("Rose")
    assert QSignalSpy(card._refresh_timer.timeout).wait(3000)
    assert card._image != selected
    for template in CARD_TEMPLATES[1:]:
        previous = card._image.copy()
        card.template_combo.setCurrentText(template)
        assert QSignalSpy(card._refresh_timer.timeout).wait(3000)
        assert card._image != previous
        assert card._render_key[1].template == template
    editor.share_tabs.setCurrentIndex(0)
    card.schedule_refresh()
    assert not card._refresh_timer.isActive()


def striped_background(path):
    image = QImage(240, 80, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    for index, color in enumerate(("#ff0000", "#00ff00", "#0000ff")):
        painter.fillRect(index * 80, 0, 80, 80, QColor(color))
    painter.end()
    image.save(str(path))
    return str(path)


def test_image_fill_crops_without_stretching_and_supports_zoom_blur_and_dim(qapp, tmp_path):
    style = CardStyle(background_mode="Image", image_path=striped_background(tmp_path / "stripes.png"), image_dim=0)
    colors = theme_colors(style.theme, style.template)
    left = card_background(replace(style, crop_x=0), colors)
    right = card_background(replace(style, crop_x=100), colors)
    assert left.pixelColor(540, 540).name() == "#ff0000"
    assert right.pixelColor(540, 540).name() == "#0000ff"
    zoomed = card_background(replace(style, image_zoom=200), colors)
    assert zoomed.pixelColor(540, 540).name() == "#00ff00"
    dimmed = card_background(replace(style, image_dim=50), colors)
    assert 125 <= dimmed.pixelColor(540, 540).green() <= 130
    seam = replace(style, crop_x=25)
    assert card_background(seam, colors) != card_background(replace(seam, image_blur=40), colors)


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("size", CARD_SIZES.values())
@pytest.mark.parametrize("position", [0, 100])
def test_custom_spacing_keeps_lyric_ink_inside_card_padding(qapp, template, size, position):
    import numpy as np
    # Magenta identifies lyric ink independently of borders and metadata.
    style = CardStyle(width=size[0], height=size[1], template=template, padding=160,
                      line_spacing=180, vertical_position=position, title="Title", credit="Artist",
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    image = render_lyric_card("A lyric line\nSecond line\nThird line", style)
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(size[1], size[0], 4)
    y, x = np.where((pixels[:, :, 0] == 255) & (pixels[:, :, 1] == 0) & (pixels[:, :, 2] == 255))
    assert len(x) > 0
    assert x.min() >= style.padding
    assert x.max() < style.width - style.padding
    assert y.min() >= style.padding
    assert y.max() < style.height - style.padding


def test_custom_colors_and_gradient_angle_affect_background_and_theme_reset(editor, monkeypatch):
    card = open_card(editor)
    baseline = card._image.copy()
    card.custom_colors.setChecked(True)
    monkeypatch.setattr(card_module.QColorDialog, "getColor", lambda *_: QColor("#2468ac"))
    card.choose_color(0)
    card.background_mode.setCurrentText("Solid")
    card.refresh()
    assert card._image.pixelColor(0, 0).name() == "#2468ac"
    assert card._image != baseline
    card.background_mode.setCurrentText("Gradient")
    card.gradient_angle.setValue(180)
    card.refresh()
    gradient = card._image.copy()
    card.gradient_angle.setValue(0)
    card.refresh()
    assert card._image != gradient
    card.custom_colors.setChecked(False)
    assert card.current_style().colors == ()
    assert not card.color_buttons[0].isEnabled()


def test_background_picker_missing_image_and_clear_recover_without_stale_export(editor, tmp_path, monkeypatch):
    card = open_card(editor)
    image_path = tmp_path / "background.png"
    striped_background(image_path)
    monkeypatch.setattr(card_module.QFileDialog, "getOpenFileName", lambda *_: (str(image_path), ""))
    card.choose_image()
    card.refresh()
    assert card.current_style().background_mode == "Image"
    assert not card._image.isNull()
    image_path.unlink()
    card.refresh()
    assert card._image.isNull()
    assert not card.save_button.isEnabled()
    assert "missing" in card.status.text()
    card.clear_image()
    card.refresh()
    assert card.current_style().background_mode == "Gradient"
    assert card.save_button.isEnabled()
    assert not card.image_path_edit.text()


def test_background_image_updates_are_used_when_copying(editor, tmp_path, monkeypatch):
    card = open_card(editor)
    path = tmp_path / "background.png"
    striped_background(path)
    monkeypatch.setattr(card_module.QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    card.choose_image()
    card.copy_image()
    before = card._image.copy()
    replacement = QImage(240, 80, QImage.Format.Format_RGB32)
    replacement.fill(QColor("#ffffff"))
    replacement.save(str(path))
    card.copy_image()
    assert card._image != before
    assert QApplication.clipboard().image() == card._image


def test_presets_reload_across_editors_preserving_song_and_other_preferences(make_lyrics_editor, monkeypatch, tmp_path):
    from core.settings import load_all_settings, save_all_settings
    save_all_settings({"unrelated_preference": {"keep": True}})
    editor = make_lyrics_editor()
    card = open_card(editor)
    card.template_combo.setCurrentText("Record sleeve")
    card.theme_combo.setCurrentText("Rose")
    card.custom_colors.setChecked(True)
    card._colors[5] = "#abcdef"
    card.size_combo.setCurrentIndex(1)
    card.font_size.setValue(64)
    card.alignment_combo.setCurrentText("Right")
    for button in card.text_style_buttons.values():
        button.setChecked(True)
    card.stroke_enabled.setChecked(True)
    card.shadow_enabled.setChecked(True)
    card._effect_colors.update(stroke_color="#123456", shadow_color="#abcdef")
    card.stroke_width.setValue(5)
    card.shadow_opacity.setValue(75)
    card.shadow_blur.setValue(12)
    card.shadow_offset_x.setValue(-14)
    card.shadow_offset_y.setValue(20)
    card.watermark_enabled.setChecked(True)
    card.watermark_width.setValue(250)
    card.watermark_opacity.setValue(62)
    card.watermark_position.setCurrentText("Top left")
    card.image_path_edit.setText(striped_background(tmp_path / "preset-background.png"))
    card.background_mode.setCurrentText("Image")
    card.gradient_angle.setValue(270)
    card.image_blur.setValue(12)
    card.image_dim.setValue(60)
    card.image_zoom.setValue(145)
    card.crop_x.setValue(65)
    card.crop_y.setValue(35)
    card.padding.setValue(120)
    card.line_spacing.setValue(140)
    card.vertical_position.setValue(25)
    card.title_edit.setText("First song")
    card.credit_edit.setText("First artist")
    style = card.current_style()
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("My style", True))
    card.save_preset()
    stored = load_all_settings()
    assert stored["unrelated_preference"] == {"keep": True}
    assert "title" not in stored["lyrics_card_presets"]["My style"]
    assert "credit" not in stored["lyrics_card_presets"]["My style"]
    next_editor = make_lyrics_editor()
    next_card = open_card(next_editor)
    title = next_card.title_edit.text()
    next_card.preset_combo.setCurrentText("My style")
    assert replace(next_card.current_style(), title=style.title, credit=style.credit) == style
    assert next_card.title_edit.text() == title
    assert next_card.credit_edit.text() == ""
    next_card.delete_preset()
    assert load_all_settings()["lyrics_card_presets"] == {}
    assert load_all_settings()["unrelated_preference"] == {"keep": True}


def test_failed_preset_write_reports_error_without_adding_preset(editor, monkeypatch):
    card = open_card(editor)
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("My style", True))
    def fail(*_):
        raise OSError("Disk full")
    monkeypatch.setattr(card_module, "save_card_preset", fail)
    messages = []
    monkeypatch.setattr(card_module.QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    card.save_preset()
    assert messages == ["Disk full"]
    assert card.preset_combo.findText("My style") == -1


def test_malformed_presets_fall_back_to_safe_defaults(qapp):
    from ui.lyrics_card_presets import style_from_preset
    style = style_from_preset({"width": [], "height": None, "theme": {}, "template": [],
                               "image_blur": 99999, "image_zoom": -1, "colors": ["bad"] * 6,
                               "padding": "invalid", "centered": "false", "title": "Other song"})
    assert style.width == 1080
    assert style.template == "Classic"
    assert style.image_blur == 40
    assert style.image_zoom == 100
    assert style.padding == 84
    assert style.colors == ()
    assert style.title == ""
    assert type(style_from_preset({"width": 1080.0, "height": 1080.0}).width) is int


def test_preset_replacement_requires_the_dialog_choice(editor, monkeypatch):
    from ui.lyrics_card_presets import load_card_presets
    card = open_card(editor)
    monkeypatch.setattr(card_module.QInputDialog, "getText", lambda *args, **kwargs: ("My style", True))
    card.save_preset()
    card.padding.setValue(120)
    monkeypatch.setattr(card_module.QMessageBox, "question", lambda *_: card_module.QMessageBox.StandardButton.No)
    card.save_preset()
    assert load_card_presets()["My style"]["padding"] == 84
    monkeypatch.setattr(card_module.QMessageBox, "question", lambda *_: card_module.QMessageBox.StandardButton.Yes)
    card.save_preset()
    assert load_card_presets()["My style"]["padding"] == 120


@pytest.mark.parametrize("template", CARD_TEMPLATES)
def test_right_alignment_anchors_styled_lyrics_to_the_right_edge(qapp, template):
    import numpy as np
    base = CardStyle(template=template, bold=True, italic=True, underline=True, strikeout=True,
                     colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    bounds = []
    for alignment in ("left", "center", "right"):
        image = render_lyric_card("When we lift off\n夜の歌", replace(base, alignment=alignment))
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(base.height, base.width, 4)
        y, x = np.where((pixels[:, :, 0] == 255) & (pixels[:, :, 1] == 0) & (pixels[:, :, 2] == 255))
        assert len(x)
        assert x.min() >= base.padding
        assert x.max() < base.width - base.padding
        bounds.append((x.min(), x.max()))
    assert bounds[0][0] < bounds[1][0] < bounds[2][0]
    assert abs(bounds[2][1] - (base.width - base.padding)) <= base.font_size / 2


@pytest.mark.parametrize("flag", ["bold", "italic", "underline", "strikeout"])
def test_each_text_style_changes_the_export_and_can_be_toggled_off(editor, flag, tmp_path, monkeypatch):
    card = open_card(editor)
    baseline = card._image.copy()
    card.text_style_buttons[flag].setChecked(True)
    assert QSignalSpy(card._refresh_timer.timeout).wait(3000)
    assert card._image != baseline
    assert getattr(card.current_style(), flag)
    output = tmp_path / "styled.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    card.export_image()
    assert QImage(str(output)).convertToFormat(card._image.format()) == card._image
    card.text_style_buttons[flag].setChecked(False)
    card.refresh()
    assert card._image == baseline


def test_legacy_centered_presets_and_invalid_text_styles_load_safely(editor):
    from ui.lyrics_card_presets import style_from_preset
    card = open_card(editor)
    card._presets["Legacy"] = {"centered": True}
    card.preset_combo.addItem("Legacy", "Legacy")
    card.preset_combo.setCurrentText("Legacy")
    assert card.alignment_combo.currentData() == "center"
    card.refresh()
    assert card._image == render_lyric_card("Hello\nWorld", card.current_style())
    invalid = style_from_preset({"alignment": [], "bold": "false", "italic": 1,
                                 "underline": None, "strikeout": {}})
    assert invalid.alignment == ""
    assert not (invalid.bold or invalid.italic or invalid.underline or invalid.strikeout)


def image_pixels(image):
    import numpy as np
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4).copy()


def test_stroke_outlines_lyric_glyphs_while_preserving_the_fill(qapp):
    import numpy as np
    style = CardStyle(background_mode="Solid", colors=("#000000", "#000000", "#ffffff", "#ffffff", "#ffffff", "#888888"))
    base = image_pixels(render_lyric_card("Stroke 夜の歌", style))
    outlined = image_pixels(render_lyric_card("Stroke 夜の歌", replace(style, stroke_enabled=True, stroke_width=4, stroke_color="#00ff00")))
    white = np.all(outlined[:, :, :3] == (255, 255, 255), axis=2)
    baseline_white = np.all(base[:, :, :3] == (255, 255, 255), axis=2)
    green = np.all(outlined[:, :, :3] == (0, 255, 0), axis=2)
    assert green.sum() > 100
    assert white.sum() >= baseline_white.sum() * .5


def test_shadow_color_opacity_and_zero_opacity_rendering(qapp):
    import numpy as np
    style = CardStyle(background_mode="Solid", shadow_enabled=True, shadow_color="#ff0000",
                      shadow_opacity=100, shadow_blur=0, shadow_offset_x=25, shadow_offset_y=25,
                      colors=("#ffffff", "#ffffff", "#ffffff", "#ffffff", "#ffffff", "#888888"))
    opaque = image_pixels(render_lyric_card("Shadow 夜の歌", style))
    red = np.all(opaque[:, :, :3] == (255, 0, 0), axis=2)
    assert red.sum() > 100
    half = image_pixels(render_lyric_card("Shadow 夜の歌", replace(style, shadow_opacity=50)))
    assert np.all(half[red][:, 0] == 255)
    assert np.all((half[red][:, 1] >= 126) & (half[red][:, 1] <= 128))
    invisible = render_lyric_card("Shadow 夜の歌", replace(style, shadow_opacity=0))
    assert invisible == render_lyric_card("Shadow 夜の歌", replace(style, shadow_enabled=False))


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("position", [0, 100])
@pytest.mark.parametrize("offset", [-40, 40])
@pytest.mark.parametrize("include_cover", [False, True])
def test_combined_effects_keep_all_ink_inside_padding(qapp, template, position, offset, include_cover):
    import numpy as np
    style = CardStyle(template=template, alignment="right", bold=True, italic=True, underline=True,
                      stroke_enabled=True, stroke_width=12, stroke_color="#00ff00",
                      shadow_enabled=True, shadow_color="#0000ff", shadow_opacity=100,
                      shadow_blur=30, shadow_offset_x=offset, shadow_offset_y=offset,
                      padding=160, vertical_position=position, title="Title", credit="Artist",
                      show_album_cover=include_cover, cover_size=240,
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    cover = QImage(64, 64, QImage.Format.Format_RGB32)
    cover.fill(QColor("#ffffff"))
    pixels = image_pixels(render_lyric_card("Italic effect\n夜の歌", style, cover))
    red, green, blue = (pixels[:, :, index] for index in range(3))
    ink = (((red == 255) & (green == 0) & (blue == 255))
           | ((red == 0) & (green > 0) & (blue == 0))
           | ((red == 0) & (green == 0) & (blue > 0)))
    if template == "Record sleeve":
        # Metadata now casts effects too, inside the sidebar's own padding.
        sidebar_width = min(420, round(style.width * .30))
        metadata_y, metadata_x = np.where(ink[:, :sidebar_width])
        assert len(metadata_x)
        assert metadata_x.min() >= 64 and metadata_x.max() < sidebar_width - 64
        assert metadata_y.min() >= style.padding and metadata_y.max() < style.height - style.padding
        ink[:, :sidebar_width] = False
    y, x = np.where(ink)
    assert len(x)
    assert x.min() >= style.padding and x.max() < style.width - style.padding
    assert y.min() >= style.padding and y.max() < style.height - style.padding


@pytest.mark.parametrize("effect", ["stroke", "shadow"])
def test_effect_controls_update_preview_export_and_toggle_off(editor, effect, tmp_path, monkeypatch):
    card = open_card(editor)
    baseline = card._image.copy()
    group = getattr(card, effect + "_enabled")
    assert not card.effect_color_buttons[effect + "_color"].isEnabled()
    group.setChecked(True)
    assert card.effect_color_buttons[effect + "_color"].isEnabled()
    monkeypatch.setattr(card_module.QColorDialog, "getColor", lambda *_: QColor("#ff5577"))
    card.choose_effect_color(effect + "_color")
    assert QSignalSpy(card._refresh_timer.timeout).wait(3000)
    assert card._image != baseline
    assert getattr(card.current_style(), effect + "_color") == "#ff5577"
    output = tmp_path / (effect + ".png")
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    card.export_image()
    assert QImage(str(output)).convertToFormat(card._image.format()) == card._image
    group.setChecked(False)
    card.refresh()
    assert card._image == baseline


def test_effect_presets_validate_colors_flags_and_bounds():
    from ui.lyrics_card_presets import style_from_preset
    style = style_from_preset({"stroke_enabled": "true", "shadow_enabled": [],
                               "stroke_color": "invalid", "shadow_color": {},
                               "stroke_width": 999, "shadow_opacity": -10, "shadow_blur": 999,
                               "shadow_offset_x": -999, "shadow_offset_y": 999})
    assert not style.stroke_enabled and not style.shadow_enabled
    assert style.stroke_color == style.shadow_color == "#000000"
    assert style.stroke_width == 12
    assert style.shadow_opacity == 0 and style.shadow_blur == 30
    assert style.shadow_offset_x == -40 and style.shadow_offset_y == 40


@pytest.mark.parametrize("template,size", [("Classic", (1080, 1080)),
                                           ("Spotlight", (1080, 1350)),
                                           ("Record sleeve", (1920, 1080))])
@pytest.mark.parametrize("corner", ["top_left", "top_right", "bottom_left", "bottom_right"])
def test_watermark_keeps_aspect_transparency_and_opacity_in_each_corner(qapp, tmp_path, monkeypatch, template, size, corner):
    import numpy as np
    import ui.lyrics_card_renderer as renderer
    logo = QImage(200, 50, QImage.Format.Format_RGBA8888)
    logo.fill(Qt.GlobalColor.transparent)
    clear_path = tmp_path / "clear-logo.png"
    logo.save(str(clear_path))
    painter = QPainter(logo)
    painter.fillRect(20, 10, 160, 30, QColor("#00ff00"))
    painter.end()
    logo_path = tmp_path / "logo.png"
    logo.save(str(logo_path))
    style = CardStyle(width=size[0], height=size[1], template=template, title="Title", credit="Artist",
                      watermark_enabled=True, watermark_width=200, watermark_opacity=100, watermark_position=corner)
    monkeypatch.setattr(renderer, "LOGO_PATH", clear_path)
    background = image_pixels(render_lyric_card("Some lyrics", style))
    monkeypatch.setattr(renderer, "LOGO_PATH", logo_path)
    opaque = image_pixels(render_lyric_card("Some lyrics", style))
    y, x = np.where(np.any(opaque != background, axis=2))
    origin_x = 48 if corner.endswith("left") else style.width - 48 - 200
    origin_y = 48 if corner.startswith("top") else style.height - 48 - 50
    assert (x.min(), x.max()) == (origin_x + 20, origin_x + 179)
    assert (y.min(), y.max()) == (origin_y + 10, origin_y + 39)
    # Transparent pixels remain untouched; only the logo's ink is blended.
    assert np.array_equal(opaque[origin_y, origin_x], background[origin_y, origin_x])
    half = image_pixels(render_lyric_card("Some lyrics", replace(style, watermark_opacity=50)))
    sample = (origin_y + 25, origin_x + 100)
    expected = (background[sample][:3].astype(float) + np.array((0, 255, 0))) / 2
    assert np.all(np.abs(half[sample][:3].astype(float) - expected) <= 1)


def test_watermark_controls_update_preview_and_png_and_can_be_disabled(editor, tmp_path, monkeypatch):
    card = open_card(editor)
    baseline = card._image.copy()
    assert not card.watermark_width.isEnabled()
    card.watermark_enabled.setChecked(True)
    assert card.watermark_width.isEnabled()
    card.watermark_width.setValue(240)
    card.watermark_opacity.setValue(70)
    card.watermark_position.setCurrentText("Top left")
    assert QSignalSpy(card._refresh_timer.timeout).wait(3000)
    assert card._image != baseline
    output = tmp_path / "watermarked.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    card.export_image()
    assert QImage(str(output)).convertToFormat(card._image.format()) == card._image
    card.watermark_opacity.setValue(0)
    card.refresh()
    assert card._image == baseline
    card.watermark_enabled.setChecked(False)
    card.refresh()
    assert card._image == baseline


def test_missing_watermark_logo_reports_error_and_disabling_it_restores_export(editor, tmp_path, monkeypatch):
    import ui.lyrics_card_renderer as renderer
    card = open_card(editor)
    monkeypatch.setattr(renderer, "LOGO_PATH", tmp_path / "missing-logo.png")
    card.watermark_enabled.setChecked(True)
    card.refresh()
    assert card._image.isNull()
    assert not card.save_button.isEnabled()
    assert "LRCPlus logo" in card.status.text()
    card.watermark_enabled.setChecked(False)
    card.refresh()
    assert card.save_button.isEnabled()


def test_watermark_preset_defaults_and_invalid_values_are_safe():
    from ui.lyrics_card_presets import style_from_preset
    assert not style_from_preset({}).watermark_enabled
    style = style_from_preset({"watermark_enabled": "true", "watermark_width": 9999,
                               "watermark_opacity": -1, "watermark_position": []})
    assert not style.watermark_enabled
    assert style.watermark_width == 320
    assert style.watermark_opacity == 0
    assert style.watermark_position == "bottom_right"


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("corner", ["top_left", "top_right", "bottom_left", "bottom_right"])
def test_large_watermark_reserves_space_for_song_details_cover_and_lyrics(qapp, tmp_path, monkeypatch, template, corner):
    import numpy as np
    import ui.lyrics_card_renderer as renderer
    logo = QImage(320, 96, QImage.Format.Format_RGB32)
    logo.fill(QColor("#ff8000"))
    path = tmp_path / "logo.png"
    logo.save(str(path))
    monkeypatch.setattr(renderer, "LOGO_PATH", path)
    cover = QImage(100, 100, QImage.Format.Format_RGB32)
    cover.fill(QColor("#00ff00"))
    style = CardStyle(template=template, title="Song title", credit="Artist credit", padding=48,
                      watermark_enabled=True, watermark_width=320, watermark_opacity=100,
                      watermark_position=corner, show_album_cover=True, cover_size=240,
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    pixels = image_pixels(render_lyric_card("First lyric\nSecond lyric", style, cover))
    content = (np.all(pixels[:, :, :3] == (255, 255, 255), axis=2)
               | np.all(pixels[:, :, :3] == (255, 0, 255), axis=2)
               | np.all(pixels[:, :, :3] == (0, 255, 0), axis=2))
    logo_ink = np.all(pixels[:, :, :3] == (255, 128, 0), axis=2)
    content_y, _ = np.where(content)
    logo_y, _ = np.where(logo_ink)
    if corner.startswith("top"):
        assert content_y.min() > logo_y.max()
    else:
        assert content_y.max() < logo_y.min()
