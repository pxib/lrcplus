from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QWidget

from core.playback_audio_tap import PLAYBACK_SAMPLE_FRAMES, PlaybackAudioTap
from core.plugin_manager import PluginManager
from plugins.community.audio_visualizer import plugin as visualizer
from plugins.community.audio_visualizer.plugin import SpectrumWidget
from ui.main_window import Player


def _tone(frequency=1000):
    audio_format = QAudioFormat()
    audio_format.setSampleRate(48000)
    audio_format.setChannelCount(2)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    tone = (12000 * np.sin(2 * np.pi * frequency * np.arange(PLAYBACK_SAMPLE_FRAMES) / 48000))
    return np.column_stack((tone, -tone)).astype(np.int16).tobytes(), audio_format


def _overlay_alpha(widget):
    image = QImage(widget.size(), QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return np.frombuffer(image.constBits(), dtype=np.uint8)[3::4].reshape(
        widget.height(), widget.width(),
    ).copy()


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


def test_artwork_styles_render_distinct_transparent_overlays_and_clear_at_idle(qapp):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    player.appearance_settings = {"theme_mode": "dark"}
    widget = SpectrumWidget(player)
    widget.over_artwork = True
    widget.resize(420, 280)
    widget._levels[:] = np.linspace(0.85, 0.0, widget._levels.size)
    widget._peaks[:] = widget._levels
    frames = []

    for style in ("Aurora", "Particle Sphere", "Radial", "Mirror"):
        widget.visual_style = style
        alpha = _overlay_alpha(widget)
        assert 100 < np.count_nonzero(alpha) < alpha.size / 2, style
        frames.append(alpha.tobytes())

    assert len(set(frames)) == len(frames)
    widget._levels.fill(0)
    widget._peaks.fill(0)
    assert not _overlay_alpha(widget).any()


def test_spectrogram_scrolls_from_right_to_left_and_ages_out(qapp, monkeypatch):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    player.playback_audio_tap.set_collecting(True)
    player.appearance_settings = {"theme_mode": "dark"}
    widget = SpectrumWidget(player)
    widget.resize(420, 280)
    widget.over_artwork = True
    widget.set_visual_style("Spectrogram")
    now = 0.0
    monkeypatch.setattr(visualizer, "monotonic", lambda: now)

    for frequency in (120, 4000):
        data, audio_format = _tone(frequency)
        player.playback_audio_tap.capture(data, audio_format, "native")
        widget._advance()
        now += visualizer._HISTORY_INTERVAL
    low_row = widget._history[:, -2].argmax()
    high_row = widget._history[:, -1].argmax()
    assert low_row > high_row
    frame = _overlay_alpha(widget)
    assert frame.max() > 30
    assert not frame[:, :widget.width() // 2].any()

    for _ in range(visualizer._HISTORY_COLUMNS):
        player.playback_audio_tap.capture(bytes(len(data)), audio_format, "native")
        widget._advance()
        now += visualizer._HISTORY_INTERVAL
    assert not _overlay_alpha(widget).any()


def test_waterfall_blends_fixed_layers_and_fades_as_history_ages(qapp, monkeypatch):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    player.playback_audio_tap.set_collecting(True)
    player.appearance_settings = {"theme_mode": "dark"}
    widget = SpectrumWidget(player)
    widget.resize(420, 280)
    widget.over_artwork = True
    widget.set_visual_style("Waterfall")
    now = 0.0
    monkeypatch.setattr(visualizer, "monotonic", lambda: now)
    data, audio_format = _tone()
    silence = bytes(len(data))

    player.playback_audio_tap.capture(data, audio_format, "native")
    widget._advance()
    player.playback_audio_tap.capture(silence, audio_format, "native")
    now += visualizer._HISTORY_INTERVAL / 4
    widget._advance()
    quarter = _overlay_alpha(widget)[:, 50]
    now += visualizer._HISTORY_INTERVAL / 4
    widget._advance()
    half = _overlay_alpha(widget)[:, 50]
    assert 0 < quarter.max() < half.max()
    assert quarter.argmax() == half.argmax()

    now += visualizer._HISTORY_INTERVAL / 2
    widget._advance()
    front = _overlay_alpha(widget)

    for _ in range(12):
        now += visualizer._HISTORY_INTERVAL
        player.playback_audio_tap.capture(silence, audio_format, "native")
        widget._advance()
    distant = _overlay_alpha(widget)
    front_rows, front_cols = np.where(front > 30)
    distant_rows, distant_cols = np.where(distant > 30)
    assert distant_rows.max() < front_rows.max()
    assert np.ptp(distant_cols) < np.ptp(front_cols)
    assert distant.max() < front.max()

    for _ in range(visualizer._WATERFALL_ROWS - 12):
        now += visualizer._HISTORY_INTERVAL
        player.playback_audio_tap.capture(silence, audio_format, "native")
        widget._advance()
    assert not _overlay_alpha(widget).any()


def test_stereo_oscilloscope_shows_centered_and_wide_signals(qapp):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    player.playback_audio_tap.set_collecting(True)
    player.appearance_settings = {"theme_mode": "dark"}
    widget = SpectrumWidget(player)
    widget.resize(420, 280)
    widget.over_artwork = True
    widget.set_visual_style("Stereo Oscilloscope")
    wide, audio_format = _tone()
    samples = np.frombuffer(wide, dtype=np.int16).reshape(-1, 2).copy()
    samples[:, 1] = samples[:, 0]

    player.playback_audio_tap.capture(samples.tobytes(), audio_format, "native")
    widget._advance()
    centered = _overlay_alpha(widget) > 120
    assert np.ptp(np.where(centered)[0]) > 5 * np.ptp(np.where(centered)[1])

    player.playback_audio_tap.capture(wide, audio_format, "native")
    widget._advance()
    horizontal = _overlay_alpha(widget) > 120
    assert np.ptp(np.where(horizontal)[1]) > 5 * np.ptp(np.where(horizontal)[0])

    widget.set_running(True)
    widget.set_running(False)
    assert not _overlay_alpha(widget).any()


def test_large_artwork_effects_use_bounded_surfaces_and_clear_on_style_change(qapp, monkeypatch):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    player.playback_audio_tap.set_collecting(True)
    player.appearance_settings = {"theme_mode": "dark"}
    widget = SpectrumWidget(player)
    widget.resize(2560, 1440)
    widget.over_artwork = True
    data, audio_format = _tone()
    now = 0.0
    monkeypatch.setattr(visualizer, "monotonic", lambda: now)

    for style, limit in (
        ("Waterfall", visualizer._WATERFALL_RASTER_WIDTH),
        ("Stereo Oscilloscope", visualizer._SCOPE_RASTER_SIZE),
        ("Waveform Ribbon", visualizer._RIBBON_RASTER_WIDTH),
        ("Aurora", visualizer._AURORA_RASTER_WIDTH),
    ):
        widget.set_visual_style(style)
        assert not _overlay_alpha(widget).any()
        player.playback_audio_tap.capture(data, audio_format, "native")
        widget._advance()
        now += visualizer._HISTORY_INTERVAL
        widget._advance()

        alpha = _overlay_alpha(widget)
        assert alpha.max() > 40
        bounds = widget._effect_rect()
        assert not alpha[:bounds.top()].any()
        assert not alpha[bounds.bottom() + 1:].any()
        assert max(widget._effect_image.width(), widget._effect_image.height()) == limit


def test_waveform_ribbon_draws_pcm_near_the_bottom_and_fades_on_silence(qapp):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    player.playback_audio_tap.set_collecting(True)
    player.appearance_settings = {"theme_mode": "dark"}
    widget = SpectrumWidget(player)
    widget.resize(420, 280)
    widget.over_artwork = True
    widget.set_visual_style("Waveform Ribbon")

    # A fully out-of-phase test tone still produces a visible waveform.
    data, audio_format = _tone(230)
    player.playback_audio_tap.capture(data, audio_format, "native")
    widget._advance()
    frame = _overlay_alpha(widget)
    rows, cols = np.where(frame > 60)
    assert rows.min() >= widget.height() * 0.72
    assert np.ptp(cols) > widget.width() * 0.65
    amplitude = np.ptp(rows)

    quiet = (np.frombuffer(data, dtype=np.int16) // 8).tobytes()
    player.playback_audio_tap.capture(quiet, audio_format, "native")
    widget._advance()
    assert 0 < np.ptp(np.where(_overlay_alpha(widget) > 60)[0]) < amplitude

    for _ in range(5):
        player.playback_audio_tap.capture(bytes(len(data)), audio_format, "native")
        widget._advance()
    assert not _overlay_alpha(widget).any()


def test_pause_flattens_and_holds_all_visualizers_before_fading(qapp, monkeypatch):
    player = QWidget()
    player.playback_audio_tap = PlaybackAudioTap()
    player.playback_audio_tap.set_collecting(True)
    player.appearance_settings = {"theme_mode": "dark"}
    widget = SpectrumWidget(player)
    data, audio_format = _tone()
    now = 100.0
    monkeypatch.setattr(visualizer, "monotonic", lambda: now)

    for style in (*visualizer._VISUAL_STYLES, "Seekbar"):
        widget.over_artwork = style != "Seekbar"
        widget.resize(420, 280 if widget.over_artwork else 34)
        widget.set_visual_style(style)
        widget.set_running(True)
        player.playback_audio_tap.capture(data, audio_format, "native")
        widget._advance()
        now += visualizer._HISTORY_INTERVAL * 1.25
        widget._advance()
        assert _overlay_alpha(widget).any(), style

        widget.pause()
        paused_at = now
        now = paused_at + 0.08
        widget._advance()
        assert _overlay_alpha(widget).any(), style
        now = paused_at + 0.25
        widget._advance()
        flat = _overlay_alpha(widget)
        assert np.ptp(np.where(flat > 0)[0]) <= 2, style
        assert flat.max() > 100, style
        assert widget._timer.interval() > 4000

        now = paused_at + 4.99
        widget.pause()
        widget._advance()
        np.testing.assert_array_equal(_overlay_alpha(widget), flat, err_msg=style)
        now = paused_at + 5.18
        widget._advance()
        assert 0 < _overlay_alpha(widget).max() < flat.max(), style
        now = paused_at + 5.36
        widget._advance()
        assert not _overlay_alpha(widget).any(), style
        assert not widget._timer.isActive(), style


def test_visualizer_menu_placements_and_native_audio(qapp, monkeypatch):
    plugin_file = Path(__file__).resolve().parents[1] / "plugins/community/audio_visualizer/plugin.py"
    manager = PluginManager()
    manager.load_from_file("audio_visualizer", plugin_file)
    window = Player(plugin_manager=manager)
    manager.set_host_window(window)
    manager.emit("app_ready", window=window)
    try:
        playing = True
        monkeypatch.setattr(window.media_player, "isPlaying", lambda: playing)
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

        playing = False
        manager.emit("playback_state_changed", state="PausedState")
        assert widget._pause_started is not None
        assert not window.playback_audio_tap.collecting
        widget._advance_pause(widget._pause_started + 0.25)
        assert widget._flat_opacity == 1.0
        playing = True
        manager.emit("playback_state_changed", state="PlayingState")
        assert window.playback_audio_tap.collecting
        assert widget._pause_started is None and widget._flat_opacity == 0
        assert widget._timer.interval() == 16
        window.playback_audio_tap.capture(data, audio_format, "native")
        widget._advance()
        assert widget._levels[strongest] > 0.6

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
        settings.style.setCurrentIndex(settings.style.findData("Waveform Ribbon"))
        entry["apply"](settings)
        assert widget.visual_style == "Waveform Ribbon"
        assert manager.get_plugin_setting("audio_visualizer", "style") == "Waveform Ribbon"
        settings.style.setCurrentIndex(settings.style.findData("Waterfall"))
        entry["apply"](settings)
        assert widget.visual_style == "Waterfall"
        assert manager.get_plugin_setting("audio_visualizer", "style") == "Waterfall"
        settings.placement.setCurrentIndex(settings.placement.findData("seekbar"))
        assert not settings.style.isEnabled()
        entry["apply"](settings)
        window.workspace_stack.setCurrentWidget(window.library_page)
        qapp.processEvents()
        assert widget.isVisible()
        seek_bottom = window.seek_track.mapTo(window, QPoint(0, window.seek_track.height())).y()
        assert widget.mapTo(window, QPoint()).y() >= seek_bottom
        assert widget.mapTo(window, QPoint()).x() == window.seek_track.mapTo(window, QPoint()).x()
        assert widget.width() == window.seek_track.width()
        assert manager.get_plugin_setting("audio_visualizer", "placement") == "seekbar"

        settings.placement.setCurrentIndex(settings.placement.findData("artwork"))
        assert settings.style.isEnabled()
        entry["apply"](settings)
        window.workspace_stack.setCurrentWidget(window.lyrics_page)
        qapp.processEvents()
        assert widget.isVisible()
        assert widget.geometry() == window.lyrics_background.rect()
        assert window.lyric_display_stack.parentWidget() is window.lyrics_background
        assert widget.visual_style == "Waterfall"

        window.playback_audio_tap.capture(data, audio_format, "native")
        widget._advance()
        playing = False
        manager.emit("playback_state_changed", state="PausedState")
        assert widget._pause_started is not None
        action.trigger()
        assert not widget.isVisible()
        assert not widget._timer.isActive()
        assert widget._pause_started is None
        assert not widget._peaks.any()
        assert not window.playback_audio_tap.collecting
        assert window.media_player.audioBufferOutput() is None
    finally:
        manager.unload_all()
        window.close()
        window.deleteLater()
