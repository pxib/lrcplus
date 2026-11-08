"""Session randomness, guarded choices, and event reaction continuations."""

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core.onboarding_script import compile_script
from ui.onboarding import OnboardingDialog


@pytest.fixture
def scene(qapp, monkeypatch):
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda key: [("Setup Wizard", "Welcome.")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.auto_read_check.setChecked(False)
    dialog.text_speed_combo.setCurrentIndex(dialog.text_speed_combo.findData("instant"))

    def start(source):
        labels = compile_script(source)
        monkeypatch.setattr("ui.onboarding.load_route", lambda name: labels.get(name))
        dialog.select_route("start")
        return dialog

    yield start
    dialog.done(dialog.DialogCode.Rejected)


def test_random_exhausts_each_block_independently_and_refills(scene, monkeypatch):
    monkeypatch.setattr("ui.onboarding.random.choice", lambda items: items[0])
    dialog = scene('''label start:
    random:
        "A"
        "B"
        "C"
label other:
    random:
        "A"
        "B"
        "C"
''')
    assert dialog.description.text() == "A"
    dialog.select_route("other")
    assert dialog.description.text() == "A"
    seen = []
    for _ in range(5):
        dialog.select_route("start")
        seen.append(dialog.description.text())
    assert seen == ["B", "C", "A", "B", "C"]


def test_guarded_menu_hides_disables_and_resumes_after_selection(scene):
    dialog = scene('''default tried = false
label start:
    wizard "Choose."
    menu:
        "Hidden" if tried:
            wizard "Wrong."
        "Disabled" enabled_if tried:
            wizard "Wrong."
        "Try":
            $ tried = true
            wizard "Selected."
    wizard "After."
''')
    assert dialog.route_buttons.count() == 2
    assert not dialog.route_buttons.itemAt(0).widget().isEnabled()
    dialog.route_buttons.itemAt(1).widget().click()
    assert dialog.variables["tried"] is True
    assert dialog.description.text() == "Selected."
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."
    dialog.select_route("start")
    assert dialog.route_buttons.count() == 3
    assert dialog.route_buttons.itemAt(0).widget().isEnabled()


def test_menu_with_no_available_choice_continues(scene):
    dialog = scene('''label start:
    wizard "Choose."
    menu:
        "Hidden" if missing:
            jump wrong
        "Disabled" enabled_if missing:
            jump wrong
    wizard "After."
''')
    assert dialog.route_buttons.count() == 0
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."


def test_fractional_auto_delay_keeps_text_clean_and_respects_auto_read(scene):
    dialog = scene('''label start:
    wizard "Read this."
    $ auto(0.4)
    wizard "After."
''')
    assert dialog.description.text() == "Read this."
    assert dialog._read_delay_ms == 400
    assert not dialog.timer.isActive()
    dialog.auto_read_check.setChecked(True)
    assert dialog.timer.isActive()
    assert dialog.timer.interval() == 400


REACTION = '''
label reaction:
    $ count += 1
    wizard "Moved!"
    return
'''


def test_reaction_restores_manual_line_and_does_not_repeat_actions(scene):
    dialog = scene('''default count = 0
label start:
    before react(wizard_moved, reaction)
    wizard "Original."
    $ count += 10
    wizard "After."
''' + REACTION)
    dialog.receive_event("wizard_moved")
    assert dialog.description.text() == "Moved!"
    dialog.receive_event("wizard_moved")
    assert len(dialog._call_stack) == 1
    dialog.advance_dialogue()
    assert dialog.description.text() == "Original."
    assert dialog.variables["count"] == 11
    QTest.qWait(10)
    assert dialog.description.text() == "Moved!"
    dialog.advance_dialogue()
    assert dialog.description.text() == "Original."
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."
    assert dialog.variables["count"] == 12


def test_reaction_restores_unfinished_typing(scene):
    dialog = scene('''label start:
    before react(wizard_moved, reaction)
    wizard "Original text is still typing."
    wizard "After."
''' + REACTION)
    dialog.text_speed_combo.setCurrentIndex(dialog.text_speed_combo.findData("normal"))
    dialog.select_route("start")
    dialog.type_character()
    index = dialog.current_text_index
    dialog.receive_event("wizard_moved")
    dialog.finish_typing()
    dialog.advance_dialogue()
    assert dialog.typing_timer.isActive()
    assert dialog.current_text_index == index
    assert dialog.description.text() == "Original text is still typing."[:index]
    dialog.finish_typing()
    assert dialog.description.text() == "Original text is still typing."


def test_reaction_suspends_pause_and_restores_remaining_time(scene):
    dialog = scene('''label start:
    before react(wizard_moved, reaction)
    wizard "Before."
    $ pause(0.2)
    extend " After."
''' + REACTION)
    dialog.receive_event("wizard_moved")
    QTest.qWait(240)
    assert dialog.description.text() == "Moved!"
    dialog.advance_dialogue()
    assert dialog.script_timer.isActive()
    assert dialog.description.text() == "Before."
    QTest.qWait(240)
    assert dialog.description.text() == "Before. After."


def test_waited_events_take_priority_and_buffer_while_reacting(scene):
    dialog = scene('''label start:
    before react(wizard_moved, reaction)
    wizard "Waiting."
    $ wait_event(lyrics_search_results)
    wizard "Received."
''' + REACTION)
    dialog.receive_event("wizard_moved")
    dialog.receive_event("lyrics_search_results")
    assert dialog.description.text() == "Moved!"
    dialog.advance_dialogue()
    QTest.qWait(10)
    assert dialog.description.text() == "Received."
    assert dialog.last_event == "lyrics_search_results"


def test_reaction_waits_until_menu_is_answered(scene):
    dialog = scene('''label start:
    before react(wizard_moved, reaction)
    wizard "Choose."
    menu:
        "Continue":
            wizard "Chosen."
    wizard "After."
''' + REACTION)
    dialog.receive_event("wizard_moved")
    assert dialog.route_buttons.count() == 1
    dialog.route_buttons.itemAt(0).widget().click()
    QTest.qWait(10)
    assert dialog.description.text() == "Moved!"
    dialog.advance_dialogue()
    assert dialog.description.text() == "Chosen."


@pytest.mark.parametrize("ending", ["return", 'wizard "Closing."\n    $ end'])
def test_reaction_inside_helper_returns_then_resumes_call(scene, ending):
    dialog = scene('''label start:
    before react(wizard_moved, reaction)
    wizard "Intro."
    call helper
    wizard "After."
label helper:
    wizard "Helping."
    return
label reaction:
    wizard "Reaction."
    ''' + ending + '\n')
    dialog.advance_dialogue()
    dialog.receive_event("wizard_moved")
    dialog.advance_dialogue()
    assert dialog.description.text() == "Helping."
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."
    assert not dialog._call_stack


def test_page_change_discards_reaction_and_handlers(scene):
    dialog = scene('''label start:
    before react(wizard_moved, reaction)
    wizard "Original."
''' + REACTION)
    dialog.receive_event("wizard_moved")
    dialog.set_page(1)
    assert not dialog._call_stack
    assert not dialog._reaction_routes
    dialog.receive_event("wizard_moved")
    assert not dialog.dialogue_lines


@pytest.mark.parametrize("option", ['"Bad" if x and y:', '"Bad" enabled_if __import__("os"):',
                                    '"Bad" if x == [1]:'])
def test_invalid_choice_conditions_report_line(option):
    with pytest.raises(ValueError, match="line 3"):
        compile_script('label start:\n    menu:\n        ' + option + '\n            return\n')
