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
