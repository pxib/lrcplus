import os

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
