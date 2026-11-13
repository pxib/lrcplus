"""Hidden playback visuals should not allocate processed images or decoders."""

import time

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QThread, Signal, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QTest

from ui.artwork_background import ArtworkBackgroundWidget, Image
from lyrics import waveform


def test_large_background_retains_only_downscaled_encoded_artwork(qapp):
    image = QImage(2796, 1400, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    widget = ArtworkBackgroundWidget(defer_hidden_rendering=True)
    try:
        assert widget.set_artwork_bytes(bytes(encoded))
        retained = QImage.fromData(widget._artwork_bytes)
        assert retained.width() == 1024
        assert retained.height() == 512
        assert retained.pixelColor(500, 200).red() > 250
        assert widget._source_pixmap.isNull()
        # Original-resolution layouts also use the bounded playback copy.
        widget.set_background_layout("center")
        widget.show()
        qapp.processEvents()
        assert widget._source_pixmap.size() == retained.size()
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


def test_downscaled_background_preserves_transparency(qapp):
    from ui.artwork_background import compact_background_artwork

    image = QImage(2048, 1024, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    retained = QImage.fromData(compact_background_artwork(bytes(encoded)))
    assert retained.width() == 1024
    assert retained.height() == 512
    assert retained.pixelColor(100, 100).alpha() == 0


def test_runtime_snapshot_does_not_initialize_hidden_renderers(qapp):
    import json
    from tools.profile_memory import runtime_state
    from ui.main_window import Player

    window = Player()
    try:
        assert not window._lyrics_renderers_ready
        state = runtime_state(window)
        assert not state["renderers_ready"]
        assert state["artwork_cache_size"] == [0, 0]
        assert not state["waveform_decoder_active"]
        assert state["loaded_plugins"] == []
        assert not state["karaoke_timer_active"]
        assert state["lyric_lines"] == 0
        assert state["translated_lyric_lines"] == 0
        assert not window._lyrics_renderers_ready
        json.dumps(state)
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def artwork(color):
    pixmap = QPixmap(40, 40)
    pixmap.fill(color)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert pixmap.save(buffer, "PNG")
    return bytes(data)


def test_hidden_background_defers_blur_and_restores_latest_artwork(qapp, monkeypatch):
    calls = []
    original = Image.Image.filter

    def filtered(self, effect):
        calls.append(True)
        return original(self, effect)

    monkeypatch.setattr(Image.Image, "filter", filtered)
    widget = ArtworkBackgroundWidget(defer_hidden_rendering=True)
    try:
        widget.resize(80, 80)
        widget.set_darkness(0)
        assert widget.set_artwork_bytes(artwork(Qt.GlobalColor.red))
        assert not calls
        assert widget._source_pixmap.isNull()
        assert widget._cached_background.isNull()
        widget.show()
        qapp.processEvents()
        assert calls
        assert widget.grab().toImage().pixelColor(40, 40) == Qt.GlobalColor.red
        widget.hide()
        assert widget._cached_background.isNull()
        assert widget._source_pixmap.isNull()
        count = len(calls)
        assert widget.set_artwork_bytes(artwork(Qt.GlobalColor.blue))
        widget.set_blur_radius(6)
        assert len(calls) == count
        widget.show()
        qapp.processEvents()
        assert widget.grab().toImage().pixelColor(40, 40) == Qt.GlobalColor.blue
        count = len(calls)
        widget._rebuild_background()
        assert len(calls) == count  # Identical settings reuse the processed image.
        widget.set_background_enabled(False)
        assert widget._cached_background.isNull()
        assert widget._source_pixmap.isNull()
        assert len(calls) == count
        widget.set_background_enabled(True)
        assert widget.grab().toImage().pixelColor(40, 40) == Qt.GlobalColor.blue
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


def test_hidden_background_does_not_decode_until_shown(qapp, monkeypatch):
    widget = ArtworkBackgroundWidget(defer_hidden_rendering=True)
    decoded = []
    original = widget._decode_artwork_bytes

    def decode(data):
        decoded.append(data)
        return original(data)

    monkeypatch.setattr(widget, "_decode_artwork_bytes", decode)
    try:
        first, latest = artwork(Qt.GlobalColor.red), artwork(Qt.GlobalColor.blue)
        assert widget.set_artwork_bytes(first)
        widget.set_background_layout("center")
        assert widget.set_artwork_bytes(latest)
        assert decoded == []
        widget.show()
        qapp.processEvents()
        assert decoded == [latest]
        widget.hide()
        assert widget._source_pixmap.isNull()
        assert not widget.set_artwork_bytes(b"invalid image")
        widget.show()
        qapp.processEvents()
        assert widget._source_pixmap.isNull()
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


def test_hidden_waveform_waits_for_show_and_preserves_playback_position(qapp, monkeypatch):
    decoders = []

    class Decoder(QThread):
        loaded = Signal(list, int)
        failed = Signal(str)

        def __init__(self, path, parent=None):
            super().__init__(parent)
            self.path = path
            decoders.append(self)

        def run(self):
            self.loaded.emit([0.25], 90000)

        def cancel(self):
            self.requestInterruption()

    monkeypatch.setattr(waveform, "_WaveformWorker", Decoder)
    monkeypatch.setattr(waveform, "find_ffmpeg", lambda: "ffmpeg")
    widget = waveform.WaveformWidget(defer_hidden_load=True)
    try:
        widget.load_audio("first.mp3")
        widget.set_duration(90000)
        widget.set_position(30000)
        assert decoders == []
        widget.show()
        deadline = time.monotonic() + 2
        while widget._peaks != [0.25] and time.monotonic() < deadline:
            QTest.qWait(10)
        assert len(decoders) == 1 and decoders[0].path == "first.mp3"
        assert widget._position_ms == 30000
        assert widget._peaks == [0.25]
        widget.hide()
        widget.load_audio("second.mp3")
        widget.set_position(40000)
        assert len(decoders) == 1
        widget.show()
        deadline = time.monotonic() + 2
        while widget._peaks != [0.25] and time.monotonic() < deadline:
            QTest.qWait(10)
        assert len(decoders) == 2 and decoders[1].path == "second.mp3"
        assert widget._position_ms == 40000
    finally:
        widget._stop_worker()
        widget.close()
        widget.deleteLater()
        qapp.processEvents()
