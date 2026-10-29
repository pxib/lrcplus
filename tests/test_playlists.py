import pytest

from core.playlists import (
    available_tracks, delete_playlist, load_saved_playlists, read_m3u,
    rename_playlist, save_playlist, write_m3u,
)
from core.settings import load_all_settings, save_all_settings


def test_saved_playlists_preserve_duplicates_missing_paths_and_other_preferences(tmp_path):
    save_all_settings({"playback": {"volume": 37}})
    track = tmp_path / "song.mp3"
    tracks = [track, tmp_path / "missing.mp3", track]
    save_playlist(" Practice ", tracks)
    tracks.clear()
    assert load_saved_playlists() == {"Practice": [str(track), str(tmp_path / "missing.mp3"), str(track)]}
    assert load_all_settings()["playback"] == {"volume": 37}
    rename_playlist("Practice", "practice")
    assert list(load_saved_playlists()) == ["practice"]
    delete_playlist("practice")
    assert load_saved_playlists() == {}
    assert load_all_settings()["playback"] == {"volume": 37}


def test_playlist_name_collisions_require_explicit_overwrite(tmp_path):
    first, second = tmp_path / "first.mp3", tmp_path / "second.mp3"
    save_playlist("Morning", [first])
    with pytest.raises(FileExistsError):
        save_playlist(" morning ", [second])
    assert load_saved_playlists() == {"Morning": [str(first)]}
    save_playlist("MORNING", [second], overwrite=True)
    save_playlist("Evening", [first])
    with pytest.raises(FileExistsError):
        rename_playlist("Evening", "morning")
    with pytest.raises(ValueError):
        save_playlist("  ", [first])
    assert load_saved_playlists()["MORNING"] == [str(second)]


def test_malformed_saved_playlists_do_not_break_valid_entries(tmp_path):
    save_all_settings({"saved_playlists": {"bad": None, "": [], "Good": [None, 1, "song.mp3", ""], "good": []}})
    assert load_saved_playlists() == {"Good": ["song.mp3"]}
    save_all_settings({"saved_playlists": []})
    assert load_saved_playlists() == {}


def test_m3u_import_resolves_relative_paths_unicode_file_uris_and_repeats(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    song = music / "日本語 100%.mp3"
    song.touch()
    unsupported = tmp_path / "notes.txt"
    unsupported.touch()
    playlist = tmp_path / "mix.m3u8"
    playlist.write_text("\ufeff#EXTM3U\n#EXTINF:123,Song\n\nmusic/日本語 100%.mp3\n"
                        + song.as_uri() + "\nmissing.mp3\nhttps://example.com/audio.mp3\nnotes.txt\n", encoding="utf-8")
    result = read_m3u(playlist)
    assert result.paths == [song, song]
    assert len(result.unavailable) == 3


def test_m3u_export_round_trips_with_relative_paths_and_hash_filenames(tmp_path):
    song = tmp_path / "#song 日本語.mp3"
    song.touch()
    other = tmp_path / "other.flac"
    other.touch()
    playlist = tmp_path / "mix.m3u8"
    write_m3u(playlist, [song, other, song])
    assert str(tmp_path) not in playlist.read_text(encoding="utf-8")
    result = read_m3u(playlist)
    assert result.paths == [song, other, song]
    assert result.unavailable == []


def test_legacy_m3u_encoding_is_supported_but_invalid_m3u8_is_reported(tmp_path):
    song = tmp_path / "café.mp3"
    song.touch()
    playlist = tmp_path / "mix.m3u"
    playlist.write_bytes("café.mp3\n".encode("cp1252"))
    assert read_m3u(playlist).paths == [song]
    invalid = tmp_path / "invalid.m3u8"
    invalid.write_bytes(playlist.read_bytes())
    with pytest.raises(UnicodeDecodeError):
        read_m3u(invalid)


def test_unavailable_tracks_are_filtered_without_changing_saved_list(tmp_path):
    song = tmp_path / "song.mp3"
    song.touch()
    paths = [str(song), str(tmp_path / "missing.mp3"), str(song)]
    result = available_tracks(paths)
    assert result.paths == [song, song]
    assert len(result.unavailable) == 1
    assert len(paths) == 3
