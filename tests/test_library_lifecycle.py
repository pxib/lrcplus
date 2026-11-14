"""Library scans, temporary widgets and layout writes have bounded lifetimes."""

from threading import Event
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer
from PySide6.QtWidgets import QMenu

from core.music_library import LibraryTrack
from core import settings
from ui.main_window import Player
from ui.player_dialogs import LibraryScanWorker


def wait_until(qapp, predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.001)
    assert predicate()


def flush_deletes():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(Player, "_automatic_update_check", lambda self: None)
    window = Player()
    yield window
    window.close()
    wait_until(qapp, lambda: window._library_scan_worker is None)
    window.close()
    window.deleteLater()
    flush_deletes()


def test_close_waits_for_scan_without_blocking_gui(window, qapp, monkeypatch):
    entered, release = Event(), Event()

    def scan(folders, *, cancelled):
        entered.set()
        assert release.wait(3)
        assert cancelled()
        return []

    monkeypatch.setattr("ui.player_dialogs.scan_folders", scan)
    window.show()
    window.rescan_music_library()
    assert entered.wait(2)
    try:
        assert not window.close()
        assert window.isVisible()
        assert window._library_scan_worker.isRunning()
    finally:
        release.set()
    wait_until(qapp, lambda: not window.isVisible())
    flush_deletes()
    assert window._library_scan_worker is None
    assert not window.findChildren(LibraryScanWorker)


def test_scan_requests_coalesce_and_never_publish_old_folders(window, qapp, monkeypatch, tmp_path):
    entered, release = Event(), Event()
    calls = []

    def scan(folders, *, cancelled):
        calls.append(list(folders))
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return [LibraryTrack(str(tmp_path / (folders[0] + ".mp3")), folders[0])]

    monkeypatch.setattr("ui.player_dialogs.scan_folders", scan)
    window.library_folders = ["old"]
    window.rescan_music_library()
    assert entered.wait(2)
    try:
        for folder in ("intermediate", "latest"):
            window.library_folders = [folder]
            window.rescan_music_library()
    finally:
        release.set()
    wait_until(qapp, lambda: window._library_scan_worker is None)
    assert calls == [["old"], ["latest"]]
    assert [track.title for track in window.library_tracks] == ["latest"]
    assert settings.load_music_library()["folders"] == ["latest"]
    flush_deletes()
    assert not window.findChildren(LibraryScanWorker)


@pytest.mark.parametrize("fail", [False, True])
def test_finished_scanners_are_deleted(window, qapp, monkeypatch, fail):
    def scan(folders, **kwargs):
        if fail:
            raise OSError("Unavailable drive")
        return []

    monkeypatch.setattr("ui.player_dialogs.scan_folders", scan)
    for _ in range(3):
        window.rescan_music_library()
        wait_until(qapp, lambda: window._library_scan_worker is None)
        flush_deletes()
        assert not window.findChildren(LibraryScanWorker)
        assert window.library_table.isEnabled()


def test_column_resize_coalesces_small_writes_and_flushes_on_close(window, monkeypatch):
    from ui import player_library

    writes = []
    save = player_library.save_music_library_columns
    monkeypatch.setattr(player_library, "save_music_library_columns",
                        lambda state: (writes.append(state), save(state)))
    window.library_tracks = [LibraryTrack("song.mp3", "Song")]
    monkeypatch.setattr(LibraryTrack, "to_dict", lambda self: pytest.fail("Reserialized library"))
    for width in range(340, 352):
        window.library_table.setColumnWidth(2, width)
    assert not writes
    assert window._library_column_save_timer.isActive()
    window.close()
    assert len(writes) == 1
    assert settings.load_music_library()["columns"]["widths"]["2"] == 351


def test_layout_file_preserves_library_and_legacy_columns():
    original = {"music_library": {"folders": ["music"], "tracks": [{"title": "Song"}],
                                  "columns": {"sort_column": 1}}}
    settings.save_all_settings(original)
    assert settings.load_music_library()["columns"] == {"sort_column": 1}
    before = settings.SETTINGS_PATH.read_bytes()
    settings.save_music_library_columns({"sort_column": 2})
    assert settings.SETTINGS_PATH.read_bytes() == before
    assert settings.load_music_library()["columns"] == {"sort_column": 2}
    settings.save_music_library(["other"], [], {"sort_column": 3})
    assert settings.load_music_library()["columns"] == {"sort_column": 3}


def test_real_folder_scanner_stops_between_files(tmp_path, monkeypatch):
    from core import music_library

    cancelled = Event()
    reads = []
    for index in range(3):
        (tmp_path / f"song-{index}.mp3").touch()

    def read(path):
        reads.append(path)
        cancelled.set()
        return LibraryTrack(str(path), path.stem)

    monkeypatch.setattr(music_library, "read_track", read)
    assert music_library.scan_folders([tmp_path], cancelled=cancelled.is_set) == []
    assert len(reads) == 1


def test_repeated_header_menus_are_deleted(window, qapp):
    before = len(window.findChildren(QMenu))

    def dismiss():
        for widget in qapp.topLevelWidgets():
            if isinstance(widget, QMenu):
                widget.close()

    for _ in range(3):
        QTimer.singleShot(10, dismiss)
        window._show_library_column_menu(QPoint(10, 2))
        flush_deletes()
        assert len(window.findChildren(QMenu)) == before
