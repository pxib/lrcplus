"""Structured branches and reusable calls preserve the script execution order."""

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core.onboarding_script import compile_script, load_script
from ui.onboarding import OnboardingDialog


@pytest.fixture
def scene(qapp, monkeypatch):
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda key: [("Guide", r"Welcome.\n")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.auto_read_check.setChecked(False)
    dialog.text_speed_combo.setCurrentIndex(dialog.text_speed_combo.findData("instant"))

    def start(source, label="start"):
        labels = compile_script(source)
        monkeypatch.setattr("ui.onboarding.load_route", lambda name: labels.get(name, []))
        dialog.select_route(label)
        return dialog

    yield start
    dialog.close()
    qapp.processEvents()


def test_nested_calls_return_after_dialogue_and_resume_actions(scene):
    dialog = scene('''default count = 0
label start:
    wizard "Before."
    call outer
    $ count += 1
    wizard "After."
    $ end
label outer:
    $ count += 10
    call inner
    $ count += 100
    return
label inner:
    wizard "Inner."
    $ pause(0.01)
    $ count += 1000
    return
''')
    assert dialog.description.text() == "Before."
    assert not dialog._call_stack
    dialog.advance_dialogue()
    assert dialog.description.text() == "Inner."
    assert len(dialog._call_stack) == 2
    for _ in range(50):
        if dialog.variables["count"] == 1010:
            break
        QTest.qWait(10)
    assert dialog.description.text() == "Inner."
    assert dialog.variables["count"] == 1010
    dialog.advance_dialogue()
    assert dialog.description.text() == "After."
    assert dialog.variables["count"] == 1111
    assert not dialog._call_stack
    assert dialog.next.isEnabled()


def test_inline_call_preserves_remaining_text_and_pause(scene):
    dialog = scene('''label start:
    wizard "Before"
    $ call helper
    extend " after."
    $ pause(0.01)
    $ end
label helper:
    wizard "Helper."
    return
''')
    assert dialog.description.text() == "Helper."
    dialog.advance_dialogue()
    assert dialog.description.text() == "Before after."
    assert dialog.script_timer.isActive()
    QTest.qWait(30)
    assert dialog.next.isEnabled()


@pytest.mark.parametrize("value, expected", [(0, "Else."), (1, "First."), (2, "Second.")])
def test_if_elif_else_selects_one_multistatement_block(scene, value, expected):
    dialog = scene(f'''default value = {value}
default count = 0
label start:
    if value == 1:
        $ count += 1
        wizard "First."
    elif value == 2:
        $ count += 2
        wizard "Second."
    else:
        $ count += 3
        wizard "Else."
    wizard "Done."
    $ end
''')
    assert dialog.description.text() == expected
    assert dialog.variables["count"] == {0: 3, 1: 1, 2: 2}[value]
    dialog.advance_dialogue()
    assert dialog.description.text() == "Done."


def test_nested_conditions_and_comments_do_not_leak_unselected_state(scene):
    dialog = scene('''default first = true
default second = false
label start:
    if first:
        # Nested blocks may include comments and blank lines.
        if second:
            $ result = "wrong"
        else:
            $ result = "right"
            wizard "Nested."
    else:
        $ result = "also wrong"
        wizard "Wrong."
    wizard "Finished."
    $ end
''')
    assert dialog.description.text() == "Nested."
    assert dialog.variables["result"] == "right"
    dialog.advance_dialogue()
    assert dialog.description.text() == "Finished."


def test_elif_is_not_rechecked_after_selected_body_changes_state(scene):
    dialog = scene('''default value = 1
label start:
    if value == 1:
        $ value = 2
        $ selected = "first"
    elif value == 2:
        $ selected = "wrong"
    wizard "Ready."
    $ end
''')
    assert dialog.variables["selected"] == "first"
    assert dialog.description.text() == "Ready."


def test_menu_body_calls_helper_then_resumes_after_menu(scene):
    dialog = scene('''label start:
    wizard "Choose."
    menu:
        "Details (optional)":
            $ chosen = true
            call help
            $ completed = true
        "Skip":
            $ chosen = false
    wizard "Back at setup."
    $ end
label help:
    wizard "Here's the explanation."
    return
''')
    assert dialog.route_buttons.count() == 2
    dialog.route_buttons.itemAt(0).widget().click()
    assert dialog.description.text() == "Here's the explanation."
    assert dialog.variables["chosen"] is True
    assert "completed" not in dialog.variables
    dialog.advance_dialogue()
    assert dialog.description.text() == "Back at setup."
    assert dialog.variables["completed"] is True
    assert not dialog._call_stack
    assert dialog.route_buttons.count() == 0


def test_callee_event_wait_then_return_preserves_caller(scene):
    dialog = scene('''label start:
    call helper
    wizard "Back."
    $ end
label helper:
    wizard "Waiting."
    $ wait_event(lyrics_search_started)
    if event == "LYRICS_SEARCH_STARTED":
        $ received = true
    else:
        $ received = false
    return
''')
    assert dialog.waiting_events == {"lyrics_search_started"}
    dialog.events.occurred.emit("lyrics_search_started")
    assert dialog.description.text() == "Back."
    assert dialog.variables["received"] is True
    assert not dialog.waiting_events


def test_jump_inside_call_keeps_return_address(scene):
    dialog = scene('''label start:
    call helper
    wizard "Back."
    $ end
label helper:
    jump other
label other:
    $ visited = true
    return
''')
    assert dialog.variables["visited"] is True
    assert dialog.description.text() == "Back."


def test_page_change_discards_abandoned_return_addresses(scene):
    dialog = scene('''label start:
    call helper
    wizard "Old caller."
label helper:
    wizard "Waiting."
    $ wait_event(lyrics_search_started)
    return
''')
    assert len(dialog._call_stack) == 1
    dialog.set_page(2)
    assert not dialog._call_stack
    assert not dialog.waiting_events
    dialog.events.occurred.emit("lyrics_search_started")
    assert not dialog.dialogue_lines


def test_return_without_caller_finishes_conversation(scene):
    dialog = scene('''label start:
    return
''')
    assert dialog.next.isEnabled()
    assert not dialog.dialogue_lines


def test_missing_call_logs_and_continues_without_pushing_frame(scene, caplog):
    dialog = scene('''label start:
    call missing
    wizard "Still here."
    $ end
''')
    assert dialog.description.text() == "Still here."
    assert not dialog._call_stack
    assert "missing" in caplog.text


def test_end_of_helper_returns_implicitly(scene):
    dialog = scene('''label start:
    call helper
    wizard "Back."
    $ end
label helper:
    wizard "Last helper line."
''')
    assert dialog.description.text() == "Last helper line."
    dialog.advance_dialogue()
    assert dialog.description.text() == "Back."
    assert not dialog._call_stack


def test_explicit_end_inside_helper_abandons_the_call(scene):
    dialog = scene('''label start:
    call helper
    wizard "Should not run."
label helper:
    wizard "Leaving setup."
    $ end
''')
    assert dialog.description.text() == "Leaving setup."
    assert dialog.next.isEnabled()
    assert not dialog._call_stack


def test_recursive_calls_have_a_bounded_stack_and_can_finish(scene, qapp, caplog):
    dialog = scene('''label start:
    call helper
    wizard "Recovered."
    $ end
label helper:
    call helper
    return
''')
    for _ in range(20):
        qapp.processEvents()
        assert len(dialog._call_stack) <= 64
        if not dialog._call_stack:
            break
    assert dialog.description.text() == "Recovered."
    assert dialog.next.isEnabled()
    assert "call depth limit" in caplog.text


@pytest.mark.parametrize("body", [
    'if flag:\n    wizard "Not indented"',
    'else:\n        wizard "Orphan"',
    'if flag:\n        wizard "One"\n    else:\n        wizard "Two"\n    elif flag:\n        wizard "Three"',
    'if flag:\n          wizard "Wrong indentation"',
    'menu:\n        "Empty":',
    'if __import__("os"):\n        wizard "No"',
])
def test_invalid_blocks_have_line_diagnostics(body):
    with pytest.raises(ValueError, match="line"):
        compile_script("label start:\n    " + body + "\n")


@pytest.mark.parametrize("prefix", ["", "# Saved by an editor\n", "\n", "default ready = true\n"])
def test_compiler_accepts_leading_utf8_bom(prefix):
    source = prefix + 'label start:\n    wizard "こんにちは"\n    $ end\n'
    assert compile_script("\ufeff" + source) == compile_script(source)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_load_script_accepts_utf8_with_or_without_bom(tmp_path, encoding):
    source = '# Dialogue\nlabel start:\n    wizard "こんにちは"\n    $ end\n'
    path = tmp_path / "onboarding.rpy"
    path.write_text(source, encoding=encoding)
    assert load_script(path) == compile_script(source)


def test_bom_handling_preserves_dialogue_characters_and_line_diagnostics():
    source = '\ufefflabel start:\n    wizard "keep\ufeffthis"\n'
    assert compile_script(source)["start"][0][1] == "keep\ufeffthis"
    with pytest.raises(ValueError, match="line 3"):
        compile_script('\ufeff# Header\nlabel start:\n  wizard "Bad indent"\n')


@pytest.mark.parametrize("cue", ['sound("idea")', "pause(0.01)", "hide_cursor", "checkpoint(9)"])
def test_before_cues_apply_to_extended_dialogue(cue):
    prefix = 'label start:\n    wizard "Before."\n    $ pause(0.01)\n'
    suffix = '    extend " After."\n    $ end\n'
    assert compile_script(prefix + "    before " + cue + "\n" + suffix) == compile_script(
        prefix + "    $ " + cue + "\n" + suffix)


def test_extend_with_a_before_cue_still_requires_previous_dialogue():
    with pytest.raises(ValueError, match="line 3: extend requires a previous line"):
        compile_script('label start:\n    before sound("idea")\n    extend "No previous line."\n')
