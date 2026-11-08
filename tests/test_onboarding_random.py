"""Random dialogue selects one complete variant and preserves surrounding flow."""

import pytest
from pathlib import Path
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core.onboarding_script import compile_script
from ui.onboarding import OnboardingDialog


@pytest.fixture
def scene(qapp, monkeypatch):
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda key: [("Guide", r"Welcome.\n")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.auto_read_check.setChecked(False)
    dialog.sound_check.setChecked(False)
    dialog.text_speed_combo.setCurrentIndex(dialog.text_speed_combo.findData("instant"))

    def start(source, pick=0):
        labels = compile_script(source)
        monkeypatch.setattr("ui.onboarding.load_route", lambda name: labels[name])
        monkeypatch.setattr("ui.onboarding.random.choice", lambda variants: variants[pick])
        dialog.select_route("start")
        return dialog

    yield start
    dialog.close()
    qapp.processEvents()


@pytest.mark.parametrize("pick, text", [(0, "Hello."), (1, "Hey."), (2, "Welcome.")])
def test_random_dialogue_list_selects_one_line_and_then_continues(scene, pick, text):
    dialog = scene('''label start:
    random:
        wizard "Hello."
        "Hey."
        wizard "Welcome."
    wizard "After."
    $ end
''', pick)
    assert dialog.description.text() == text
    assert dialog.route_buttons.count() == 0
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."
    assert dialog.next.isEnabled()


@pytest.mark.parametrize("pick, expected", [(0, 1), (1, 10)])
def test_random_choices_only_run_selected_actions(scene, pick, expected):
    dialog = scene('''default count = 0
label start:
    random:
        choice:
            $ count += 1
            before sound("playful")
            wizard "First."
        choice:
            $ count += 10
            before sound("confused")
            wizard "Second."
    wizard "Done."
    $ end
''', pick)
    assert dialog.variables["count"] == expected
    assert dialog.expression_label.text() == ("Playful" if pick == 0 else "Confused")
    dialog.advance_dialogue()
    assert dialog.description.text() == "Done."
    assert not dialog.expression_label.isVisible()


def test_common_pause_sound_and_extend_follow_the_selected_variant(scene):
    dialog = scene('''label start:
    before sound("playful")
    random:
        wizard "First."
        wizard "Second."
    $ pause(0.01)
    before sound("idea")
    extend " More."
    $ next
    wizard "After."
    $ end
''', 1)
    assert dialog.description.text() == "Second."
    assert dialog.expression_label.text() == "Playful"
    assert dialog.script_timer.isActive()
    QTest.qWait(30)
    assert dialog.description.text() == "Second. More."
    assert dialog.expression_label.text() == "Inspired"
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."


def test_random_dialogue_between_auto_lines_is_not_skipped(scene, monkeypatch):
    dialog = scene('''label start:
    wizard "Before."
    $ auto(0)
    random:
        wizard "Picked."
        wizard "Other."
    $ auto(0)
    wizard "After."
    $ end
''')
    selections = []

    def pick(variants):
        selections.append(True)
        return variants[0]

    monkeypatch.setattr("ui.onboarding.random.choice", pick)
    assert dialog.description.text() == "Before."
    dialog.auto_read_check.setChecked(True)
    QTest.qWait(40)
    assert selections == [True]
    assert dialog.description.text() == "After."
    assert dialog.next.isEnabled()


def test_random_is_selected_again_each_time_label_is_visited(scene, monkeypatch):
    dialog = scene('''label start:
    random:
        wizard "First visit."
        wizard "Second visit."
''')
    assert dialog.description.text() == "First visit."
    monkeypatch.setattr("ui.onboarding.random.choice", lambda variants: variants[0])
    dialog.select_route("start")
    assert dialog.description.text() == "Second visit."


def test_random_helper_returns_to_its_caller(scene):
    dialog = scene('''label start:
    call helper
    wizard "Returned."
    $ end
label helper:
    random:
        wizard "One explanation."
        wizard "Another explanation."
    return
''', 1)
    assert dialog.description.text() == "Another explanation."
    assert len(dialog._call_stack) == 1
    dialog.advance_dialogue()
    assert dialog.description.text() == "Returned."
    assert not dialog._call_stack


def test_choice_can_contain_nested_condition_and_event_wait(scene):
    dialog = scene('''default enabled = true
label start:
    random:
        choice:
            if enabled:
                wizard "Waiting."
                $ wait_event(lyrics_search_started)
            else:
                wizard "Wrong branch."
            wizard "Received."
        choice:
            wizard "Not chosen."
    wizard "Finished."
    $ end
''')
    assert dialog.description.text() == "Waiting."
    assert dialog.waiting_events == {"lyrics_search_started"}
    dialog.events.occurred.emit("lyrics_search_started")
    assert dialog.description.text() == "Received."
    dialog.advance_dialogue()
    assert dialog.description.text() == "Finished."


def test_random_block_can_nest_inside_another_choice(scene):
    dialog = scene('''label start:
    random:
        choice:
            random:
                wizard "Nested one."
                wizard "Nested two."
        wizard "Unselected."
    wizard "After."
    $ end
''')
    assert dialog.description.text() == "Nested one."
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."


def farewell_script():
    script = Path("assets/onboarding/onboarding.rpy").read_text(encoding="utf-8")
    start = script.index('    wizard "Thanks for hanging out with a setup window')
    end = script.index("\nlabel lyrics_local:", start)
    return "label start:\n" + script[start:end]


def advance_scene(dialog):
    if dialog.script_timer.isActive():
        dialog.script_timer.stop()
        dialog.finish_script_wait()
    else:
        dialog.advance_dialogue()


@pytest.mark.parametrize("pick", [0, 1])
def test_actual_farewell_always_offers_optional_ddlc_route(scene, pick):
    dialog = scene(farewell_script(), pick)
    dialog.advance_dialogue()
    assert [dialog.route_buttons.itemAt(i).widget().text() for i in range(dialog.route_buttons.count())] == [
        "I love you", "You'll always be my dearest friend.", "Let's play some music"
    ]


@pytest.mark.parametrize("farewell, decisions, expected", [
    (0, [], "I'll do my best to keep your lyrics on time."),
    (2, [], "Best idea I've heard all day."),
    (1, ["Back to the music"], "Meeting adjourned."),
    (1, ["Keep going", "Back to the music"], "Meeting adjourned."),
    (1, ["Keep going", "She's part of the club."], "Thanks for sticking up for me."),
    (1, ["Keep going", "Offer the developer a playlist."], "Diplomacy through playlists."),
])
def test_actual_ddlc_branches_return_to_setup_and_close_windows(scene, qapp, farewell, decisions, expected):
    dialog = scene(farewell_script())
    dialog.reduced_motion_check.setChecked(True)
    dialog.advance_dialogue()
    dialog.route_buttons.itemAt(farewell).widget().click()
    pending = list(decisions)
    saw_developer = saw_terminal = False
    displayed = []
    for _ in range(250):
        qapp.processEvents()
        saw_developer |= dialog.developer_window is not None
        if dialog.terminal_window is not None:
            saw_terminal = True
            assert "Microsoft Windows" in dialog.terminal_window.scene_text.text()
        displayed.append(dialog.description.text())
        if dialog.description.text() == "Next takes you to the finish.":
            break
        assert dialog.description.text(), "Unexpected blank dialogue after a choice"
        if dialog.route_buttons.count():
            assert pending, "Unexpected extra choice"
            wanted = pending.pop(0)
            buttons = [dialog.route_buttons.itemAt(i).widget() for i in range(dialog.route_buttons.count())]
            next(button for button in buttons if button.text() == wanted).click()
        else:
            advance_scene(dialog)
    assert dialog.description.text() == "Next takes you to the finish."
    assert any(expected in text for text in displayed), "\n".join(displayed)
    assert not pending
    assert not dialog._call_stack
    assert not dialog.route_buttons.count()
    assert not dialog.waiting_events
    assert dialog.next.isEnabled()
    assert dialog.developer_window is None and dialog.terminal_window is None
    reaches_developer = len(decisions) == 2
    assert saw_developer == reaches_developer
    assert saw_terminal == reaches_developer
    if reaches_developer and decisions[-1] != "Back to the music":
        assert dialog.variables["ddlc_defended"] == (decisions[-1] == "She's part of the club.")
        assert any("Keep the song you love. Leave room for the next." in text for text in displayed)


def test_developer_window_resizes_for_long_dialogue(scene):
    line = "You're just a... parody of Ren'Py. Can't you look at your ones and zeros?"
    dialog = scene('label start:\n    developer "' + line + '"\n    $ end\n')
    window = dialog.developer_window
    QTest.qWait(300)
    label = window.scene_text
    assert label.text() == line
    assert label.height() >= label.heightForWidth(label.width())


@pytest.mark.parametrize("body", [
    "random:", 'random:\n        choice:',
    'random:\n        $ value = 1',
    'random:\n      wizard "Bad indentation."',
    'random:\n        __import__("os")',
])
def test_invalid_random_blocks_have_line_diagnostics(body):
    with pytest.raises(ValueError, match="line"):
        compile_script("label start:\n    " + body + "\n")
