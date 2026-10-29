from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import QModelIndex, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QInputDialog, QMessageBox

from core.music_library import LibraryTrack
from core.playlists import load_saved_playlists
from core.settings import get_playback_settings, save_startup_settings
from ui.main_window import Player
from ui.playlist_dialog import PlaylistDialog


class FakeMedia:
    def __init__(self):
        self.playing = False
        self.time = 0
        self.loads = []
        self.status = QMediaPlayer.MediaStatus.LoadedMedia

    def stop(self):
        self.playing = False
        self.time = 0

    def play(self):
        self.playing = True

    def pause(self):
        self.playing = False

    def isPlaying(self):
        return self.playing

    def position(self):
        return self.time

    def duration(self):
        return 60_000

    def setPosition(self, value):
        self.time = value

    def setSource(self, source):
        self.loads.append(source.toLocalFile())

    def mediaStatus(self):
        return self.status


@pytest.fixture
def queue_player(qapp, monkeypatch):
    window = Player()
    real_media = window.media_player
    window.media_player = FakeMedia()
    for method in ("update_background_for_audio", "refresh_lyrics_settings_for_current_file",
                   "_flush_pcm_output", "_configure_pcm_volume", "_set_native_pitch_compensation"):
        monkeypatch.setattr(window, method, lambda *args: None)
    monkeypatch.setattr(window.waveform_widget, "load_audio", lambda *args: None)
    monkeypatch.setattr(window, "find_lrc_for_audio", lambda path: Path(path).with_suffix(".lrc"))
    try:
        yield window
    finally:
        window.media_player = real_media
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.fixture
def tracks(tmp_path):
    result = [tmp_path / f"{name}.mp3" for name in "abcd"]
    for path in result:
        path.touch()
    return result


def wait_for(qapp, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert condition()


def test_lyric_scan_keeps_playback_and_gui_responsive(queue_player, tracks, qapp, monkeypatch):
    window = queue_player
    audio = tracks[0]
    lyric = audio.with_suffix(".lrc")
    lyric.write_text("[00:01.000]Hello", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    threads = []
    original_index = window._get_lyrics_folder_index

    def slow_index(folder):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return original_index(folder)

    monkeypatch.setattr(window, "find_lrc_for_audio", Player.find_lrc_for_audio.__get__(window))
    monkeypatch.setattr(window, "_get_lyrics_folder_index", slow_index)
    applied = []
    monkeypatch.setattr(window, "load_lrc_file", lambda path: applied.append((path, threading.get_ident())))
    positions = []
    monkeypatch.setattr(window, "update_lyrics", positions.append)
    window.lyrics = [(0, "Previous track")]
    window.lyrics_list_widget.addItem("Previous track")
    try:
        window.load_audio(audio)
        window.media_player.play()
        assert entered.wait(2)
        assert window.media_player.isPlaying()
        assert not window.lyrics
        assert window.lyrics_list_widget.count() == 0
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks and not applied
        assert threads[0] != threading.get_ident()
        window.media_player.time = 12345
        release.set()
        wait_for(qapp, lambda: bool(applied))
        assert applied == [(str(lyric), threading.get_ident())]
        assert positions == [12345]
    finally:
        release.set()


@pytest.mark.parametrize("action", ["change_track", "clear_queue", "manual_lyrics", "close"])
def test_late_lyric_search_cannot_replace_current_state(queue_player, tracks, qapp, monkeypatch, action):
    window = queue_player
    entered, release = threading.Event(), threading.Event()
    lyric = tracks[0].with_suffix(".lrc")
    lyric.write_text("[00:01.000]Hello", encoding="utf-8")
    next_lyric = tracks[1].with_suffix(".lrc")
    next_lyric.write_text("[00:02.000]Next", encoding="utf-8")

    def slow_search(path):
        entered.set()
        assert release.wait(5)
        return Path(path).with_suffix(".lrc")

    monkeypatch.setattr(window, "find_lrc_for_audio", slow_search)
    window.load_audio(tracks[0])
    future = window._lyrics_search_job._future
    applied = []
    try:
        assert entered.wait(2)
        if action == "change_track":
            window.load_audio(tracks[1])
        elif action == "clear_queue":
            window.clear_queue()
        elif action == "manual_lyrics":
            monkeypatch.setattr(window, "_start_furigana_prefetch", lambda: None)
            window.load_lrc_file(str(lyric))
            assert window.current_lyrics_path == lyric
        else:
            monkeypatch.setattr(window.media_player, "setAudioBufferOutput", lambda _: None, raising=False)
            window.close()
        monkeypatch.setattr(window, "load_lrc_file", applied.append)
        release.set()
        future.result(timeout=5)
        if action == "change_track":
            wait_for(qapp, lambda: bool(applied))
            assert applied == [str(next_lyric)]
            assert window.current_audio_path == tracks[1]
        else:
            window._lyrics_search_job._poll()
            qapp.processEvents()
            assert not applied
    finally:
        release.set()


@pytest.mark.parametrize("fails", [False, True])
def test_missing_or_failed_lyric_search_leaves_audio_ready(queue_player, tracks, qapp, monkeypatch, fails):
    window = queue_player

    def search(path):
        if fails:
            raise OSError("Folder unavailable")
        return Path(path).with_suffix(".lrc")

    monkeypatch.setattr(window, "find_lrc_for_audio", search)
    window.load_audio(tracks[0])
    window.media_player.play()
    wait_for(qapp, lambda: window._lyrics_search_job._future is None)
    assert window.media_player.isPlaying()
    assert window.current_lyrics_path is None
    assert not window.lyrics
    if fails:
        assert "Folder unavailable" in window.statusBar().currentMessage()


def test_rapid_track_changes_skip_pending_searches(queue_player, tracks, qapp, monkeypatch):
    window = queue_player
    entered, release = threading.Event(), threading.Event()
    searched, applied = [], []
    for audio in tracks:
        audio.with_suffix(".lrc").write_text("[00:01]Hello", encoding="utf-8")

    def search(path):
        searched.append(path)
        if path == tracks[0]:
            entered.set()
            assert release.wait(5)
        return path.with_suffix(".lrc")

    monkeypatch.setattr(window, "find_lrc_for_audio", search)
    monkeypatch.setattr(window, "load_lrc_file", applied.append)
    try:
        window.load_audio(tracks[0])
        assert entered.wait(2)
        window.load_audio(tracks[1])
        window.load_audio(tracks[2])
        release.set()
        wait_for(qapp, lambda: bool(applied))
        assert searched == [tracks[0], tracks[2]]
        assert applied == [str(tracks[2].with_suffix(".lrc"))]
    finally:
        release.set()


def test_duplicate_entries_advance_by_occurrence(queue_player, tracks):
    window = queue_player
    a, b, c, _ = tracks
    window.replace_queue([a, b, a, c])
    window.play_queue_index(2)
    assert window.current_index == 2
    window.next_track()
    assert window.current_audio_path == c
    assert window.current_index == 3
    window.previous_track()
    assert window.current_audio_path == a
    assert window.current_index == 2


def test_reordering_preserves_current_occurrence_playback_and_position(queue_player, tracks):
    window = queue_player
    a, b, c, _ = tracks
    window.replace_queue([a, b, a, c])
    window.play_queue_index(2)
    window.media_player.time = 12345
    loads = list(window.media_player.loads)
    window.reorder_queue([2, 3, 0, 1])
    assert window.current_index == 0
    assert window.media_player.loads == loads
    assert window.media_player.time == 12345
    assert window.media_player.isPlaying()
    window.next_track()
    assert window.current_audio_path == c


def test_enqueue_and_play_next_switch_from_library_without_interrupting(queue_player, tracks):
    window = queue_player
    a, b, c, d = tracks
    window.replace_queue([a, b])
    window.play_queue_index(0)
    window.media_player.time = 1000
    window.playback_settings["play_mode"] = "library"
    loads = list(window.media_player.loads)
    window.enqueue_tracks([d])
    window.enqueue_tracks([c], play_next=True)
    assert window.playlist == [a, c, b, d]
    assert window.media_player.loads == loads
    assert window.media_player.time == 1000
    assert get_playback_settings()["play_mode"] == "playlist"
    window.shuffle_enabled = True
    window.next_track()
    assert window.current_audio_path == c


def test_play_next_priorities_survive_reordering_and_removal(queue_player, tracks):
    window = queue_player
    a, b, c, d = tracks
    window.replace_queue([a, b])
    window.enqueue_tracks([c, d], play_next=True)
    window.reorder_queue([1, 3, 2, 0])  # c, b, d, a (current)
    window.remove_queue_tracks([1])
    window.media_player.status = QMediaPlayer.MediaStatus.EndOfMedia
    window.handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert window.current_audio_path == c
    window.next_track()
    assert window.current_audio_path == d


def test_queue_play_next_moves_existing_entries_instead_of_copying(queue_player, tracks):
    window = queue_player
    a, b, c, d = tracks
    window.replace_queue(tracks)
    window.play_queue_index(1)
    window.move_queue_tracks_next([0, 1, 3])
    assert window.playlist == [b, a, d, c]
    assert window.current_index == 0
    window.shuffle_enabled = True
    window.next_track()
    assert window.current_audio_path == a
    window.next_track()
    assert window.current_audio_path == d


@pytest.mark.parametrize("playing", [False, True])
def test_removing_current_track_keeps_playback_state(queue_player, tracks, playing):
    window = queue_player
    window.replace_queue(tracks)
    window.play_queue_index(2)
    window.media_player.playing = playing
    window.remove_queue_tracks([0, 2])
    assert window.playlist == [tracks[1], tracks[3]]
    assert window.current_index == 1
    assert window.current_audio_path == tracks[3]
    assert window.media_player.isPlaying() == playing


def test_replacing_queue_keeps_current_audio_when_present_and_clear_stops(queue_player, tracks):
    window = queue_player
    window.replace_queue(tracks)
    window.play_queue_index(1)
    window.media_player.time = 8000
    loads = list(window.media_player.loads)
    window.replace_queue([tracks[3], tracks[1]])
    assert window.current_index == 1
    assert window.media_player.loads == loads
    assert window.media_player.time == 8000
    window.clear_queue()
    assert window.playlist == []
    assert window.current_index == -1
    assert window.current_audio_path is None
    assert not window.media_player.isPlaying()
    assert not window.play_button.isEnabled()


def test_queue_session_restores_duplicate_occurrence(queue_player, tracks):
    window = queue_player
    save_startup_settings({"restore_playlist": True})
    window.startup_settings["restore_playlist"] = True
    window.replace_queue([tracks[0], tracks[1], tracks[0]])
    window.play_queue_index(2)
    window._save_startup_state()
    window.clear_queue()
    window._restore_startup_state()
    assert window.current_index == 2
    assert window.playlist == [tracks[0], tracks[1], tracks[0]]


def test_queue_session_missing_files_do_not_shift_current_occurrence(queue_player, tracks):
    window = queue_player
    window.startup_settings["restore_playlist"] = True
    window.replace_queue([tracks[0], tracks[1], tracks[2], tracks[1]])
    window.play_queue_index(1)
    window._save_startup_state()
    tracks[0].unlink()
    window.clear_queue()
    window._restore_startup_state()
    assert window.current_index == 0
    assert window.current_audio_path == tracks[1]
    assert window.playlist == [tracks[1], tracks[2], tracks[1]]


def test_library_multi_selection_expands_albums_once(queue_player, tracks):
    window = queue_player
    window.library_tracks = [LibraryTrack(str(path), path.stem, album="Album") for path in tracks]
    window._populate_library_view()
    album = window.library_table.topLevelItem(0)
    album.setSelected(True)
    album.child(1).setSelected(True)
    assert window._selected_library_paths() == [str(path) for path in tracks]
    album.child(2).setHidden(True)
    assert str(tracks[2]) not in window._selected_library_paths()
    album.setHidden(True)
    assert window._selected_library_paths() == []


def test_dialog_reorders_qt_model_and_keeps_play_target(qapp, queue_player, tracks):
    window = queue_player
    window.replace_queue(tracks)
    dialog = PlaylistDialog(window.playlist, window.current_index, window)
    dialog.order_requested.connect(window.reorder_queue)
    dialog.track_requested.connect(window.play_queue_index)
    refresh = lambda: dialog.set_playlist(window.playlist, window.current_index)
    window.queue_changed.connect(refresh)
    try:
        dialog._select_indices([2])
        assert dialog.list_widget.model().moveRow(QModelIndex(), 2, QModelIndex(), 0)
        qapp.processEvents()
        assert window.playlist == [tracks[2], tracks[0], tracks[1], tracks[3]]
        assert dialog._selected_indices() == [0]
        dialog.load_button.click()
        assert window.current_audio_path == tracks[2]
        dialog._select_indices([1, 2])
        dialog.down_button.click()
        assert window.playlist == [tracks[2], tracks[3], tracks[0], tracks[1]]
        assert dialog._selected_indices() == [2, 3]
    finally:
        window.queue_changed.disconnect(refresh)
        dialog.deleteLater()
        qapp.processEvents()


def test_dialog_saves_loads_and_appends_without_modifying_saved_playlist(qapp, queue_player, tracks, monkeypatch):
    window = queue_player
    window.replace_queue(tracks[:2])
    dialog = PlaylistDialog(window.playlist, window.current_index, window)
    dialog.replace_requested.connect(window.replace_queue)
    dialog.add_requested.connect(lambda paths, next_: window.enqueue_tracks(paths, play_next=next_))
    refresh = lambda: dialog.set_playlist(window.playlist, window.current_index)
    window.queue_changed.connect(refresh)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Practice", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    try:
        dialog._save_as()
        assert load_saved_playlists()["Practice"] == [str(path) for path in tracks[:2]]
        window.replace_queue(tracks[2:])
        dialog._use_saved(append=True)
        assert window.playlist == tracks[2:] + tracks[:2]
        dialog._use_saved(append=False)
        assert window.playlist == tracks[:2]
        assert len(load_saved_playlists()["Practice"]) == 2
        tracks[0].unlink()
        dialog._use_saved(append=False)
        assert window.playlist == [tracks[1]]
        assert "Skipped 1" in dialog.status_label.text()
        assert len(load_saved_playlists()["Practice"]) == 2
    finally:
        window.queue_changed.disconnect(refresh)
        dialog.deleteLater()
        qapp.processEvents()


def test_queue_manager_wires_actions_and_disconnects_on_close(qapp, queue_player, tracks, monkeypatch):
    window = queue_player
    window.replace_queue(tracks[:2])

    def use_dialog(dialog):
        dialog.add_requested.emit([tracks[2]], False)
        assert dialog.playlist == tracks[:3]
        dialog.play_next_requested.emit([2])
        assert dialog.playlist == [tracks[0], tracks[2], tracks[1]]
        dialog.track_requested.emit(1)
        assert window.current_audio_path == tracks[2]
        dialog.remove_tracks_requested.emit([2])
        assert dialog.playlist == [tracks[0], tracks[2]]
        dialog.clear_requested.emit()
        assert dialog.playlist == []
        return 0

    monkeypatch.setattr(PlaylistDialog, "exec", use_dialog)
    window.show_playlist()
    qapp.processEvents()
    # A closed manager must not leave a callback to its deleted widgets.
    window.enqueue_tracks(tracks[:1])
    assert window.playlist == tracks[:1]


def test_plugin_audio_import_preserves_active_playback(queue_player, tracks):
    from core.plugin_manager import PluginManager

    window = queue_player
    window.replace_queue(tracks[:1])
    window.play_queue_index(0)
    window.media_player.time = 12345
    loads = list(window.media_player.loads)
    manager = PluginManager()
    manager.set_host_window(window)
    api = manager.get_api("ytdlp_import")
    api.import_audio_files([str(tracks[1])])
    api.import_audio_files([str(tracks[2])], add_to_queue=False)
    assert window.playlist == tracks[:2]
    assert window.current_audio_path == tracks[0]
    assert window.media_player.loads == loads
    assert window.media_player.time == 12345
    assert window.media_player.isPlaying()
    assert {track.path for track in window.library_tracks} == {str(tracks[1]), str(tracks[2])}


def test_plugin_lyric_attachment_refreshes_lyrics_without_restarting_audio(queue_player, tracks, monkeypatch):
    from core.plugin_manager import PluginManager
    from core.settings import get_lyrics_attachment

    window = queue_player
    monkeypatch.setattr(window, "_start_furigana_prefetch", lambda: None)
    window.replace_queue(tracks[:1])
    window.play_queue_index(0)
    window.media_player.time = 12345
    loads = list(window.media_player.loads)
    original = tracks[0].with_suffix(".lrc")
    original.write_text("[00:00.000]Authored", encoding="utf-8")
    captions = tracks[0].with_name("a.en.manual.lrc")
    captions.write_text("[00:01.000]Subtitles\n[00:03.000]\n", encoding="utf-8")
    manager = PluginManager()
    manager.set_host_window(window)
    manager.get_api("ytdlp_import").attach_lyrics(str(tracks[0]), str(captions))
    assert window.current_lyrics_path == captions
    assert get_lyrics_attachment(tracks[0]) == str(captions)
    assert window.media_player.loads == loads
    assert window.media_player.time == 12345
    assert window.media_player.isPlaying()
    assert original.read_text(encoding="utf-8") == "[00:00.000]Authored"


def test_oversized_lyric_file_is_rejected_before_allocating_scrolling_widgets(queue_player, tmp_path, monkeypatch):
    from lyrics.models import LyricLine

    window = queue_player
    path = tmp_path / "dense.lrc"
    path.write_text("[00:00.000]Dense\n", encoding="utf-8")
    window.lyrics = [(1.0, "Old lyric")]
    window._playback_line_indices = [0]
    window.lyrics_list_widget.addItem("Old lyric")
    with monkeypatch.context() as patch:
        patch.setattr("core.lyrics_controller.load_lyrics", lambda path: [LyricLine(1, "Frame")] * 37043)
        patch.setattr(window.scrolling_lyrics_widget, "set_lyrics", lambda lines: pytest.fail("Oversized lyrics created widgets"))
        window.load_lrc_file(path)
    assert window.lyric_lines == window.lyrics == window.lyric_times == []
    assert window._playback_line_indices == []
    assert window.lyrics_list_widget.count() == 0
    assert window.scrolling_lyrics_widget.rows == []
    assert "37,043" in window.statusBar().currentMessage()
    monkeypatch.setattr(window, "_start_furigana_prefetch", lambda: None)
    window.load_lrc_file(path)
    assert len(window.scrolling_lyrics_widget.rows) == 1
    assert window.statusBar().currentMessage() == ""


def test_oversized_plugin_attachment_keeps_previous_association(queue_player, tracks, monkeypatch):
    from core.plugin_manager import PluginManager
    from core.settings import get_lyrics_attachment, set_lyrics_attachment
    from lyrics.models import LyricLine

    original = tracks[0].with_suffix(".lrc")
    original.write_text("[00:00.000]Authored", encoding="utf-8")
    set_lyrics_attachment(tracks[0], original)
    manager = PluginManager()
    manager.set_host_window(queue_player)
    monkeypatch.setattr("lyrics.loader.load_lyrics", lambda path: [LyricLine(1, "Frame")] * 37043)
    with pytest.raises(ValueError, match="limit"):
        manager.get_api("ytdlp_import").attach_lyrics(str(tracks[0]), str(original))
    assert get_lyrics_attachment(tracks[0]) == str(original)
