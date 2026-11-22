from pathlib import Path

import numpy as np

from PySide6.QtMultimedia import QAudioFormat

from core.plugin_manager import PluginManager
from plugins.core.equalizer.dsp import (
    Band, EqualizerProcessor, FREQUENCIES, THIRD_OCTAVE_FREQUENCIES, graphic_bands,
)
from ui.settings_dialog import PluginSettingsDialog


def test_equalizer_boosts_selected_band_without_changing_frame_count():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    time = np.arange(12000) / fmt.sampleRate()
    source = (0.1 * np.sin(2 * np.pi * time[:, None] * [1000, 8000])).astype(np.float32)
    gains = tuple(6 if frequency == 1000 else 0 for frequency in FREQUENCIES)
    configuration = (0, graphic_bands(gains))
    equalizer = EqualizerProcessor(lambda: configuration)

    data = b"".join(equalizer.process(block.tobytes(), fmt)
                    for block in np.array_split(source, 17))
    output = np.frombuffer(data, np.float32).reshape(source.shape)
    gain = np.linalg.norm(output[2400:], axis=0) / np.linalg.norm(source[2400:], axis=0)
    np.testing.assert_allclose(gain, [10 ** (6 / 20), 1], rtol=0.02)

    equalizer.reset()
    assert equalizer.process(source.tobytes(), fmt) == data
    configuration = None
    assert equalizer.process(source.tobytes(), fmt) == source.tobytes()


def test_parametric_frequency_width_and_shelf_are_applied():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(3)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    time = np.arange(24000) / fmt.sampleRate()
    source = (0.1 * np.sin(2 * np.pi * time[:, None] * [50, 1800, 2200])).astype(np.float32)
    shelf = Band(200, gain=-6, q=0.7, shape="low_shelf")
    configuration = (0, (shelf, Band(1800, gain=9, q=10)))
    equalizer = EqualizerProcessor(lambda: configuration)

    def measured_gain():
        data = equalizer.process(source.tobytes(), fmt)
        output = np.frombuffer(data, np.float32).reshape(source.shape)
        return np.linalg.norm(output[4800:], axis=0) / np.linalg.norm(source[4800:], axis=0)

    narrow = measured_gain()
    np.testing.assert_allclose(narrow[:2], [10 ** (-6 / 20), 10 ** (9 / 20)], rtol=0.02)
    configuration = (0, (shelf, Band(1800, gain=9, q=1)))
    assert measured_gain()[2] > narrow[2] * 1.5


def test_core_equalizer_settings_enable_and_restore_the_playback_route(qapp, monkeypatch):
    from ui.main_window import Player

    root = Path(__file__).resolve().parents[1] / "plugins"
    manager = PluginManager()
    metadata = next(entry for entry in manager.discover_plugins(root)
                    if entry["plugin_id"] == "equalizer")
    assert metadata["category"] == "Core"
    legacy_gains = [6 if frequency == 1000 else 0 for frequency in FREQUENCIES]
    manager.set_plugin_setting("equalizer", "equalizer", {
        "enabled": False, "preamp": 0, "gains": legacy_gains,
    })
    manager.load_from_file("equalizer", metadata["plugin_dir"] / "plugin.py", metadata)
    window = Player(plugin_manager=manager)
    manager.set_host_window(window)
    manager.emit("app_ready", window=window)
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer", parent=window)
    try:
        assert not window._pcm_worker._controls.enabled
        _, panel = dialog._instances[0]
        assert panel.mode() == "graphic"
        assert panel.count() == 10
        assert panel.graphic.sliders[5].value() == 6
        panel.enabled.setChecked(True)
        dialog._save()
        assert window._equalizer.configuration == (0, (Band(1000, gain=6),))
        assert window._pcm_worker._controls.enabled

        saved_dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer", parent=window)
        _, saved_panel = saved_dialog._instances[0]
        assert saved_panel.enabled.isChecked()
        assert saved_panel.graphic.sliders[5].value() == 6
        saved_panel.modes.setCurrentIndex(1)
        shape, frequency, gain, q = saved_panel.parametric.rows[0]
        shape.setCurrentIndex(shape.findData("high_shelf"))
        frequency.setValue(8000)
        gain.setValue(-3.5)
        q.setValue(0.7)
        saved_dialog._save()
        expected = (0, (Band(8000, gain=-3.5, q=0.7, shape="high_shelf"),))
        assert window._equalizer.configuration == expected
        assert window._pcm_worker._controls.enabled

        reloaded = PluginManager()
        reloaded.load_from_file("equalizer", metadata["plugin_dir"] / "plugin.py", metadata)
        try:
            assert reloaded.get_service("playback_equalizer").configuration == expected
        finally:
            reloaded.unload_all()

        reset_dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer", parent=window)
        _, reset_panel = reset_dialog._instances[0]
        assert reset_panel.mode() == "parametric"
        assert reset_panel.parametric.bands()[0] == expected[1][0]
        reset_panel._reset()
        reset_dialog._save()
        assert not window._pcm_worker._controls.enabled
        assert reset_panel.graphic.gains()[5] == 6
    finally:
        window.close()
        manager.unload_all()


def test_expanded_equalizer_keeps_31_band_curves_separate(qapp):
    root = Path(__file__).resolve().parents[1] / "plugins"
    manager = PluginManager()
    plugin_file = root / "core" / "equalizer" / "plugin.py"
    manager.load_from_file("equalizer", plugin_file)
    try:
        dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer")
        _, panel = dialog._instances[0]
        assert panel.count() == len(panel.graphic.sliders) == 10
        panel.enabled.setChecked(True)
        panel.graphic.sliders[0].setValue(-4)
        panel.counts.setCurrentIndex(panel.counts.findData(31))
        assert len(panel.graphic.sliders) == len(panel.parametric.rows) == 31
        assert panel.parametric.bands()[2].frequency == 31.5
        panel.graphic.sliders[-1].setValue(6)
        panel.modes.setCurrentIndex(panel.modes.findData("parametric"))
        panel.parametric.rows[-1][2].setValue(3.5)
        panel.modes.setCurrentIndex(panel.modes.findData("graphic"))
        dialog._save()
        assert manager.get_service("playback_equalizer").configuration == (
            0, (Band(THIRD_OCTAVE_FREQUENCIES[-1], gain=6, q=4.32),),
        )

        manager.unload("equalizer")
        manager.load_from_file("equalizer", plugin_file)
        reopened = PluginSettingsDialog(manager, "equalizer", "Equalizer")
        _, panel = reopened._instances[0]
        assert panel.count() == 31
        assert panel.graphic.sliders[-1].value() == 6
        assert panel.parametric.bands()[-1].gain == 3.5
        panel.counts.setCurrentIndex(panel.counts.findData(10))
        assert panel.graphic.sliders[0].value() == -4
        reopened._save()
        assert manager.get_service("playback_equalizer").configuration == (0, (Band(31, gain=-4),))
    finally:
        manager.unload_all()
