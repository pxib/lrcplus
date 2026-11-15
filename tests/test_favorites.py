from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from core.favorites import favorite_key, load_favorites, save_favorites
from core.music_library import LibraryTrack
from core.settings import load_all_settings, save_all_settings
from ui.main_window import Player
from ui.track_actions import add_favorite_action
from PySide6.QtWidgets import QMenu


def test_favorites_survive_missing_files_and_preserve_other_preferences(tmp_path):
    paths = [tmp_path / "日本語.mp3", tmp_path / "missing.mp3"]
    save_all_settings({"playback": {"volume": 37}})
    save_favorites([*paths, paths[0]])
    assert load_favorites() == {favorite_key(path) for path in paths}
    assert load_all_settings()["playback"] == {"volume": 37}
    save_all_settings({"favorite_tracks": [None, 7, "", str(paths[0])]})
    assert load_favorites() == {favorite_key(paths[0])}
    save_all_settings({"favorite_tracks": {"bad": True}})
    assert load_favorites() == set()


@pytest.fixture
def favorite_player(qapp, tmp_path, monkeypatch):
    window = Player()
    paths = [tmp_path / f"song-{i}.mp3" for i in range(3)]
    for path in paths:
        path.touch()
    window.library_tracks = [LibraryTrack(str(path), f"Song {i}", album="Album") for i, path in enumerate(paths)]
    window._populate_library_view()
    try:
        yield window, paths
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_favorite_filter_intersects_search_and_click_does_not_start_playback(favorite_player, monkeypatch):
    window, paths = favorite_player
    album = window.library_table.topLevelItem(0)
    column = window.library_favorite_column
    loads = []
    monkeypatch.setattr(window, "load_audio", loads.append)
    window._toggle_library_favorite(album.child(1), column)
    window._play_library_item(album.child(1), column)
    assert not loads
    assert album.child(1).text(column) == "♥"
    assert not window.library_table.isColumnHidden(column)
    window.library_favorites_filter.setChecked(True)
    assert [album.child(i).isHidden() for i in range(3)] == [True, False, True]
    window.library_search.setText("Song 0")
    window._filter_library_view()
    assert album.isHidden()
    window.library_search.clear()
    window._filter_library_view()
    window._populate_library_view()
    album = window.library_table.topLevelItem(0)
    assert album.child(1).text(column) == "♥"
    window._set_tracks_favorite([paths[1]], False)
    assert album.isHidden()
    assert load_favorites() == set()


def test_current_heart_tracks_song_changes_and_preserves_playback(favorite_player):
    window, paths = favorite_player
    window.current_audio_path = paths[0]
    window.queue_changed.emit()
    assert window.favorite_button.isEnabled()
    window.favorite_button.click()
    assert window.favorite_button.isChecked()
    assert favorite_key(paths[0]) in load_favorites()
    window.current_audio_path = paths[1]
    window.queue_changed.emit()
    assert not window.favorite_button.isChecked()
    window.current_audio_path = None
    window.queue_changed.emit()
    assert not window.favorite_button.isEnabled()


def test_mixed_favorite_selection_adds_all_then_removes_all(qapp, tmp_path):
    paths = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
    calls = []
    menu = QMenu()
    try:
        action = add_favorite_action(menu, paths, {favorite_key(paths[0])}, lambda *args: calls.append(args))
        assert action.text() == "Add to favorites"
        action.trigger()
        assert calls == [(paths, True)]
        action = add_favorite_action(menu, paths, {favorite_key(path) for path in paths}, lambda *args: calls.append(args))
        assert action.text() == "Remove from favorites"
        action.trigger()
        assert calls[-1] == (paths, False)
    finally:
        menu.deleteLater()
