"""Preset reactions remain usable after the appearance introduction ends."""

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core import settings
from core.onboarding_script import load_script
from ui.onboarding import OnboardingDialog
from ui.settings_dialog import _load_theme_color_presets


@pytest.fixture
def wizard(qapp, monkeypatch):
    labels = load_script()
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda name: labels.get(name))
    monkeypatch.setattr("ui.onboarding.load_route", lambda name: labels.get(name))
    player = QWidget()
    player.library_folders = []
    player.appearance_settings = settings.get_appearance_settings()
    player._apply_main_window_style = lambda: None
    player._apply_appearance_settings = lambda: None
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.auto_read_check.setChecked(False)
    dialog.text_speed_combo.setCurrentIndex(dialog.text_speed_combo.findData("instant"))
    dialog.set_page(2)
    dialog.show_dialogue()
    # Finish the authored introduction through its normal choices and waits.
    for _ in range(50):
        if not dialog.dialogue_lines:
            break
        if dialog.route_buttons.count():
            dialog.route_buttons.itemAt(1).widget().click()
        elif dialog._pause_active:
            dialog.script_timer.stop()
            dialog.finish_script_wait()
        else:
            dialog.advance_dialogue()
    assert not dialog.dialogue_lines
    assert dialog.preset_combo.isVisible()
    yield dialog
    dialog.done(dialog.DialogCode.Rejected)


def finish_reaction(dialog):
    for _ in range(12):
        if not dialog._call_stack:
            return
        if dialog._pause_active:
            dialog.script_timer.stop()
            dialog.finish_script_wait()
        else:
            dialog.advance_dialogue()
    pytest.fail("Theme reaction did not return")


@pytest.mark.parametrize("name", ["Green", "Blue", "Purple", "Rose", "Orange"] +
                         [name for name, _ in _load_theme_color_presets()])
def test_named_theme_reacts_and_restores_setup(wizard, name):
    before = wizard.description.text()
    index = wizard.preset_combo.findText(name)
    wizard.choose_preset(index)
    preset = wizard.preset_combo.itemData(index)
    if isinstance(preset, dict):
        assert wizard.player.appearance_settings["theme_preset_name"] == name
    else:
        assert wizard.player.appearance_settings["theme_color"] == preset
    assert name in wizard.description.text()
    assert wizard.space.isEnabled()
    assert wizard.next.isEnabled()
    assert wizard.preset_combo.isVisible()
    finish_reaction(wizard)
    assert wizard.description.text() == before
    assert not wizard.space.isEnabled()
    assert not wizard.dialogue_lines
    assert wizard.next.isEnabled()


def test_reselect_is_silent_but_switching_away_allows_another_reaction(wizard):
    sakura = wizard.preset_combo.findText("Sakura")
    wizard.choose_preset(sakura)
    finish_reaction(wizard)
    wizard.choose_preset(sakura)
    assert not wizard._call_stack
    wizard.preset_combo.addItem("Custom Teal", "#008888")
    wizard.choose_preset(wizard.preset_combo.findText("Custom Teal"))
    assert not wizard._call_stack
    wizard.choose_preset(sakura)
    assert wizard._call_stack


def test_rapid_browsing_keeps_latest_theme_reaction(wizard):
    wizard.choose_preset(wizard.preset_combo.findText("Matcha"))
    wizard.choose_preset(wizard.preset_combo.findText("Reimu"))
    wizard.choose_preset(wizard.preset_combo.findText("Cyberpunk"))
    wizard.choose_preset(wizard.preset_combo.findText("Cyberpunk"))
    assert [event for event in wizard.pending_events if event.startswith("theme_")] == ["theme_cyberpunk"]
    finish_reaction(wizard)
    QTest.qWait(10)
    assert "Cyberpunk" in wizard.description.text()
    finish_reaction(wizard)
    QTest.qWait(10)
    assert not wizard._call_stack


def test_page_change_discards_theme_comment(wizard):
    wizard.choose_preset(wizard.preset_combo.findText("Matcha"))
    wizard.next_page()
    assert wizard.page == 3
    assert not wizard._call_stack
    assert not any(event.startswith("theme_") for event in wizard._reaction_routes)
