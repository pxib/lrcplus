import math

import pytest

from core.settings import save_playback_settings
from ui.main_window import Player
from ui.player_dialogs import PitchChangerDialog


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
            assert dialog.slider.value() == 50
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
            dialog.slider.setValue(70)
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
