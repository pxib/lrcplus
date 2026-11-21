from pathlib import Path

import numpy as np

from PySide6.QtMultimedia import QAudioFormat

from core.plugin_manager import PluginManager
from plugins.core.equalizer.dsp import FREQUENCIES, EqualizerProcessor
from ui.settings_dialog import PluginSettingsDialog


def test_equalizer_boosts_selected_band_without_changing_frame_count():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    time = np.arange(12000) / fmt.sampleRate()
    source = (0.1 * np.sin(2 * np.pi * time[:, None] * [1000, 8000])).astype(np.float32)
    gains = tuple(6 if frequency == 1000 else 0 for frequency in FREQUENCIES)
    configuration = (0, gains)
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


def test_core_equalizer_settings_enable_and_restore_the_playback_route(qapp, monkeypatch):
    from ui.main_window import Player

    root = Path(__file__).resolve().parents[1] / "plugins"
    manager = PluginManager()
    metadata = next(entry for entry in manager.discover_plugins(root)
                    if entry["plugin_id"] == "equalizer")
    assert metadata["category"] == "Core"
    manager.load_from_file("equalizer", metadata["plugin_dir"] / "plugin.py", metadata)
    window = Player(plugin_manager=manager)
    manager.set_host_window(window)
    manager.emit("app_ready", window=window)
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer", parent=window)
    try:
        assert not window._pcm_worker._controls.enabled
        _, panel = dialog._instances[0]
        panel.enabled.setChecked(True)
        panel.bands[5].setValue(6)
        dialog._save()
        assert window._pcm_worker._controls.enabled

        saved_dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer", parent=window)
        _, saved_panel = saved_dialog._instances[0]
        assert saved_panel.enabled.isChecked()
        assert saved_panel.bands[5].value() == 6
        saved_panel.enabled.setChecked(False)
        saved_dialog._save()
        assert not window._pcm_worker._controls.enabled
    finally:
        window.close()
        manager.unload_all()
