"""Recent-song dialogs must release their widget trees after each opening."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog, QListWidget, QPushButton, QStyledItemDelegate, QWidget

from ui.main_window import Player


@pytest.mark.parametrize("outcome", ["load", "cancel", "close", "error"])
def test_recent_dialog_is_deleted_after_every_open(tmp_path, qapp, monkeypatch, outcome):
    audio = tmp_path / "song.mp3"
    audio.touch()
    monkeypatch.setattr("ui.main_window.get_recent_songs", lambda: [
        {"audio": str(audio), "timestamp": "2026-11-01T12:00:00"},
    ])
    parent = QWidget()
    loaded = []
    destroyed = []
    parent.load_audio = loaded.append

    def finish(dialog):
        dialog.destroyed.connect(lambda: destroyed.append(True))
        if outcome == "error":
            raise RuntimeError("recent dialog failed")
        if outcome == "close":
            dialog.close()
        else:
            if outcome == "load":
                dialog.findChild(QListWidget).setCurrentRow(0)
            text = "Load" if outcome == "load" else "Cancel"
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == text)
            button.click()
        expected = QDialog.DialogCode.Accepted if outcome == "load" else QDialog.DialogCode.Rejected
        assert dialog.result() == expected
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", finish)
    try:
        for opening in range(5):
            if outcome == "error":
                with pytest.raises(RuntimeError, match="recent dialog failed"):
                    Player.show_recent_songs(parent)
            else:
                Player.show_recent_songs(parent)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert parent.findChildren(QDialog) == []
            assert len(destroyed) == opening + 1
        assert loaded == ([str(audio)] * 5 if outcome == "load" else [])
    finally:
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_opening_recents_does_not_measure_every_hidden_title(tmp_path, qapp, monkeypatch):
    recent = []
    for row in range(20):
        title = "\uc694\uc544\uc18c\ube44" if row == 16 else f"song-{row}"
        audio = tmp_path / f"{title}.mp3"
        audio.touch()
        recent.append({"audio": str(audio)})
    monkeypatch.setattr("ui.main_window.get_recent_songs", lambda: recent)
    measured = []
    loaded = []
    parent = QWidget()
    parent.load_audio = loaded.append

    class TrackingDelegate(QStyledItemDelegate):
        def sizeHint(self, option, index):
            measured.append(index.row())
            return super().sizeHint(option, index)

    class TrackingList(QListWidget):
        def __init__(self):
            super().__init__()
            self.setItemDelegate(TrackingDelegate(self))

    monkeypatch.setattr("ui.main_window.QListWidget", TrackingList)

    def inspect(dialog):
        view = dialog.findChild(QListWidget)
        view.setFixedHeight(80)
        dialog.show()
        qapp.processEvents()
        view.grab()  # Force layout and painting with the first rows visible.
        assert view.count() == 20
        assert view.item(16).text() == "\uc694\uc544\uc18c\ube44.mp3"
        assert view.visualItemRect(view.item(16)).top() >= view.viewport().height()
        # Sizing offscreen text can load whole additional system fonts.
        assert measured
        assert 16 not in measured
        assert len(set(measured)) < 10
        # Hidden rows must still become accessible and loadable on scrolling.
        view.setCurrentRow(16)
        view.scrollToItem(view.item(16))
        qapp.processEvents()
        assert view.visualItemRect(view.item(16)).intersects(view.viewport().rect())
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "Load").click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", inspect)
    try:
        Player.show_recent_songs(parent)
        assert loaded == [recent[16]["audio"]]
    finally:
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
