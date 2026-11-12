from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import ui.lyrics_card_renderer as renderer
import ui.lyrics_share_card as card_module
from ui.lyrics_card_presets import save_card_preset, style_from_preset
from ui.lyrics_card_text import CardLyrics


@pytest.mark.parametrize("template", renderer.CARD_TEMPLATES)
@pytest.mark.parametrize("mode", ("horizontal", "vertical_rl"))
def test_quality_renders_more_pixels_without_changing_text_layout(qapp, monkeypatch, template, mode):
    recorded = []
    paint = renderer._paint_lyrics
    def capture(painter, document, x, y, color, style):
        recorded.append((document.toPlainText(), document.defaultFont().pixelSize(),
                         document.size().width(), document.size().height(), x, y))
        paint(painter, document, x, y, color, style)
    monkeypatch.setattr(renderer, "_paint_lyrics", capture)
    style = renderer.CardStyle(template=template, width=360, height=480, padding=48,
                               font_size=26, writing_mode=mode)
    source = CardLyrics("Night sky\nCarry the light", readings=((0, 5, "night"),))
    standard = renderer.render_lyric_card(source, style)
    layout = recorded[:]
    for scale in (2, 3):
        recorded.clear()
        image = renderer.render_lyric_card(source, replace(style, export_scale=scale))
        assert (image.width(), image.height()) == (360 * scale, 480 * scale)
        assert recorded == layout
        # The scene is painted again, rather than resizing the finished bitmap.
        assert image != standard.scaled(image.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)


@pytest.mark.parametrize("scale", (2, 3))
def test_quality_scales_background_and_text_effects(qapp, tmp_path, scale):
    import numpy as np
    background = QImage(640, 640, QImage.Format.Format_RGB32)
    background.fill(QColor("#123456"))
    path = tmp_path / "background.png"
    assert background.save(str(path))
    style = renderer.CardStyle(width=240, height=300, padding=48, font_size=26,
                               background_mode="Image", image_path=str(path), image_dim=0,
                               stroke_enabled=True, stroke_width=2, stroke_color="#00ff00",
                               shadow_enabled=True, shadow_blur=0, shadow_offset_x=12,
                               shadow_offset_y=12, shadow_opacity=100, shadow_color="#ff0000")
    images = [renderer.render_lyric_card("Light", replace(style, export_scale=value)) for value in (1, scale)]
    for image in images:
        assert image.pixelColor(0, 0) == QColor("#123456")
    def bounds(image, rgb):
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)
        y, x = np.where(np.all(pixels[:, :, :3] == rgb, axis=2))
        assert len(y)
        return (x.min(), y.min(), x.max(), y.max())
    for color in ((0, 255, 0), (255, 0, 0)):
        normal, high = bounds(images[0], color), bounds(images[1], color)
        assert all(abs(a - b / scale) < 3 for a, b in zip(normal, high))


@pytest.mark.parametrize("invalid", (None, True, "2", 0, 4, [], 2.0))
def test_quality_rejects_invalid_values_and_old_presets_use_standard(qapp, invalid):
    assert style_from_preset({"export_scale": invalid}).export_scale == 1
    with pytest.raises(ValueError, match="quality"):
        renderer.render_lyric_card("Light", renderer.CardStyle(export_scale=invalid))


def test_quality_control_preset_export_clipboard_and_dimensions(editor, monkeypatch, tmp_path):
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.quality_combo.setCurrentIndex(card.quality_combo.findData(2))
    card.refresh()
    assert (card._image.width(), card._image.height()) == (2160, 2160)
    assert "2160" in card.preview_details.text()
    save_card_preset("High quality", card.current_style())
    card._reload_presets()
    card.quality_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("High quality")
    assert card.current_style().export_scale == 2
    target = tmp_path / "high.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    card.export_image()
    assert QImage(str(target)).convertToFormat(card._image.format()) == card._image
    card.copy_image()
    assert QApplication.clipboard().image() == card._image
    save_card_preset("Old style", renderer.CardStyle())
    card._reload_presets()
    card.preset_combo.setCurrentText("Old style")
    assert card.current_style().export_scale == 1
