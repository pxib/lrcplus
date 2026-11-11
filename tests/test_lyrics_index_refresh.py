import os
from pathlib import Path

import pytest

from core.lyrics_search import LyricsSearchMixin


class Search(LyricsSearchMixin):
    _lyrics_folder_index_cache = {}


_DIRECTORY_STAMPS = {}


def changed_directory(path):
    # Windows can reset an explicitly advanced timestamp to the same clock
    # tick after the next filesystem operation. Always advance our last stamp.
    stamp = max(path.stat().st_mtime_ns, _DIRECTORY_STAMPS.get(path, 0)) + 1_000_000_000
    _DIRECTORY_STAMPS[path] = stamp
    os.utime(path, ns=(stamp, stamp))


def test_unchanged_folder_reuses_index_and_new_nested_lyrics_refresh_it(tmp_path):
    Search._lyrics_folder_index_cache = {}
    folder = tmp_path / "nested"
    folder.mkdir()
    before = Search._get_lyrics_folder_index(tmp_path)
    assert Search._get_lyrics_folder_index(tmp_path) is before
    lyric = folder / "new.lrc"
    lyric.write_text("[00:01.000]New lyric", encoding="utf-8")
    changed_directory(folder)
    after = Search._get_lyrics_folder_index(tmp_path)
    assert [entry[0] for entry in after["entries"]] == [lyric]


def test_renamed_and_deleted_lyrics_are_removed_from_cached_index(tmp_path):
    Search._lyrics_folder_index_cache = {}
    lyric = tmp_path / "before.lrc"
    lyric.write_text("[00:00.000]Text", encoding="utf-8")
    Search._get_lyrics_folder_index(tmp_path)
    renamed = tmp_path / "after.lrc"
    lyric.rename(renamed)
    changed_directory(tmp_path)
    assert [entry[0] for entry in Search._get_lyrics_folder_index(tmp_path)["entries"]] == [renamed]
    renamed.unlink()
    changed_directory(tmp_path)
    assert Search._get_lyrics_folder_index(tmp_path)["entries"] == []


@pytest.mark.parametrize("suffix", [".lrc", ".lrcx"])
@pytest.mark.parametrize("change", ["add", "retarget", "remove"])
def test_external_attachment_edit_refreshes_index(tmp_path, suffix, change):
    Search._lyrics_folder_index_cache = {}
    folder = tmp_path / "nested"
    folder.mkdir()
    lyric = folder / ("unrelated" + suffix)
    song_a = tmp_path / "SongA.mp3"
    song_b = tmp_path / "SongB.mp3"
    text = "[00:00.000]Text"
    before = text if change == "add" else f"[attach:{song_a}]\n{text}"
    after = text if change == "remove" else f"[attach:{song_b}]\n{text}"
    lyric.write_text(before, encoding="utf-8")
    search = Search()

    def lookup(song):
        return search.find_lrc_for_audio(song, configured_folders=[], attachments={})

    assert lookup(song_a) == (song_a.with_suffix(".lrc") if change == "add" else lyric)
    directories = [tmp_path, folder]
    directory_stamps = [(path.stat().st_atime_ns, path.stat().st_mtime_ns)
                        for path in directories]
    old_stamp = lyric.stat().st_mtime_ns
    lyric.write_text(after, encoding="utf-8")
    os.utime(lyric, ns=(old_stamp + 1_000_000_000, old_stamp + 1_000_000_000))
    # Reproduce an in-place edit independently of filesystem directory behavior.
    for path, stamps in zip(directories, directory_stamps):
        os.utime(path, ns=stamps)
    assert lookup(song_a) == song_a.with_suffix(".lrc")
    assert lookup(song_b) == (song_b.with_suffix(".lrc") if change == "remove" else lyric)


def test_unchanged_files_do_not_reopen_lyrics_or_walk_folder(tmp_path, monkeypatch):
    Search._lyrics_folder_index_cache = {}
    (tmp_path / "song.lrc").write_text("[00:00.000]Text", encoding="utf-8")
    before = Search._get_lyrics_folder_index(tmp_path)

    def unexpected_io(*args, **kwargs):
        pytest.fail("Unchanged lyrics should only require metadata checks")

    monkeypatch.setattr(Path, "read_text", unexpected_io)
    monkeypatch.setattr(Path, "rglob", unexpected_io)
    assert Search._get_lyrics_folder_index(tmp_path) is before


def test_scan_started_before_invalidation_does_not_restore_stale_cache(tmp_path, monkeypatch):
    Search._lyrics_folder_index_cache = {}
    lyric = tmp_path / "song.lrc"
    lyric.write_text("[00:00.000]Text", encoding="utf-8")
    original_signature = Search._directory_signature

    def invalidate_during_scan(directories):
        Search.clear_lyrics_search_index()
        return original_signature(directories)

    monkeypatch.setattr(Search, "_directory_signature", invalidate_during_scan)
    assert Search._get_lyrics_folder_index(tmp_path)["entries"]
    assert not Search._lyrics_folder_index_cache
