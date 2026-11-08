from PySide6.QtCore import QEasingCurve, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QPushButton, QToolButton, QWidget
import pytest

from core import settings
from core.onboarding_script import compile_script, load_script
from ui.onboarding import FINISH_PAGE, LYRICS_PAGE, OnboardingDialog, load_dialogue, load_route, needs_onboarding, parse_dialogue_line


def test_renpy_style_script_compiles_menu_conditions_and_inline_actions():
    script = '''label intro:
    before checkpoint(6)
    wizard "Hello"
    $ pause(1)
    extend " there!"
    $ wait_event(lyrics_search_results,lyrics_search_failed)
    if event == "lyrics_search_failed":
        jump failed
    menu:
        "Continue":
            jump done
label done:
    wizard "Bye"
    $ end
'''
    labels = compile_script(script)
    assert labels["intro"] == [("Setup Wizard", r"\c6Hello\wait(1) there!\waitevent(lyrics_search_results,lyrics_search_failed)\if(lyrics_search_failed,failed)\button(Continue,done)")]
    assert labels["done"] == [("Setup Wizard", r"Bye\e")]


def test_current_script_contains_setup_and_route_labels():
    labels = load_script()
    assert all(labels.get(key) for key in ("firststeplanguage", "fourthsteplyrics", "lyrics_online", "search_failed"))


def test_script_rejects_arbitrary_python():
    with pytest.raises(ValueError, match="unknown command"):
        compile_script('label firststeplanguage:\n    wizard "Hi"\n    $ __import__("os")\n')


def test_first_run_does_not_interrupt_existing_users(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "LEGACY_SETTINGS_PATH", tmp_path / "legacy.json")
    monkeypatch.delenv("LRCPLUS_SETTINGS_PATH", raising=False)
    assert needs_onboarding()
    settings.LEGACY_SETTINGS_PATH.write_text("{}", encoding="utf-8")
    assert not needs_onboarding()
    monkeypatch.setenv("LRCPLUS_SETTINGS_PATH", str(settings.SETTINGS_PATH))
    assert needs_onboarding()
    settings.SETTINGS_PATH.write_text("{}", encoding="utf-8")
    assert not needs_onboarding()


def test_dialogue_data_and_space_advancement(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda key, *args: [
        ("Setup Wizard", r"Youkoso\nw3"),
        ("Setup Wizard", r"choose a language\nw3"),
        ("Setup Wizard", r"localize\e"),
    ])
    lines = load_dialogue("firststeplanguage")
    assert len(lines) == 3
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    app.processEvents()
    assert dialog.typing_timer.isActive()
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    assert "Youkoso" in dialog.description.text()
    assert dialog.timer.isActive()
    assert not dialog.next.isEnabled()
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    assert "choose a language" in dialog.description.text()
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    assert "localize" in dialog.description.text()
    assert not dialog.timer.isActive()
    assert dialog.next.isEnabled()
    assert dialog.title.text() == "Setup Wizard"
    dialog.close()


def test_appearance_choice_is_applied(monkeypatch):
    app = QApplication.instance() or QApplication([])
    saved = []
    monkeypatch.setattr(settings, "save_appearance_settings", lambda value: saved.append(dict(value)))
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.appearance_settings = {"theme_mode": "light", "theme_overrides": {}}
    player._apply_main_window_style = lambda: None
    player._apply_appearance_settings = lambda: None
    dialog = OnboardingDialog(player)
    dialog.theme_combo.setCurrentIndex(1)
    assert saved[-1]["theme_mode"] == "dark"
    dialog.choose_preset(dialog.preset_combo.findText("Blue"))
    assert saved[-1]["theme_color"] == "#4477cc"
    assert load_dialogue("thirdstepappearance")[-1][1].endswith(r"\e")
    assert load_dialogue("fourthsteplyrics")[-1][1].endswith(r"\e")
    dialog.close()


def test_movement_commands_run_in_order(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.move(100, 100)
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.dialogue_lines = [("Guide", r"Look here!\wait(0.01)\pos(40,60,linear)\e")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    QTest.qWait(750)
    assert dialog.pos() == player.mapToGlobal(QPoint(40, 60))
    assert dialog.next.isEnabled()
    assert parse_dialogue_line(r"Hello\wait\pos(-10,20)\nw3")[1] == [r"\wait", r"\pos(-10,20)", r"\nw3"]
    dialog.close()


def test_percentage_movement_scales_with_window_size(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.resize(400, 200)
    dialog = OnboardingDialog(player)
    dialog.show()
    monkeypatch.setattr(dialog, "visible_position", lambda position: position)
    dialog.typing_timer.stop()
    script = 'label scene:\n    wizard "Look"\n    $ move(75%,20%,linear)\n'
    dialog.dialogue_lines = compile_script(script)["scene"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.move_animation.endValue() == player.mapToGlobal(QPoint(300, 40))
    dialog.move_animation.stop()
    player.resize(800, 500)
    dialog.script_commands = [r"\pos(75%,20%,linear)"]
    dialog.run_script_command()
    assert dialog.move_animation.endValue() == player.mapToGlobal(QPoint(600, 100))
    dialog.close()


def test_wizard_movement_is_clamped_to_available_screen(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    bounds = player.screen().availableGeometry()
    position = dialog.visible_position(QPoint(bounds.right() + 1000, bounds.bottom() + 1000))
    assert position.x() + dialog.frameGeometry().width() <= bounds.right() + 1
    assert position.y() + dialog.frameGeometry().height() <= bounds.bottom() + 1
    dialog.close()


def test_scripted_move_can_go_beyond_screen_edge(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    point = QPoint(player.screen().availableGeometry().right() + 500, 50)
    relative = player.mapFromGlobal(point)
    dialog.script_commands = [fr"\pos({relative.x()},{relative.y()})"]
    dialog.run_script_command()
    assert dialog.move_animation.endValue() == point
    dialog.close()


def test_named_lyrics_control_opens_menu(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.resize(400, 300)
    player.lyrics_button = QToolButton(player)
    player.lyrics_button.setGeometry(30, 30, 100, 32)
    player.lyrics_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    player.lyrics_menu = QMenu(player.lyrics_button)
    player.lyrics_menu.addAction("Search Lyrics")
    player.lyrics_button.setMenu(player.lyrics_menu)
    opened = []
    player.lyrics_menu.aboutToShow.connect(lambda: opened.append(True))
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.script_commands = [r"\clickcontrol(lyrics_button,menu_disrupted)"]
    QTimer.singleShot(850, player.lyrics_menu.close)
    dialog.run_script_command()
    QTest.qWait(1000)
    assert opened == [True]
    dialog.close()


def test_point_at_aligns_dialogue_caret_with_dropdown(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.resize(900, 500)
    player.lyrics_button = QToolButton(player)
    player.lyrics_button.setGeometry(500, 20, 90, 32)
    player.lyrics_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    app.processEvents()
    dialog.typing_timer.stop()
    dialog.full_text = "Let's see... ^ right here."
    for x in (500, 220):
        player.lyrics_button.move(x, 20)
        dialog.script_commands = [r"\pointat(lyrics_button,in_out_cubic)"]
        dialog.run_script_command()
        end = dialog.move_animation.endValue()
        metrics = dialog.description.fontMetrics()
        current_origin = dialog.description.mapToGlobal(dialog.description.contentsRect().topLeft())
        caret_x = end.x() + current_origin.x() - dialog.pos().x() + metrics.horizontalAdvance("Let's see... ") + metrics.horizontalAdvance("^") // 2
        arrow_x = player.lyrics_button.mapToGlobal(dialog.tool_button_menu_rect(player.lyrics_button).center()).x()
        assert caret_x == arrow_x
        dialog.move_animation.stop()
    dialog.close()


def test_route_buttons_and_checkpoint_return(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", r"Returning.\cr2")] if key == "answer_yes" else [])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.dialogue_lines = [("Guide", r"Pick one.\c2\button(Yes,answer_yes)\button(No,answer_no)")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.route_buttons.count() == 2
    dialog.select_route("answer_yes")
    dialog.finish_typing()
    assert dialog.full_text == "Pick one."
    dialog.finish_typing()
    assert dialog.route_buttons.count() == 2
    dialog.close()


def test_route_can_jump_forward_to_step_checkpoint(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", r"Okay.\wait(2)\cr6")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.step_dialogue_lines = [("Guide", r"Question.\n"), ("Guide", r"\c6furigana\e")]
    dialog.dialogue_lines = dialog.step_dialogue_lines
    dialog.dialogue_index = 1
    dialog.script_busy = False
    dialog.select_route("ok")
    dialog.finish_typing()
    dialog.script_timer.stop()
    dialog.run_script_command()
    assert "furigana" in dialog.full_text
    dialog.finish_typing()
    assert dialog.next.isEnabled()
    dialog.close()


def test_lyrics_online_last_line_reaches_checkpoint(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.step_dialogue_lines = [("Guide", r"Question.\n"), ("Guide", r"\c6furigana\e")]
    dialog.dialogue_lines = [("Guide", r"A response.\wait(2)\cr6")]
    dialog.dialogue_index = len(dialog.dialogue_lines) - 2
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.script_timer.isActive()
    dialog.script_timer.stop()
    dialog.run_script_command()
    assert "furigana" in dialog.full_text
    dialog.close()


def test_inline_command_pauses_then_continues_text(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.dialogue_lines = [("Guide", r"Wait...\wait(0.01) okay!\e")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.description.text() == "Wait..."
    assert dialog.script_timer.isActive()
    QTest.qWait(30)
    assert dialog.typing_timer.isActive()
    dialog.finish_typing()
    assert dialog.description.text() == "Wait... okay!"
    assert dialog.next.isEnabled()
    dialog.close()


def test_reset_restores_starting_relative_position(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    original = QPoint(dialog.start_position)
    dialog.move(player.mapToGlobal(QPoint(10, 20)))
    dialog.typing_timer.stop()
    dialog.dialogue_lines = [("Guide", r"Returning.\res(linear)\e")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.move_animation.easingCurve().type() == QEasingCurve.Type.Linear
    QTest.qWait(750)
    assert dialog.pos() == player.mapToGlobal(original)
    assert dialog.next.isEnabled()
    dialog.close()


def test_script_click_uses_main_window_coordinates(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.resize(400, 200)
    player.library_folders = []
    button = QPushButton("Target", player)
    button.setGeometry(180, 80, 40, 40)
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    player.show()
    dialog = OnboardingDialog(player)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.dialogue_lines = [("Guide", r"Watch.\click(50%,50%)\e")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    QTest.qWait(900)
    assert clicks == [True]
    assert dialog.fake_cursor.isVisible()
    assert dialog.next.isEnabled()
    dialog.dialogue_lines = [("Guide", r"Done.\curhide\e")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert not dialog.fake_cursor.isVisible()
    dialog.close()


def test_script_click_can_activate_open_menu(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.resize(400, 300)
    player.library_folders = []
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    menu = QMenu(player)
    action = menu.addAction("Search Lyrics")
    triggered = []
    action.triggered.connect(lambda: triggered.append(True))
    menu.popup(player.mapToGlobal(QPoint(30, 30)))
    app.processEvents()
    point = player.mapFromGlobal(menu.mapToGlobal(menu.actionGeometry(action).center()))
    dialog.click_position = point
    dialog.perform_script_click()
    app.processEvents()
    assert triggered == [True]
    dialog.close()


def test_watched_menu_closing_switches_to_recovery_route(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", "Menu closed.")] if key == "menu_disrupted" else [])
    player = QWidget()
    player.library_folders = []
    player.show()
    player.lyrics_menu = QMenu(player)
    player.lyrics_menu.addAction("Search Lyrics")
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    app.installEventFilter(dialog)
    try:
        player.lyrics_menu.popup(player.mapToGlobal(QPoint(30, 30)))
        app.processEvents()
        dialog.script_commands = [r"\watchmenu(lyrics_menu,menu_disrupted)"]
        dialog.run_script_command()
        player.lyrics_menu.hide()
        QTest.qWait(30)
        assert dialog.full_text == "Menu closed."
    finally:
        app.removeEventFilter(dialog)
        dialog.close()


def test_named_menu_click_activates_expected_action(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.show()
    player.lyrics_menu = QMenu(player)
    action = player.lyrics_menu.addAction("Search Lyrics")
    action.setObjectName("search_lyrics")
    triggered = []
    action.triggered.connect(lambda: triggered.append(True))
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    player.lyrics_menu.popup(player.mapToGlobal(QPoint(30, 30)))
    app.processEvents()
    dialog.script_commands = [r"\clickmenu(lyrics_menu,search_lyrics,menu_disrupted)"]
    dialog.run_script_command()
    QTest.qWait(900)
    assert triggered == [True]
    dialog.close()


def test_standalone_jump_runs_after_pause(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", r"Arrived.\e")] if key == "next_route" else [])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.dialogue_lines = compile_script('label start:\n    wizard "Wait.Okay"\n    $ pause(0.01)\n    jump next_route\n')["start"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    QTest.qWait(30)
    assert dialog.full_text == "Arrived."
    dialog.close()


def test_outside_click_toggle_persists_across_steps(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.typing_timer.stop()
    dialog.script_commands = [r"\toggleoutsideclick"]
    dialog.run_script_command()
    assert dialog.outside_click_enabled
    dialog.set_page(1)
    assert dialog.outside_click_enabled
    dialog.script_commands = [r"\toggleoutsideclick"]
    dialog.run_script_command()
    assert not dialog.outside_click_enabled
    QTimer.singleShot(0, dialog.accept)
    dialog.run()


def test_outside_click_explicit_true_and_false_are_idempotent(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.typing_timer.stop()
    script = compile_script('label example:\n    wizard "Hi"\n    $ toggle_outside_click(true)\n    $ toggle_outside_click(true)\n')
    _, commands = parse_dialogue_line(script["example"][0][1])
    dialog.script_commands = commands
    dialog.tokens = []
    dialog.run_script_command()
    assert dialog.outside_click_enabled
    dialog.script_commands = [r"\toggleoutsideclick(false)", r"\toggleoutsideclick(false)"]
    dialog.run_script_command()
    assert not dialog.outside_click_enabled
    dialog.close()


def test_idle_wait_restarts_after_user_activity(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.dialogue_lines = compile_script('label start:\n    wizard "Waiting"\n    $ idle(0.3)\n    wizard "Back"\n    $ end\n')["start"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    QTest.qWait(130)
    dialog.record_user_activity()
    QTest.qWait(200)
    assert dialog.full_text == "Waiting"
    QTest.qWait(150)
    assert dialog.full_text == "Back"
    assert dialog.last_event == "wizard_idle"
    dialog.close()


@pytest.mark.parametrize("event_name", [None, "wizard_moved", "lyrics_search_started"])
def test_wait_event_races_idle_and_other_events(monkeypatch, event_name):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    script = '''label start:
    wizard "Waiting"
    $ wait_event(wizard_moved,lyrics_search_started,idle(0.2))
    wizard "Continued"
    $ end
'''
    dialog.dialogue_lines = compile_script(script)["start"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.idle_timer.isActive()
    if event_name:
        dialog.events.occurred.emit(event_name)
    else:
        QTest.qWait(250)
    assert dialog.last_event == (event_name or "wizard_idle")
    assert dialog.full_text == "Continued"
    assert not dialog.idle_timer.isActive()
    dialog.close()


def test_user_window_movement_emits_event_but_scripted_motion_does_not(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    QTest.qWait(20)
    dialog.pending_events.clear()
    pressed = [False]
    monkeypatch.setattr(dialog, "user_drag_active", lambda: pressed[0])
    dialog.move(dialog.pos() + QPoint(10, 10))
    app.processEvents()
    QTest.qWait(250)
    assert "wizard_moved" not in dialog.pending_events
    pressed[0] = True
    for _ in range(5):
        dialog.move(dialog.pos() + QPoint(4, 4))
        app.processEvents()
    pressed[0] = False
    QTest.qWait(250)
    assert "wizard_moved" in dialog.pending_events
    assert dialog.pending_events.count("wizard_moved") == 1
    dialog.pending_events.clear()
    dialog.move_animation.setStartValue(dialog.pos())
    dialog.move_animation.setEndValue(dialog.pos() + QPoint(20, 20))
    dialog.start_wizard_motion()
    QTest.qWait(750)
    assert "wizard_moved" not in dialog.pending_events
    dialog.close()


def test_moving_window_during_wait_runs_reaction(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda name: [("Guide", r"Off me!\e")] if name == "window_moved" else [])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    QTest.qWait(20)
    pressed = [True]
    monkeypatch.setattr(dialog, "user_drag_active", lambda: pressed[0])
    dialog.typing_timer.stop()
    dialog.pending_events.clear()
    dialog.dialogue_lines = compile_script('''label scene:
    wizard "Search now."
    $ wait_event(lyrics_search_started,wizard_moved,idle(20))
    if event == "wizard_moved":
        jump window_moved
''')["scene"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    dialog.move(dialog.pos() + QPoint(25, 15))
    app.processEvents()
    pressed[0] = False
    QTest.qWait(250)
    assert dialog.full_text == "Off me!"
    dialog.close()


def test_scripted_move_does_not_complete_movement_wait(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    QTest.qWait(20)
    dialog.typing_timer.stop()
    monkeypatch.setattr(dialog, "user_drag_active", lambda: False)
    dialog.pending_events.clear()
    dialog.dialogue_lines = compile_script('''label scene:
    wizard "Waiting."
    $ move(25,25)
    $ wait_event(wizard_moved)
    wizard "Moved."
''')["scene"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    QTest.qWait(1000)
    assert dialog.waiting_events == {"wizard_moved"}
    assert "wizard_moved" not in dialog.pending_events
    assert dialog.full_text == "Waiting."
    dialog.close()


@pytest.mark.parametrize("event_name, expected", [
    ("wizard_moved", "Hey, what are you doing? Off me!"),
    ("lyrics_search_started", "Next line."),
])
def test_conditional_dialogue_runs_inline_and_continues(monkeypatch, event_name, expected):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    script = '''label start:
    wizard "What now?"
    $ wait_event(wizard_moved,lyrics_search_started)
    if event == "wizard_moved":
        wizard "Hey, what are you doing? Off me!"
    wizard "Next line."
    $ end
'''
    dialog.dialogue_lines = compile_script(script)["start"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    dialog.events.occurred.emit(event_name)
    assert dialog.full_text == expected
    if event_name == "wizard_moved":
        dialog.finish_typing()
        dialog.advance_dialogue()
        assert dialog.full_text == "Next line."
    dialog.close()


def test_checkpoint_can_be_declared_on_its_own_line_and_found_in_another_step(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    labels = compile_script('''label firststeplanguage:
    checkpoint 42
    wizard "Found it."
    $ end
label fourthsteplyrics:
    wizard "Go back."
    $ return checkpoint(42)
''')
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    monkeypatch.setattr("ui.onboarding.load_script", lambda: labels)
    dialog.set_page(LYRICS_PAGE)
    dialog.step_dialogue_lines = labels["fourthsteplyrics"]
    dialog.dialogue_lines = labels["fourthsteplyrics"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.page == 0
    assert dialog.full_text == "Found it."
    dialog.close()


def test_debug_skips_event_wait_and_jumps_to_step(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.show_debug()
    dialog.typing_timer.stop()
    dialog.dialogue_lines = [("Guide", r"Waiting.\waitevent(lyrics_search_started)"), ("Guide", r"Next line.\e")]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.finish_typing()
    assert dialog.waiting_events == {"lyrics_search_started"}
    dialog.debug_skip_line()
    assert not dialog.waiting_events
    assert dialog.full_text == "Next line."
    dialog.debug_step.setCurrentIndex(LYRICS_PAGE)
    dialog.debug_jump_step()
    assert dialog.page == LYRICS_PAGE
    assert dialog.dialogue_index == 0
    dialog.debug_step.setCurrentIndex(FINISH_PAGE)
    dialog.debug_jump_step()
    assert dialog.page == FINISH_PAGE and dialog.next.isEnabled()
    dialog.close()


def test_search_events_are_buffered_and_choose_matching_route(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", r"Search failed.\e")] if key == "search_failed" else [])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.typing_timer.stop()
    dialog.dialogue_lines = [
        ("Guide", r"Search now.\waitevent(lyrics_search_started)"),
        ("Guide", r"Checking.\waitevent(lyrics_search_results,lyrics_search_failed,lyrics_search_empty)\if(lyrics_search_failed,search_failed)"),
    ]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()
    dialog.events.occurred.emit("lyrics_search_started")
    dialog.finish_typing()
    assert dialog.full_text == "Checking."
    dialog.events.occurred.emit("lyrics_search_failed")
    dialog.finish_typing()
    assert dialog.full_text == "Search failed."
    dialog.finish_typing()
    assert dialog.next.isEnabled()
    dialog.close()


@pytest.mark.parametrize("start_first", [False, True])
def test_closing_search_window_exits_wait(monkeypatch, start_first):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.typing_timer.stop()
    dialog.select_route("lyrics_retry")
    dialog.finish_typing()
    if start_first:
        dialog.events.occurred.emit("lyrics_search_started")
        assert dialog.dialogue_index == 1
    dialog.events.occurred.emit("lyrics_search_closed")
    if start_first:
        dialog.finish_typing()
    assert dialog.full_text == parse_dialogue_line(load_route("search_closed")[0][1])[0]
    dialog.close()


@pytest.mark.parametrize("cue", ["", r"\wait(20)", r"\idle(20)",
                                     r"\waitevent(lyrics_search_started,idle(20))", r"\pos(20,20)"])
def test_route_switch_cancels_previous_scene(monkeypatch, qapp, cue):
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", r"New scene.\n")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    try:
        dialog.show()
        dialog.typing_timer.stop()
        dialog.dialogue_lines = [("Guide", "Old scene." + cue)]
        dialog.dialogue_index = -1
        dialog.advance_dialogue()
        if cue:
            dialog.finish_typing()
        dialog.timer.start(20_000)
        dialog.event_auto_advance = True

        dialog.select_route("replacement")

        assert dialog.full_text == "New scene."
        assert dialog.typing_timer.isActive()
        assert not dialog.next.isEnabled()
        assert dialog.space.isEnabled()
        assert not dialog.timer.isActive()
        assert not dialog.script_timer.isActive()
        assert not dialog.idle_timer.isActive()
        assert not dialog.idle_cursor_timer.isActive()
        assert not dialog.waiting_events
        assert not dialog.event_auto_advance
        assert dialog.move_animation.state() == dialog.move_animation.State.Stopped
        dialog.events.occurred.emit("lyrics_search_started")
        dialog.finish_typing()
        assert dialog.description.text() == "New scene."
        assert dialog.dialogue_index == 0
    finally:
        dialog.close()


def test_new_route_invalidates_queued_menu_recovery(monkeypatch, qapp):
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", key + r"\n")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    try:
        dialog.show()
        dialog.menu_recovery_route = "old_recovery"
        dialog.recover_from_disruption()
        assert dialog.disruption_pending
        dialog.select_route("replacement")
        qapp.processEvents()
        assert dialog.full_text == "replacement"
        assert not dialog.disruption_pending
    finally:
        dialog.close()


@pytest.mark.parametrize("transition", ["route", "page", "close"])
def test_scene_transition_invalidates_queued_menu_action(monkeypatch, qapp, transition):
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", r"Replacement.\n")])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    menu = QMenu(player)
    action = menu.addAction("Search Lyrics")
    triggered = []
    action.triggered.connect(lambda: triggered.append(True))
    try:
        dialog.show()
        menu.popup(player.mapToGlobal(QPoint(30, 30)))
        qapp.processEvents()
        dialog.pending_menu_action = action
        dialog.watched_menu = menu
        dialog.perform_script_click()
        if transition == "route":
            dialog.select_route("replacement")
        elif transition == "page":
            dialog.set_page(2)
        else:
            dialog.close()
        qapp.processEvents()
        assert triggered == []
        assert not dialog.cursor_hide_timer.isActive()
    finally:
        dialog.close()


def test_long_command_sequence_yields_and_completes_without_recursion(monkeypatch, qapp):
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    try:
        dialog.typing_timer.stop()
        dialog.dialogue_lines = [("Guide", "Hello" + r"\toggleoutsideclick" * 2001 + r"\e")]
        dialog.dialogue_index = -1
        dialog.advance_dialogue()
        dialog.finish_typing()
        assert dialog.script_timer.isActive()
        assert dialog.script_busy
        for _ in range(20):
            qapp.processEvents()
            if not dialog.script_timer.isActive():
                break
        assert not dialog.script_timer.isActive()
        assert not dialog.script_busy
        assert dialog.next.isEnabled()
        assert dialog.outside_click_enabled
    finally:
        dialog.close()


def test_immediate_jump_loop_yields_and_can_be_cancelled(monkeypatch, qapp):
    monkeypatch.setattr(settings, "save_all_settings", lambda payload: None)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [
        ("Guide", r"\jump(loop)" if key == "loop" else r"Recovered.\n")
    ])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    try:
        dialog.select_route("loop")
        assert dialog.script_timer.isActive()
        QTimer.singleShot(0, lambda: dialog.select_route("replacement"))
        for _ in range(5):
            qapp.processEvents()
            if dialog.full_text == "Recovered.":
                break
        assert dialog.full_text == "Recovered."
        assert not dialog.script_timer.isActive()
        dialog.finish_typing()
        assert dialog.description.text() == "Recovered."
    finally:
        dialog.close()
