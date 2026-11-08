"""Auto-reading, pause affordances, and data-only branching state."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core import settings
from core.onboarding_script import compile_script
from core.onboarding_state import apply_state, matches_condition, parse_condition
from ui.onboarding import OnboardingDialog


@pytest.fixture
def wizard(qapp, monkeypatch):
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda key: [("Guide", r"Welcome.\n")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    qapp.processEvents()
    dialog.text_speed_combo.setCurrentIndex(dialog.text_speed_combo.findData("instant"))
    yield dialog
    dialog.close()
    qapp.processEvents()


def play(dialog, text):
    dialog._cancel_dialogue_activity()
    dialog.dialogue_lines = [("Guide", text), ("Guide", r"Next line.\e")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()


def test_auto_read_can_stop_and_restart_scripted_delay(wizard):
    play(wizard, r"Read this.\nw1")
    assert wizard.timer.isActive()
    assert wizard.timer.interval() == 1000
    wizard.auto_read_check.setChecked(False)
    assert not wizard.timer.isActive()
    assert wizard.dialogue_indicator.isVisible()
    wizard.auto_read_check.setChecked(True)
    assert wizard.timer.isActive()
    assert wizard.timer.interval() == 1000
    wizard.timer.timeout.emit()
    assert wizard.description.text() == "Next line."


@pytest.mark.parametrize("ending", ["", r"\n"])
def test_auto_read_schedules_completed_dialogue(wizard, ending):
    wizard.auto_read_check.setChecked(False)
    play(wizard, "Read me." + ending)
    assert not wizard.timer.isActive()
    wizard.auto_read_check.setChecked(True)
    assert wizard.timer.isActive()
    assert wizard.timer.interval() >= 1200
    wizard.auto_read_check.setChecked(False)
    wizard.advance_dialogue()
    assert wizard.description.text() == "Next line."


@pytest.mark.parametrize("cue", [r"\wait(0.05)", r"\wait(0.05) More.", r"\wait(0.02)\wait(0.03)"])
def test_pause_hides_arrow_until_time_is_up(wizard, cue):
    wizard.auto_read_check.setChecked(False)
    play(wizard, "Before." + cue)
    assert wizard.script_timer.isActive()
    assert not wizard.dialogue_indicator.isVisible()
    wizard.advance_dialogue()
    assert wizard.description.text() == "Before."
    wizard.auto_read_check.setChecked(True)
    assert not wizard.dialogue_indicator.isVisible()
    assert not wizard.timer.isActive()
    QTest.qWait(90)
    assert wizard.dialogue_indicator.isVisible()
    assert not wizard._pause_active
    assert wizard.description.text() == ("Before. More." if "More." in cue else "Before.")


def test_pause_transition_does_not_restore_arrow_over_choice(wizard):
    play(wizard, r"Choose.\wait(0.01)\button(Continue,route)")
    QTest.qWait(30)
    assert not wizard.dialogue_indicator.isVisible()
    assert wizard.route_buttons.count() == 1
    assert not wizard.timer.isActive()


@pytest.mark.parametrize("cue", [r"\waitevent(lyrics_search_started)", r"\idle(20)",
                                     r"\button(Continue,route)"])
def test_auto_read_does_not_bypass_interaction(wizard, cue):
    play(wizard, "Waiting." + cue)
    wizard.auto_read_check.setChecked(False)
    wizard.auto_read_check.setChecked(True)
    assert not wizard.timer.isActive()
    assert wizard.script_busy
    assert wizard.dialogue_index == 0


def test_auto_read_preference_and_keyboard_control(wizard, qapp):
    wizard.activateWindow()
    wizard.auto_read_check.setFocus()
    qapp.processEvents()
    QTest.keyClick(wizard.auto_read_check, Qt.Key.Key_Space)
    assert not wizard.auto_read_check.isChecked()
    wizard.close()
    assert settings.get_onboarding_pacing()["auto_read"] is False


def test_zero_delay_auto_still_respects_toggle(wizard):
    wizard.auto_read_check.setChecked(False)
    play(wizard, r"First.\nw0")
    QTest.qWait(20)
    assert wizard.description.text() == "First."
    wizard.auto_read_check.setChecked(True)
    QTest.qWait(20)
    assert wizard.description.text() == "Next line."


def test_script_variables_branch_across_routes_and_steps(wizard, monkeypatch):
    labels = compile_script('''default visited = false
default attempts = 0
label start:
    $ visited = true
    wizard "Start."
    $ attempts += 1
    $ set(mode, "online, (lyrics)")
    if visited:
        jump repeat
label repeat:
    wizard "Returned."
    if attempts >= 1:
        $ toggle(visited)
    if mode == "online, (lyrics)":
        jump success
label success:
    wizard "Success."
    $ end
''')
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: labels[key])
    wizard.auto_read_check.setChecked(False)
    wizard._cancel_dialogue_activity()
    wizard.dialogue_lines = labels["start"]
    wizard.dialogue_index = -1
    wizard.advance_dialogue()
    assert wizard.description.text() == "Success."
    assert wizard.variables == {"visited": False, "attempts": 1, "mode": "online, (lyrics)"}
    wizard.set_page(2)
    assert wizard.variables["attempts"] == 1


def test_conditional_text_preserves_remaining_commands_with_auto_read_off(wizard):
    lines = compile_script('''label scene:
    $ seen = true
    wizard "Question."
    if seen:
        wizard "A response."
    $ seen = false
    wizard "Continued."
    $ end
''')["scene"]
    wizard.auto_read_check.setChecked(False)
    wizard._cancel_dialogue_activity()
    wizard.dialogue_lines = lines
    wizard.dialogue_index = -1
    wizard.advance_dialogue()
    assert wizard.description.text() == "A response."
    wizard.advance_dialogue()
    assert wizard.description.text() == "Continued."
    assert wizard.variables["seen"] is False


def test_flags_survive_checkpoint_return(wizard, monkeypatch):
    labels = compile_script('''default visited = false
label firststeplanguage:
    checkpoint 5
    wizard "Checkpoint."
    if visited:
        jump done
    $ next
label mark:
    $ visited = true
    wizard "Returning."
    $ return_checkpoint(5)
label done:
    wizard "Returned with flag."
    $ end
''')
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: labels[key])
    monkeypatch.setattr("ui.onboarding.load_script", lambda: labels)
    wizard.select_route("mark")
    assert wizard.description.text() == "Returned with flag."
    assert wizard.variables["visited"] is True


@pytest.mark.parametrize("expression, expected", [
    ("missing", False), ("not missing", True), ("attempts >= 2", True),
    ('mode == "online"', True), ("enabled == 1", False), ("mode > 2", False),
    ('event == "lyrics_search_started"', True),
])
def test_safe_variable_conditions(expression, expected):
    values = {"attempts": 2, "mode": "online", "enabled": True}
    assert matches_condition(values, parse_condition(expression, "test"), "lyrics_search_started") is expected


@pytest.mark.parametrize("statement", [
    '$ value = __import__("os")', '$ value = [1, 2]', '$ value = NaN',
    '$ event = "fake"', '$ value += true',
    'if __import__("os"):\n        jump target',
    'if flag and other:\n        jump target',
])
def test_script_state_rejects_executable_or_unsupported_values(statement):
    with pytest.raises(ValueError, match="line"):
        compile_script('label scene:\n    wizard "Hello"\n    ' + statement + '\n')


def test_default_does_not_overwrite_and_invalid_counter_does_not_mutate():
    values = {"name": "original"}
    apply_state(values, {"op": "default", "name": "name", "value": "replacement"})
    with pytest.raises(ValueError, match="not a number"):
        apply_state(values, {"op": "add", "name": "name", "value": 1})
    assert values == {"name": "original"}
