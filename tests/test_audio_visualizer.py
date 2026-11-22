from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QWidget

from core.playback_audio_tap import PLAYBACK_SAMPLE_FRAMES, PlaybackAudioTap
from core.plugin_manager import PluginManager
from plugins.community.audio_visualizer.plugin import SpectrumWidget
from ui.main_window import Player


def _tone(frequency=1000):
    audio_format = QAudioFormat()
    audio_format.setSampleRate(48000)
    audio_format.setChannelCount(2)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    tone = (12000 * np.sin(2 * np.pi * frequency * np.arange(PLAYBACK_SAMPLE_FRAMES) / 48000))
    return np.column_stack((tone, -tone)).astype(np.int16).tobytes(), audio_format


def test_audio_tap_keeps_only_recent_frames_from_the_current_route():
    data, audio_format = _tone()
    tap = PlaybackAudioTap()
    tap.capture(data, audio_format, "native")
    assert tap.take_latest() is None

    tap.set_collecting(True)
    tap.capture(data, audio_format, "native")
    tap.capture(data[:400], audio_format, "native")
    assert tap.take_latest() == (data[400:] + data[:400], 48000, 2, audio_format.sampleFormat())
    assert tap.take_latest() is None

    tap.capture(data[:400], audio_format, "processed")
    assert tap.take_latest()[0] == data[:400]
    tap.clear()
    assert tap.take_latest() is None


def test_visualizer_keeps_the_spectrum_visible_at_low_gain(qapp):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    frames = np.arange(PLAYBACK_SAMPLE_FRAMES) / 48000
    samples = (0.52 * np.sin(2 * np.pi * 120 * frames)
               + 0.12 * np.sin(2 * np.pi * 1400 * frames)).astype(np.float32)
    audio_format = QAudioFormat.SampleFormat.Float
    full_gain = SpectrumWidget(player)._spectrum(samples.tobytes(), 48000, 1, audio_format)
    quiet_widget = SpectrumWidget(player)
    quiet = quiet_widget._spectrum((samples * 0.01).tobytes(), 48000, 1, audio_format)
    np.testing.assert_allclose(quiet, full_gain, atol=0.01)
    assert quiet.max() > 0.6
    assert not quiet_widget._spectrum(bytes(samples.nbytes), 48000, 1, audio_format).any()


def test_visualizer_draws_active_outer_bands_to_the_edges(qapp):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    widget = SpectrumWidget(player)
    widget._levels.fill(0.65)
    widget._peaks.fill(0.7)
    colors = [QColor("#68bcef")] * widget._levels.size

    for artwork, height in ((True, 160), (False, 34)):
        widget.resize(420, height)
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if artwork:
                widget._paint_artwork(painter, colors)
            else:
                widget._paint_seekbar(painter, colors, False)

        for x in (0, image.width() - 1):
            assert any(image.pixelColor(x, y).alpha() > 60 for y in range(height))


def test_visualizer_menu_placements_and_native_audio(qapp, monkeypatch):
    plugin_file = Path(__file__).resolve().parents[1] / "plugins/community/audio_visualizer/plugin.py"
    manager = PluginManager()
    manager.load_from_file("audio_visualizer", plugin_file)
    window = Player(plugin_manager=manager)
    manager.set_host_window(window)
    manager.emit("app_ready", window=window)
    try:
        monkeypatch.setattr(window.media_player, "isPlaying", lambda: True)
        window.show()
        action = next(action for action in window.plugins_menu.actions() if action.text() == "Audio Visualizer")
        assert not action.isChecked()
        assert window.media_player.audioBufferOutput() is None
        action.trigger()

        plugin = manager.plugins["audio_visualizer"].instance
        widget = plugin.widget
        assert not widget.isVisible()
        assert not widget._timer.isActive()
        window.workspace_stack.setCurrentWidget(window.lyrics_page)
        qapp.processEvents()
        assert widget.isVisible()
        assert widget.parentWidget() is window.lyrics_background
        assert window.lyrics_background.children().index(widget) < window.lyrics_background.children().index(
            window.lyric_display_stack)
        assert widget._timer.isActive()
        assert window.media_player.audioBufferOutput() is not None

        data, audio_format = _tone()
        window.media_player.audioBufferOutput().audioBufferReceived.emit(QAudioBuffer(data, audio_format))
        qapp.processEvents()
        widget._advance()
        strongest = widget._levels.argmax()
        assert widget._levels[strongest] > 0.6
        assert widget._frequencies[strongest] <= 1000 <= widget._frequencies[strongest + 1]
        silence = bytes(len(data))
        window.playback_audio_tap.capture(silence, audio_format, "native")
        widget._advance()
        assert widget._peaks[strongest] > widget._levels[strongest]
        for _ in range(4):
            window.playback_audio_tap.capture(silence, audio_format, "native")
            widget._advance()
        assert widget._levels[strongest] < 0.05

        entry = manager.get_settings_widgets()[0]
        settings = entry["widget_factory"](window)
        settings.placement.setCurrentIndex(settings.placement.findData("seekbar"))
        entry["apply"](settings)
        window.workspace_stack.setCurrentWidget(window.library_page)
        qapp.processEvents()
        assert widget.isVisible()
        seek_bottom = window.seek_track.mapTo(window, QPoint(0, window.seek_track.height())).y()
        assert widget.mapTo(window, QPoint()).y() >= seek_bottom
        assert widget.mapTo(window, QPoint()).x() == window.seek_track.mapTo(window, QPoint()).x()
        assert widget.width() == window.seek_track.width()
        assert manager.get_plugin_setting("audio_visualizer", "placement") == "seekbar"

        action.trigger()
        assert not widget.isVisible()
        assert not widget._peaks.any()
        assert not window.playback_audio_tap.collecting
        assert window.media_player.audioBufferOutput() is None
    finally:
        manager.unload_all()
        window.close()
        window.deleteLater()
