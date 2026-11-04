import math

import pytest

from core.settings import save_playback_settings
from ui.main_window import Player
from ui.player_dialogs import PitchChangerDialog


@pytest.mark.parametrize("saved_source", [None, "playlist", "library"])
def test_playback_source_defaults_to_library_and_restores_choice(qapp, saved_source):
    if saved_source is not None:
        save_playback_settings({"play_mode": saved_source})
    window = Player()
    try:
        assert window.playback_source_combo.currentData() == (saved_source or "library")
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("saved_pitch", [0, 5])
def test_player_starts_and_pitch_dialog_controls_work(qapp, monkeypatch, saved_pitch):
    save_playback_settings({
        "pitch_mode": "Semitones",
        "pitch_value": saved_pitch,
        "speed_percent": 100,
        "volume": 100,
        "pitch_linked_to_speed": False,
    })
    window = Player()
    try:
        assert window.pitch_semitones == saved_pitch
        window.handle_pitch_change("Semitones", 5)

        def use_dialog(dialog):
            assert dialog.slider.value() == 500
            assert dialog.speed_slider.value() == 100
            dialog.speed_slider.setValue(125)
            assert window.playback_speed == 1.25
            assert window._displayed_pitch_semitones() == 5
            dialog.link_pitch_checkbox.setChecked(True)
            assert window.pitch_linked_to_speed
            assert window._displayed_pitch_semitones() == pytest.approx(12 * math.log2(1.25))
            dialog.link_pitch_checkbox.setChecked(False)
            assert not window.pitch_linked_to_speed
            assert window._displayed_pitch_semitones() == 5
            dialog.slider.setValue(700)
            assert window.pitch_semitones == 7
            return 0

        monkeypatch.setattr(PitchChangerDialog, "exec", use_dialog)
        window.open_pitch_options()
        window.toggle_time_display()
        assert window.show_remaining_time
        window.toggle_time_display()
        assert not window.show_remaining_time
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_pitch_quick_presets_follow_selected_units(qapp):
    changes = []
    dialog = PitchChangerDialog(0, "Semitones", lambda mode, value: changes.append((mode, value)))
    try:
        assert [button.text() for button in dialog.quick_pitch_buttons] == [
            "-12 st", "-7 st", "-5 st", "-2 st", "Reset", "+2 st", "+5 st", "+7 st", "+12 st",
        ]

        dialog.unit_combo.setCurrentText("Octaves")
        assert [button.text() for button in dialog.quick_pitch_buttons] == [
            "-2 oct", "-1 oct", "-0.5 oct", "-0.25 oct", "Reset",
            "+0.25 oct", "+0.5 oct", "+1 oct", "+2 oct",
        ]
        dialog.quick_pitch_buttons[6].click()
        assert dialog.slider.value() == 600
        assert changes[-1] == ("Octaves", 0.5)

        dialog.unit_combo.setCurrentText("Percent")
        assert [button.text() for button in dialog.quick_pitch_buttons] == [
            "50%", "75%", "90%", "95%", "Reset", "105%", "110%", "125%", "150%",
        ]
        dialog.quick_pitch_buttons[7].click()
        assert dialog.slider.value() == round(12 * math.log2(1.25) * 100)
        assert changes[-1] == ("Percent", 125.0)
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("mode,text,spin_value,semitones", [
    ("Semitones", "+3", 3.0, 3.0),
    ("Octaves", "+0.25", 0.25, 3.0),
    ("Percent", "125", 125.0, round(12 * math.log2(1.25), 2)),
])
@pytest.mark.parametrize("key_name", ["Key_Return", "Key_Enter"])
def test_fine_tune_enter_commits_current_units_without_activating_preset(
        qapp, mode, text, spin_value, semitones, key_name):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    changes = []
    dialog = PitchChangerDialog(0, mode, lambda *args: changes.append(args))
    try:
        dialog.show()
        qapp.processEvents()
        dialog.value_spin.setFocus()
        dialog.value_spin.selectAll()
        QTest.keyClicks(dialog.value_spin, text)
        QTest.keyClick(dialog.value_spin, getattr(Qt.Key, key_name))
        qapp.processEvents()
        assert dialog.isVisible()
        assert dialog.value_spin.value() == pytest.approx(spin_value)
        assert dialog.slider.value() == round(semitones * 100)
        assert changes == [(mode, spin_value)]
        assert mode.lower().rstrip("s") in dialog.value_spin.toolTip().lower()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_pitch_drag_coalesces_updates_and_applies_final_value(qapp):
    from PySide6.QtTest import QTest

    changes = []
    dialog = PitchChangerDialog(0, "Semitones", lambda *args: changes.append(args))
    try:
        dialog.slider.setSliderDown(True)
        for value in range(1, 501):
            dialog.slider.setValue(value)
        assert not changes
        assert dialog.slider.value() == 500
        for _ in range(200):
            if changes:
                break
            QTest.qWait(10)
        assert changes == [("Semitones", 5.0)]
        dialog.slider.setValue(550)
        dialog.slider.setSliderDown(False)
        assert changes[-1] == ("Semitones", 5.5)
        assert not dialog._pitch_update_timer.isActive()
    finally:
        dialog.close()


def test_pitch_preferences_save_once_after_adjustments(qapp, monkeypatch):
    from PySide6.QtTest import QTest
    from ui import main_window

    window = Player()
    saved = []
    monkeypatch.setattr(main_window, "save_playback_settings", lambda values: saved.append(dict(values)))
    try:
        for value in range(1, 11):
            window.handle_pitch_change("Semitones", value / 10)
        assert not saved
        QTest.qWait(400)
        assert len(saved) == 1
        assert saved[0]["pitch_value"] == 1.0
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
