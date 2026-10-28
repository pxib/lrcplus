import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

from core.settings import get_appearance_settings, save_appearance_settings
from ui.artwork_background import ArtworkBackgroundWidget
from ui.settings_dialog import SettingsDialog


@pytest.mark.parametrize("mode, samples", [
    ("center", [(0, 0, "black"), (20, 30, "blue"), (30, 40, "red"), (60, 40, "black")]),
    ("fill", [(0, 0, "red"), (79, 79, "red")]),
    ("fit", [(0, 0, "black"), (1, 40, "blue"), (40, 40, "red"), (40, 70, "black")]),
    ("stretch", [(1, 1, "blue"), (40, 1, "red"), (79, 79, "blue")]),
    ("tile", [(1, 1, "blue"), (10, 10, "red"), (41, 21, "blue"), (50, 30, "red")]),
])
def test_background_layout_pixels(qapp, mode, samples):
    widget = ArtworkBackgroundWidget()
    try:
        widget.resize(80, 80)
        widget.set_blur_radius(0)
        widget.set_darkness(0)
        artwork = QPixmap(40, 20)
        artwork.fill(Qt.GlobalColor.red)
        painter = QPainter(artwork)
        painter.fillRect(0, 0, 5, 20, Qt.GlobalColor.blue)
        painter.fillRect(35, 0, 5, 20, Qt.GlobalColor.blue)
        painter.end()
        widget.set_artwork_pixmap(artwork)
        widget.set_background_layout(mode)
        image = widget._cached_background.toImage()
        for x, y, color in samples:
            assert image.pixelColor(x, y) == QColor(color), (mode, x, y)
        # Layouts still receive the shared darkness overlay.
        widget.set_darkness(128)
        assert widget._cached_background.toImage().pixelColor(40, 40).red() < 150
    finally:
        widget.deleteLater()


def test_native_layouts_reload_original_image_dimensions(qapp):
    artwork = QPixmap(800, 400)
    artwork.fill(Qt.GlobalColor.red)
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert artwork.save(buffer, "PNG")
    buffer.close()
    widget = ArtworkBackgroundWidget()
    try:
        widget.resize(80, 80)
        widget.set_blur_radius(0)
        assert widget.set_artwork_bytes(bytes(encoded))
        assert widget._source_pixmap.width() < 800
        for mode in ("center", "fit", "tile"):
            widget.set_background_layout(mode)
            if mode in ("center", "tile"):
                assert widget._source_pixmap.size() == artwork.size()
        widget.clear_artwork()
        widget.set_background_layout("center")
        assert widget._source_pixmap.isNull()
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("mode", ["center", "fill", "fit", "stretch", "tile"])
@pytest.mark.parametrize("blur_mode, bleed", [
    ("quality", False), ("quality", True), ("low_quality", False),
])
def test_layouts_keep_artwork_visible_with_blur_and_resize(qapp, mode, blur_mode, bleed):
    widget = ArtworkBackgroundWidget()
    try:
        widget.resize(80, 80)
        widget.set_blur_radius(4)
        widget.set_darkness(0)
        widget.set_blur_mode(blur_mode)
        widget.set_blur_bleed(bleed)
        widget.set_background_layout(mode)
        artwork = QPixmap(40, 20)
        artwork.fill(Qt.GlobalColor.red)
        widget.set_artwork_pixmap(artwork)
        widget.show()
        qapp.processEvents()
        widget.resize(100, 60)
        qapp.processEvents()
        image = widget.grab().toImage()
        assert image.size() == widget.size()
        assert widget._cached_background.size() == widget.size()
        assert image.pixelColor(50, 30).red() > 200
        if mode in ("center", "fit"):
            if bleed:
                assert image.pixelColor(0, 0).red() < 5
            else:
                assert image.pixelColor(0, 0) == QColor("black")
        elif not bleed:
            assert image.pixelColor(0, 0) == QColor("red")
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("mode", ["center", "fill", "fit", "stretch", "tile"])
def test_background_layout_setting_persists(mode):
    save_appearance_settings({"background_layout": mode})
    assert get_appearance_settings()["background_layout"] == mode


@pytest.mark.parametrize("mode", [None, "unknown", [], {}])
def test_invalid_background_layout_uses_fit(mode):
    assert get_appearance_settings({"appearance": {"background_layout": mode}})["background_layout"] == "fit"


def test_background_layout_control_loads_saves_and_follows_enabled(qapp):
    dialog = SettingsDialog([], {}, appearance_settings={
        "background_layout": "tile", "background_enabled": False,
    })
    try:
        combo = dialog.background_layout_combo
        assert [combo.itemText(i) for i in range(combo.count())] == [
            "Center", "Fill", "Fit", "Stretch", "Tile",
        ]
        assert combo.currentData() == "tile"
        assert not combo.isEnabled()
        dialog.background_enabled_checkbox.setChecked(True)
        assert combo.isEnabled()
        combo.setCurrentIndex(combo.findData("fill"))
        assert dialog.get_appearance_settings()["background_layout"] == "fill"
    finally:
        dialog.deleteLater()
