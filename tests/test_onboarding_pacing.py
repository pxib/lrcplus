"""Pacing preferences must preserve dialogue and application action ordering."""

import pytest
from PySide6.QtCore import QPoint, QPropertyAnimation, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

from core import settings
from ui.onboarding import OnboardingDialog


@pytest.fixture
def wizard(qapp, monkeypatch):
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda key: [("Guide", r"Hello there.\n")])
    player = QWidget()
    player.library_folders = []
    player.resize(900, 600)
    player.show()
    dialog = OnboardingDialog(player)
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.close()
    player.close()
    qapp.processEvents()


def play(dialog, text):
    dialog._cancel_dialogue_activity()
    dialog.dialogue_lines = [("Guide", text)]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()


@pytest.mark.parametrize("value", [None, [], {"text_speed": []}, {"text_speed": "turbo"},
                                       {"reduced_motion": "false"}, {"sound_enabled": "false"}])
def test_invalid_pacing_settings_use_defaults(value):
    assert settings.get_onboarding_pacing({"onboarding_pacing": value}) == {
        "text_speed": "normal", "reduced_motion": False, "auto_read": True, "sound_enabled": True,
    }


def test_reading_pacing_does_not_create_first_run_settings():
    assert not settings.SETTINGS_PATH.exists()
    assert settings.get_onboarding_pacing()["text_speed"] == "normal"
    assert not settings.SETTINGS_PATH.exists()


@pytest.mark.parametrize("speed, interval", [("slow", 50), ("normal", 25), ("fast", 10)])
def test_text_speed_updates_active_typing(wizard, speed, interval):
    wizard.type_character()
    before = wizard.description.text()
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData(speed))
    assert wizard.typing_timer.isActive()
    assert wizard.typing_timer.interval() == interval
    assert wizard.description.text() == before
    wizard.finish_typing()
    assert wizard.description.text() == "Hello there."


def test_instant_text_preserves_inline_pause_and_event_wait(wizard):
    play(wizard, r"Before.\wait(0.01) After.\waitevent(lyrics_search_started) Done.\e")
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    assert wizard.description.text() == "Before."
    assert wizard.script_timer.isActive()
    assert not wizard.typing_timer.isActive()
    QTest.qWait(30)
    assert wizard.description.text() == "Before. After."
    assert wizard.waiting_events == {"lyrics_search_started"}
    assert not wizard.next.isEnabled()
    wizard.events.occurred.emit("lyrics_search_started")
    assert wizard.description.text() == "Before. After. Done."
    assert wizard.next.isEnabled()


def test_instant_text_preserves_choice_and_manual_advancement(wizard, monkeypatch):
    wizard.auto_read_check.setChecked(False)
    monkeypatch.setattr("ui.onboarding.load_route", lambda key: [("Guide", r"Selected.\e")])
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    play(wizard, r"Choose.\button(Continue,chosen)")
    assert wizard.description.text() == "Choose."
    assert wizard.route_buttons.count() == 1
    assert wizard.script_busy
    wizard.advance_dialogue()
    assert wizard.description.text() == "Choose."
    wizard.route_buttons.itemAt(0).widget().click()
    assert wizard.description.text() == "Selected."
    play(wizard, r"Wait for me.\n")
    assert wizard.description.text() == "Wait for me."
    assert not wizard.timer.isActive()
    assert wizard.dialogue_lines


@pytest.mark.parametrize("command", [r"\pos(40,60)", r"\pointat(lyrics_button)", r"\res"])
def test_reduced_motion_places_guide_without_animation(wizard, command):
    wizard.player.lyrics_button = QToolButton(wizard.player)
    wizard.player.lyrics_button.setGeometry(300, 20, 80, 30)
    wizard.player.lyrics_button.show()
    wizard.reduced_motion_check.setChecked(True)
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    play(wizard, "Look." + command + r"\e")
    assert wizard.move_animation.state() == QPropertyAnimation.State.Stopped
    assert wizard.pos() == wizard.move_animation.endValue()
    assert wizard.next.isEnabled()
    assert "wizard_moved" not in wizard.pending_events


def test_reduced_motion_still_clicks_target_without_cursor(wizard):
    target = QPushButton("Target", wizard.player)
    target.setGeometry(50, 50, 80, 30)
    target.show()
    clicks = []
    target.clicked.connect(lambda: clicks.append(True))
    wizard.reduced_motion_check.setChecked(True)
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    play(wizard, r"Watch.\click(60,60) Done.\e")
    assert not wizard.fake_cursor.isVisible()
    assert wizard.cursor_animation.state() == QPropertyAnimation.State.Stopped
    QTest.qWait(220)
    assert clicks == [True]
    assert wizard.description.text() == "Watch. Done."
    assert not wizard.fake_cursor.isVisible()


def test_enabling_reduced_motion_finishes_current_move_once(wizard):
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    play(wizard, r"Moving.\pos(40,60) Arrived.\e")
    assert wizard.move_animation.state() == QPropertyAnimation.State.Running
    wizard.reduced_motion_check.setChecked(True)
    assert wizard.pos() == wizard.player.mapToGlobal(QPoint(40, 60))
    assert wizard.description.text() == "Moving. Arrived."
    assert wizard.next.isEnabled()


def test_pacing_preferences_survive_reopening(wizard):
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    wizard.reduced_motion_check.setChecked(True)
    wizard.close()
    assert settings.get_onboarding_pacing() == {"text_speed": "instant", "reduced_motion": True, "auto_read": True, "sound_enabled": True}
    reopened = OnboardingDialog(wizard.player)
    try:
        assert reopened.text_speed_combo.currentData() == "instant"
        assert reopened.reduced_motion_check.isChecked()
        assert reopened.description.text() == "Hello there."
        assert not reopened.typing_timer.isActive()
    finally:
        reopened.close()


def test_space_toggles_motion_control_without_advancing_dialogue(wizard, qapp):
    wizard.activateWindow()
    wizard.reduced_motion_check.setFocus()
    qapp.processEvents()
    QTest.keyClick(wizard.reduced_motion_check, Qt.Key.Key_Space)
    assert wizard.reduced_motion_check.isChecked()
    assert wizard.typing_timer.isActive()


def test_enabling_reduced_motion_during_cursor_animation_clicks_once(wizard):
    target = QPushButton("Target", wizard.player)
    target.setGeometry(50, 50, 80, 30)
    target.show()
    clicks = []
    target.clicked.connect(lambda: clicks.append(True))
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    play(wizard, r"Watch.\click(60,60) Done.\e")
    assert wizard.cursor_animation.state() == QPropertyAnimation.State.Running
    wizard.reduced_motion_check.setChecked(True)
    QTest.qWait(220)
    assert clicks == [True]
    assert wizard.description.text() == "Watch. Done."
    assert not wizard.fake_cursor.isVisible()


def test_window_resize_animates_and_retargets_from_current_height(wizard, qapp):
    QTest.qWait(220)
    start = wizard.height()
    play(wizard, "This explanation needs a little more room. " * 8 + r"\n")
    qapp.processEvents()
    target = wizard._height_target
    assert target > start
    assert wizard.size_animation.state() == QPropertyAnimation.State.Running
    wizard.size_animation.setCurrentTime(60)
    qapp.processEvents()
    assert start < wizard.height() < target
    current = wizard.height()
    play(wizard, r"Short again.\n")
    qapp.processEvents()
    assert wizard.size_animation.startValue() == current
    assert wizard._height_target < current
    for _ in range(20):
        if wizard.size_animation.state() == QPropertyAnimation.State.Stopped:
            break
        QTest.qWait(25)
    assert wizard.height() == wizard._height_target
    assert wizard.size_animation.state() == QPropertyAnimation.State.Stopped


def test_reduced_motion_snaps_current_and_future_window_resizes(wizard, qapp):
    play(wizard, "This explanation needs a little more room. " * 8 + r"\n")
    qapp.processEvents()
    assert wizard.size_animation.state() == QPropertyAnimation.State.Running
    wizard.reduced_motion_check.setChecked(True)
    assert wizard.size_animation.state() == QPropertyAnimation.State.Stopped
    assert wizard.height() == wizard._height_target
    play(wizard, r"Short again.\n")
    qapp.processEvents()
    assert wizard.height() == wizard._height_target
    assert wizard.size_animation.state() == QPropertyAnimation.State.Stopped


def test_resizing_keeps_text_controls_and_native_limits_steady(wizard, qapp):
    QTest.qWait(250)
    play(wizard, "This explanation needs a little more room. " * 8 + r"\n")
    qapp.processEvents()
    assert wizard.description.alignment() & Qt.AlignmentFlag.AlignTop
    widgets = (wizard.title, wizard.description, wizard.text_speed_combo, wizard.next)
    positions = [widget.geometry() for widget in widgets]
    limits = (wizard.minimumHeight(), wizard.maximumHeight())
    heights = []
    for _ in range(4):
        QTest.qWait(30)
        heights.append(wizard.height())
        assert [widget.geometry() for widget in widgets] == positions
        assert (wizard.minimumHeight(), wizard.maximumHeight()) == limits
    assert len(set(heights)) > 1
