"""Leaving setup is deliberate; canceling the confirmation preserves execution."""

import pytest
from PySide6.QtCore import QPoint, QPropertyAnimation, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from core import settings
from ui.onboarding import FINISH_PAGE, LYRICS_PAGE, READING_PAGE, OnboardingDialog


@pytest.fixture
def wizard(qapp, monkeypatch):
    monkeypatch.setattr("ui.onboarding.load_dialogue_data", lambda key: [
        ("Guide", r"Hello there.\n"), ("Guide", r"Next line.\e"),
    ])
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.auto_read_check.setChecked(False)
    qapp.processEvents()
    yield dialog
    if not dialog._dialogue_closed:
        dialog.done(QDialog.DialogCode.Rejected)
    qapp.processEvents()


def trigger(dialog, action):
    if action == "close":
        return dialog.close()
    if action == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog.skip.click()


@pytest.mark.parametrize("action", ["close", "escape", "skip"])
def test_cancel_exit_preserves_dialogue_and_does_not_save(wizard, monkeypatch, action):
    asked = []

    def keep():
        asked.append(True)
        assert wizard._confirming_exit
        assert not wizard.typing_timer.isActive()
        return False

    monkeypatch.setattr(wizard, "_ask_exit_confirmation", keep)
    index = wizard.dialogue_index
    trigger(wizard, action)
    assert asked == [True]
    assert wizard.isVisible()
    assert not wizard._dialogue_closed
    assert wizard.dialogue_index == index
    assert wizard.typing_timer.isActive()
    assert wizard.typing_timer.interval() == settings.ONBOARDING_TEXT_INTERVALS["normal"]
    assert not settings.SETTINGS_PATH.exists()


@pytest.mark.parametrize("action", ["close", "escape", "skip"])
def test_confirm_exit_finishes_once_and_cleans_up(wizard, monkeypatch, action):
    monkeypatch.setattr(wizard, "_ask_exit_confirmation", lambda: True)
    finished = []
    wizard.finished.connect(finished.append)
    trigger(wizard, action)
    assert not wizard.isVisible()
    assert wizard._dialogue_closed
    assert wizard.sounds._closed
    assert not wizard.typing_timer.isActive()
    assert not wizard.timer.isActive()
    assert not wizard.waiting_events
    assert wizard.move_animation.state() == QPropertyAnimation.State.Stopped
    assert wizard.size_animation.state() == QPropertyAnimation.State.Stopped
    assert finished == [QDialog.DialogCode.Rejected]
    assert settings.load_all_settings()["onboarding_complete"] is True


def test_finish_does_not_ask_for_exit_confirmation(wizard, monkeypatch):
    def unexpected_prompt():
        pytest.fail("Finish must complete setup directly")

    monkeypatch.setattr(wizard, "_ask_exit_confirmation", unexpected_prompt)
    wizard.set_page(FINISH_PAGE)
    wizard.next.click()
    assert wizard._dialogue_closed
    assert wizard.result() == QDialog.DialogCode.Accepted


def test_do_this_later_does_not_ask_to_leave_setup(wizard, monkeypatch):
    def unexpected_prompt():
        pytest.fail("Skipping a step does not leave setup")

    monkeypatch.setattr(wizard, "_ask_exit_confirmation", unexpected_prompt)
    wizard.set_page(READING_PAGE)
    wizard.reading_preferences.font_size.setValue(60)
    wizard.skip_reading.click()
    assert wizard.page == LYRICS_PAGE
    assert not wizard._dialogue_closed
    assert not settings.SETTINGS_PATH.exists()


def test_real_confirmation_defaults_to_keep_and_explains_reading_draft(wizard, qapp):
    wizard.set_page(READING_PAGE)
    wizard.reading_preferences.font_size.setValue(60)
    observed = {}

    def inspect_and_keep():
        box = qapp.activeModalWidget()
        observed["question"] = isinstance(box, QMessageBox)
        observed["default"] = box.defaultButton().text()
        observed["escape"] = box.escapeButton().text()
        observed["message"] = box.informativeText()
        box.defaultButton().click()

    QTimer.singleShot(0, inspect_and_keep)
    assert not wizard.request_exit()
    assert observed["question"]
    assert observed["default"] == observed["escape"] == "Keep setting up"
    assert "have not been saved" in observed["message"]
    assert wizard.isVisible()
    assert wizard.reading_preferences.font_size.value() == 60
    assert not settings.SETTINGS_PATH.exists()


def test_real_leave_button_exits_setup(wizard, qapp):
    def choose_leave():
        box = qapp.activeModalWidget()
        next(button for button in box.buttons() if button.text() == "Leave setup").click()

    QTimer.singleShot(0, choose_leave)
    assert wizard.request_exit()
    assert wizard._dialogue_closed
    assert not wizard.isVisible()


def test_search_event_waits_for_confirmation_to_be_dismissed(wizard, monkeypatch):
    wizard.typing_timer.stop()
    wizard.dialogue_lines = [("Guide", r"Waiting.\waitevent(lyrics_search_started)"), ("Guide", r"Arrived.\e")]
    wizard.dialogue_index = -1
    wizard.advance_dialogue()
    wizard.finish_typing()

    def keep():
        wizard.events.occurred.emit("lyrics_search_started")
        assert wizard.full_text == "Waiting."
        assert wizard.waiting_events == {"lyrics_search_started"}
        return False

    monkeypatch.setattr(wizard, "_ask_exit_confirmation", keep)
    wizard.request_exit()
    assert wizard.full_text == "Arrived."
    assert not wizard.waiting_events


@pytest.mark.parametrize("leave", [False, True])
def test_queued_script_action_is_held_until_keep_and_discarded_on_exit(wizard, monkeypatch, qapp, leave):
    calls = []
    wizard._defer_activity(lambda: calls.append(True))

    def decide():
        qapp.processEvents()
        assert calls == []
        assert not wizard.request_exit()  # A second close cannot create another prompt.
        return leave

    monkeypatch.setattr(wizard, "_ask_exit_confirmation", decide)
    wizard.request_exit()
    qapp.processEvents()
    assert calls == ([] if leave else [True])


def test_pause_wait_resumes_after_cancel_without_advancing_behind_prompt(wizard, monkeypatch):
    wizard.typing_timer.stop()
    wizard.dialogue_lines = [("Guide", r"Before.\wait(0.04) After.\n")]
    wizard.dialogue_index = -1
    wizard.advance_dialogue()
    wizard.finish_typing()

    def keep():
        QTest.qWait(70)
        assert wizard.description.text() == "Before."
        assert not wizard.script_timer.isActive()
        assert not wizard.dialogue_indicator.isVisible()
        return False

    monkeypatch.setattr(wizard, "_ask_exit_confirmation", keep)
    wizard.request_exit()
    assert wizard.script_timer.isActive()
    QTest.qWait(70)
    assert wizard.typing_timer.isActive()
    wizard.finish_typing()
    assert wizard.description.text() == "Before. After."


def test_active_animation_is_paused_and_resumed_on_keep(wizard, monkeypatch):
    wizard.typing_timer.stop()
    wizard.move_animation.setStartValue(wizard.pos())
    wizard.move_animation.setEndValue(wizard.pos() + QPoint(100, 0))
    wizard.start_wizard_motion()
    wizard.move_animation.setCurrentTime(100)
    position = wizard.pos()
    time = wizard.move_animation.currentTime()

    def keep():
        assert wizard.move_animation.state() == QPropertyAnimation.State.Paused
        QTest.qWait(50)
        assert wizard.pos() == position
        assert wizard.move_animation.currentTime() == time
        return False

    monkeypatch.setattr(wizard, "_ask_exit_confirmation", keep)
    wizard.request_exit()
    assert wizard.move_animation.state() == QPropertyAnimation.State.Running
    assert wizard.move_animation.currentTime() == time
