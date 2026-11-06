from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from ui.main_window import Player
from ui.player_dialogs import PitchChangerDialog
from ui.player_updates import PlayerUpdatesMixin


def flush_deletes():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("fail", [False, True])
def test_pitch_dialog_releases_its_controls_after_every_open(qapp, monkeypatch, fail):
    parent = QWidget()
    parent.pitch_semitones = 0
    parent.pitch_settings = {"mode": "Semitones"}
    parent.playback_speed = 1
    parent.pitch_linked_to_speed = False
    parent.handle_pitch_change = lambda *args: None
    parent.handle_speed_change = lambda *args: None
    parent.handle_pitch_link_change = lambda *args: None

    def dismiss(dialog):
        if fail:
            raise RuntimeError("dialog failed")
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", dismiss)
    try:
        for _ in range(3):
            if fail:
                with pytest.raises(RuntimeError):
                    Player.open_pitch_options(parent)
            else:
                Player.open_pitch_options(parent)
            flush_deletes()
            assert not parent.findChildren(QDialog)
    finally:
        parent.deleteLater()
        flush_deletes()


def test_closing_pitch_dialog_commits_pending_drag_before_disposal(qapp):
    changes = []
    dialog = PitchChangerDialog(0, "Semitones", lambda *args: changes.append(args))
    dialog.slider.setSliderDown(True)
    dialog.slider.setValue(350)
    assert dialog._pitch_update_timer.isActive()
    dialog.reject()
    assert changes[-1] == ("Semitones", 3.5)
    assert not dialog._pitch_update_timer.isActive()
    dialog.deleteLater()
    flush_deletes()


@pytest.mark.parametrize("method,title", [
    ("show_timing_shift_dialog", "Shift Lyric Timing"),
    ("show_timing_validation", "Validate Lyric Timing"),
])
def test_timing_dialogs_do_not_accumulate(editor, monkeypatch, method, title):
    monkeypatch.setattr("ui.lyrics_timing_tools.validate_timing", lambda *args, **kwargs: [
        SimpleNamespace(line_index=0, message="Timing issue"),
    ])

    def dismiss(dialog):
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", dismiss)
    for _ in range(3):
        getattr(editor, method)()
        flush_deletes()
        assert not [child for child in editor.findChildren(QDialog) if child.windowTitle() == title]


class UpdateWindow(PlayerUpdatesMixin, QWidget):
    def __init__(self):
        super().__init__()
        self._update_available_dialog = None
        self._update_progress_dialog = None
        self._update_download_worker = None
        self._close_after_update_cancel = False


def release():
    return SimpleNamespace(tag="v99.0.0", body="Release notes", html_url="https://example.com",
                           published_at="2026-11-01", prerelease=False,
                           asset=SimpleNamespace(name="update.zip", size=1))


def test_update_notices_are_deleted_after_dismissal(qapp):
    parent = UpdateWindow()
    try:
        for _ in range(3):
            parent._update_check_succeeded(None, release(), True)
            parent._update_available_dialog.reject()
            flush_deletes()
            assert parent._update_available_dialog is None
            assert not parent.findChildren(QDialog)
    finally:
        parent.deleteLater()
        flush_deletes()


def test_hidden_update_progress_is_released_when_worker_finishes(qapp, monkeypatch):
    class Worker(QObject):
        progress = Signal(int, int)
        stage_changed = Signal(str)
        prepared = Signal(str)
        failed = Signal(str)
        cancelled = Signal()
        finished = Signal()

        def __init__(self, release, transaction, parent):
            super().__init__(parent)

        def start(self):
            pass

    monkeypatch.setattr("ui.player_updates.UpdateDownloadWorker", Worker)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    parent = UpdateWindow()
    try:
        parent._begin_update(release())
        worker = parent._update_download_worker
        progress = parent._update_progress_dialog
        progress.close()
        worker.progress.emit(1, 2)  # Closing the view must not destroy active worker slots.
        assert parent._update_progress_dialog is progress
        worker.finished.emit()
        flush_deletes()
        assert parent._update_progress_dialog is None
        assert not parent.findChildren(QDialog)
    finally:
        parent.deleteLater()
        flush_deletes()


@pytest.mark.parametrize("running", [False, True])
def test_flashcard_dialog_waits_for_analysis_before_disposal(qapp, monkeypatch, running):
    from plugins.community.anki_flashcards import plugin as module

    class Worker(QObject):
        finished = Signal()

        def isRunning(self):
            return self.running

    dialog = QDialog()
    worker = Worker(dialog)
    worker.running = running
    dialog.worker = worker
    destroyed = []
    dialog.destroyed.connect(lambda: destroyed.append(True))
    monkeypatch.setattr(module, "FlashcardDialog", lambda *args: dialog)
    monkeypatch.setattr(QDialog, "exec", lambda self: self.reject())
    plugin = module.AnkiFlashcardsPlugin()
    plugin.context = SimpleNamespace(api=SimpleNamespace(
        get_current_lyrics=lambda: [{"text": "Line"}],
        get_current_track=lambda: "song.mp3",
    ))
    plugin.open_flashcards()
    flush_deletes()
    assert bool(destroyed) is not running
    if running:
        worker.running = False
        worker.finished.emit()
        flush_deletes()
        assert destroyed == [True]


def test_karaoke_screen_releases_parent_owned_stages_and_timers(qapp):
    from PySide6.QtCore import QTimer
    from plugins.core.karaoke_screen.plugin import KaraokeScreenPlugin
    from plugins.core.karaoke_screen.screen import KaraokeScreen

    parent = QWidget()
    plugin = KaraokeScreenPlugin()
    plugin._stop_microphone = lambda: None
    plugin._stop_square_wave_stream = lambda: None
    try:
        for _ in range(3):
            plugin.screen = KaraokeScreen(parent)
            plugin.screen_timer = QTimer(plugin.screen)
            plugin.pitch_timer = QTimer(plugin.screen)
            plugin.screen_timer.start(17)
            plugin.pitch_timer.start(16)
            plugin.screen.finished.connect(plugin._screen_closed)
            plugin.screen.reject()
            flush_deletes()
            assert plugin.screen is None
            assert not parent.findChildren(KaraokeScreen)
            assert not parent.findChildren(QTimer)
    finally:
        parent.deleteLater()
        flush_deletes()


@pytest.mark.parametrize("open_manager", [False, True])
def test_dependency_notice_and_manager_are_deleted(qapp, monkeypatch, open_manager):
    from ui import dependency_settings as module

    parent = QWidget()
    manager = SimpleNamespace(dependency_errors={"test": "missing library"})
    destroyed = []

    class ManagerDialog(QDialog):
        def __init__(self, manager, parent):
            super().__init__(parent)
            self.destroyed.connect(lambda: destroyed.append("manager"))

    def dismiss_notice(notice):
        notice.destroyed.connect(lambda: destroyed.append("notice"))
        text = "Open Dependency Manager" if open_manager else "Later"
        next(button for button in notice.buttons() if button.text() == text).click()
        return notice.result()

    monkeypatch.setattr(module, "DependencyManagerDialog", ManagerDialog)
    monkeypatch.setattr(QMessageBox, "exec", dismiss_notice)
    monkeypatch.setattr(QDialog, "exec", lambda self: self.reject())
    try:
        module.show_dependency_notice(manager, parent)
        flush_deletes()
        assert not parent.findChildren(QDialog)
        assert destroyed == (["notice", "manager"] if open_manager else ["notice"])
    finally:
        parent.deleteLater()
        flush_deletes()
