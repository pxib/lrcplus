from pathlib import Path

import numpy as np

from PySide6.QtMultimedia import QAudioFormat
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QInputDialog, QMessageBox

from core.plugin_manager import PluginManager
from core.theme import build_theme_palette
from plugins.core.equalizer.dsp import (
    Band, EqualizerProcessor, FREQUENCIES, THIRD_OCTAVE_FREQUENCIES, graphic_bands,
)
from plugins.core.equalizer.response import FREQUENCIES as RESPONSE_FREQUENCIES
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
    appearance = {"theme_mode": "dark", "theme_color": "#ce88ee", "theme_surface": "classic",
                  "theme_overrides": {"dark": {"window": "#192035", "surface": "#232941"}}}
    monkeypatch.setattr(window, "appearance_settings", appearance)
    colors = build_theme_palette("dark", "#ce88ee", "classic", appearance["theme_overrides"])
    opened = []

    def enable_from_menu(dialog):
        opened.append(dialog)
        assert dialog.parentWidget() is window
        _, panel = dialog._instances[0]
        assert f"background: {colors['window']};" in dialog.styleSheet()
        assert panel.response._colors["surface"].name() == colors["surface"]
        assert panel.response._colors["accent"].name() == colors["accent"]
        assert panel.mode() == "graphic"
        assert panel.count() == 10
        assert panel.graphic.sliders[5].value() == 6
        panel.enabled.setChecked(True)
        dialog._save()

    monkeypatch.setattr(PluginSettingsDialog, "exec", enable_from_menu)
    try:
        assert not window._pcm_worker._controls.enabled
        assert not window.plugins_button.isHidden()
        action = next(item for item in window.plugins_menu.actions() if item.text() == "Equalizer…")
        action.trigger()
        assert len(opened) == 1
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
        manager.unload("equalizer")
        assert action not in window.plugins_menu.actions()
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
        preview_updates = QSignalSpy(panel._response_timer.timeout)
        panel.graphic.sliders[-1].setValue(6)
        panel.preamp.setValue(-3)
        assert preview_updates.wait(1000)
        np.testing.assert_allclose(panel.response._response[-1], 3, atol=0.1)
        panel.modes.setCurrentIndex(panel.modes.findData("parametric"))
        np.testing.assert_allclose(panel.response._response, -3)
        panel._copy_graphic.click()
        assert panel.parametric.bands()[-1].gain == 6
        panel.parametric.rows[-1][2].setValue(3.5)
        assert preview_updates.wait(1000)
        np.testing.assert_allclose(panel.response._response[-1], 0.5, atol=0.1)
        panel.modes.setCurrentIndex(panel.modes.findData("graphic"))
        np.testing.assert_allclose(panel.response._response[-1], 3, atol=0.1)
        panel.preamp.setValue(0)
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
        np.testing.assert_allclose(np.interp(31, RESPONSE_FREQUENCIES, panel.response._response), -4, atol=0.1)
        reopened._save()
        assert manager.get_service("playback_equalizer").configuration == (0, (Band(31, gain=-4),))
    finally:
        manager.unload_all()


def test_presets_load_into_selected_eq_and_save_as_editable_values(qapp):
    root = Path(__file__).resolve().parents[1] / "plugins"
    manager = PluginManager()
    manager.load_from_file("equalizer", root / "core" / "equalizer" / "plugin.py")
    try:
        dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer")
        _, panel = dialog._instances[0]
        actions = {item.text(): item for item in panel.presets.menu().actions() if not item.isSeparator()}
        panel.enabled.setChecked(True)
        actions["Bass Boost"].trigger()
        bass_boost = panel.graphic.gains()
        assert bass_boost[:3] == (5, 5, 4)
        assert panel.preamp.value() < 0
        assert panel.response._response.max() <= 0.01
        service = manager.get_service("playback_equalizer")
        assert service.configuration is None

        panel.counts.setCurrentIndex(panel.counts.findData(31))
        panel.modes.setCurrentIndex(panel.modes.findData("parametric"))
        shape, frequency, gain, q = panel.parametric.rows[0]
        shape.setCurrentIndex(shape.findData("high_shelf"))
        frequency.setValue(14000)
        actions["Vocal Clarity"].trigger()
        assert len(panel.parametric.bands()) == 31
        assert panel.parametric.bands()[0] == Band(20, gain=-4, q=4.32)
        assert panel.graphic.gains() == (0,) * 31
        assert panel.response._response.max() <= 0.01
        actions["Flat"].trigger()
        assert panel.preamp.value() == 0
        assert all(band.gain == 0 for band in panel.parametric.bands())

        panel.counts.setCurrentIndex(panel.counts.findData(10))
        panel.modes.setCurrentIndex(panel.modes.findData("graphic"))
        assert panel.graphic.gains() == bass_boost
        panel.graphic.sliders[0].setValue(3)
        dialog._save()
        reopened = PluginSettingsDialog(manager, "equalizer", "Equalizer")
        _, saved_panel = reopened._instances[0]
        assert saved_panel.graphic.gains() == (3, *bass_boost[1:])
    finally:
        manager.unload_all()


def test_live_eq_preview_restores_saved_sound_and_bypass_is_temporary(qapp, monkeypatch):
    from ui.main_window import Player

    root = Path(__file__).resolve().parents[1] / "plugins"
    manager = PluginManager()
    saved_gains = [4 if frequency == 1000 else 0 for frequency in FREQUENCIES]
    manager.set_plugin_setting("equalizer", "equalizer", {"enabled": True, "gains": saved_gains})
    manager.load_from_file("equalizer", root / "core" / "equalizer" / "plugin.py")
    window = Player(plugin_manager=manager)
    manager.set_host_window(window)
    manager.emit("app_ready", window=window)
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    service = manager.get_service("playback_equalizer")
    window._refresh_equalizer_route()
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    source = (0.1 * np.sin(np.arange(4800) * (2 * np.pi * 1000 / 48000))).astype(np.float32).tobytes()
    processor = service.create_processor()
    saved = service.configuration

    dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer", parent=window)
    try:
        _, panel = dialog._instances[0]
        dialog.show()
        assert service.configuration == saved
        preview_updates = QSignalSpy(panel._response_timer.timeout)
        panel.graphic.sliders[5].setValue(-6)
        assert preview_updates.wait(1000)
        draft = (0, (Band(1000, gain=-6),))
        assert service.configuration == draft
        preview_pcm = processor.process(source, fmt)
        assert preview_pcm != source
        assert manager.get_plugin_setting("equalizer", "equalizer")["gains"] == saved_gains

        panel.bypass.setChecked(True)
        assert window._pcm_worker._controls.enabled
        assert processor.process(source, fmt) == source
        panel.bypass.setChecked(False)
        assert service.configuration == draft
        assert processor.process(source, fmt) == preview_pcm

        panel.graphic.sliders[5].setValue(-7)
        dialog.reject()
        assert service.configuration == saved
        assert not panel._response_timer.isActive()
        assert manager.get_plugin_setting("equalizer", "equalizer")["gains"] == saved_gains

        reopened = PluginSettingsDialog(manager, "equalizer", "Equalizer", parent=window)
        _, edit = reopened._instances[0]
        reopened.show()
        assert not edit.bypass.isChecked()
        preset = next(action for action in edit.presets.menu().actions() if action.text() == "Bass Boost")
        preset.trigger()
        preset_bands = tuple(band for band in graphic_bands(edit.graphic.gains()) if band.gain)
        assert service.configuration == (edit.preamp.value(), preset_bands)
        assert service.configuration != saved
        edit._reset()
        edit.graphic.sliders[5].setValue(-6)
        edit.bypass.setChecked(True)
        reopened._save()
        assert service.configuration == draft
        persisted = manager.get_plugin_setting("equalizer", "equalizer")
        assert persisted["enabled"] and persisted["gains"][5] == -6
        assert "bypass" not in persisted
    finally:
        window.close()
        manager.unload_all()


def test_named_eq_presets_persist_independently_of_the_current_curve(qapp, monkeypatch):
    root = Path(__file__).resolve().parents[1] / "plugins"
    path = root / "core" / "equalizer" / "plugin.py"
    manager = PluginManager()
    manager.load_from_file("equalizer", path)
    try:
        dialog = PluginSettingsDialog(manager, "equalizer", "Equalizer")
        _, panel = dialog._instances[0]
        save_preset = next(action for action in panel.presets.menu().actions() if action.text() == "Save current as…")
        monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("  Late Night  ", True))
        panel.graphic.sliders[0].setValue(-5)
        panel.preamp.setValue(-3)
        save_preset.trigger()

        panel.counts.setCurrentIndex(panel.counts.findData(31))
        panel.modes.setCurrentIndex(panel.modes.findData("parametric"))
        shape, frequency, gain, q = panel.parametric.rows[2]
        shape.setCurrentIndex(shape.findData("low_shelf"))
        frequency.setValue(72.5)
        gain.setValue(3.5)
        q.setValue(0.8)
        panel.preamp.setValue(-6)
        monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("Bass & Vocals", True))
        save_preset.trigger()
        dialog.reject()
        assert manager.get_plugin_setting("equalizer", "equalizer") is None

        manager.unload("equalizer")
        manager.load_from_file("equalizer", path)
        reopened = PluginSettingsDialog(manager, "equalizer", "Equalizer")
        _, panel = reopened._instances[0]
        assert panel.mode() == "graphic" and panel.count() == 10
        assert panel.graphic.sliders[0].value() == 0
        panel.graphic.sliders[1].setValue(2)
        by_name = {action.text(): action for action in panel._my_presets_menu.actions()}
        by_name["Bass && Vocals"].trigger()
        assert panel.mode() == "parametric" and panel.count() == 31
        assert panel.parametric.bands()[2] == Band(72.5, 3.5, 0.8, "low_shelf")
        assert panel.preamp.value() == -6
        by_name["Late Night"].trigger()
        assert panel.mode() == "graphic" and panel.count() == 10
        assert panel.graphic.gains()[:2] == (-5, 0)
        assert panel.preamp.value() == -3

        save_preset = next(action for action in panel.presets.menu().actions() if action.text() == "Save current as…")
        monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("late night", True))
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
        panel.graphic.sliders[0].setValue(-7)
        save_preset.trigger()
        assert manager.get_plugin_setting("equalizer", "custom_presets")["Late Night"]["gains"][0] == -5
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        save_preset.trigger()
        stored = manager.get_plugin_setting("equalizer", "custom_presets")
        assert set(stored) == {"Late Night", "Bass & Vocals"}
        assert stored["Late Night"]["gains"][0] == -7

        remove = next(action for action in panel._delete_preset_menu.actions() if action.text() == "Late Night")
        remove.trigger()
        assert set(manager.get_plugin_setting("equalizer", "custom_presets")) == {"Bass & Vocals"}
        assert panel.graphic.sliders[0].value() == -7
    finally:
        manager.unload_all()
