import pytest

from core.lyrics_search import LyricsSearchMixin


class Search(LyricsSearchMixin):
    _lyrics_folder_index_cache = {}


def find(song, folders=(), attachments=None):
    return Search().find_lrc_for_audio(
        song, configured_folders=folders, attachments=attachments or {},
    )


@pytest.mark.parametrize("version", ["Best Friend Remix", "Live", "Acoustic", "Instrumental"])
@pytest.mark.parametrize("play_version", [False, True])
def test_automatic_matching_keeps_recordings_separate(tmp_path, version, play_version):
    original = tmp_path / "DECO27 - モニタリング.lrc"
    alternate = tmp_path / f"DECO27 - モニタリング ({version}).lrcx"
    for lyric in (original, alternate):
        lyric.write_text("[00:01]Text", encoding="utf-8")
    expected = alternate if play_version else original
    song = expected.with_suffix(".mp3")
    assert find(song) == expected
    # A cached lookup must make the same choice on subsequent plays.
    assert find(song) == expected


@pytest.mark.parametrize("song_version,lyric_version", [
    ("", " (Best Friend Remix)"),
    (" (Best Friend Remix)", ""),
    (" (Best Friend Remix)", " (Other Remix)"),
    ("", " - Live"),
])
def test_different_version_is_not_a_substring_fallback(tmp_path, song_version, lyric_version):
    song = tmp_path / f"Artist - Song{song_version}.mp3"
    lyric = tmp_path / f"Artist - Song{lyric_version}.lrcx"
    lyric.write_text("[00:01]Text", encoding="utf-8")
    assert find(song) == song.with_suffix(".lrc")


def test_exact_filename_wins_over_decorated_lrcx_in_same_folder(tmp_path):
    song = tmp_path / "Artist - Song.mp3"
    exact = song.with_suffix(".lrc")
    decorated = tmp_path / "Artist - Song (Official Audio).lrcx"
    for lyric in (exact, decorated):
        lyric.write_text("[00:01]Text", encoding="utf-8")
    assert find(song) == exact


@pytest.mark.parametrize("name", [
    "Artist - Song (Official Audio)",
    "01 - Artist - Song [Lyrics]",
    "Song - Artist",
    "Song",
])
def test_presentation_labels_and_artist_order_still_match(tmp_path, name):
    song = tmp_path / "Artist - Song.mp3"
    lyric = tmp_path / (name + ".lrcx")
    lyric.write_text("[00:01]Text", encoding="utf-8")
    assert find(song) == lyric


@pytest.mark.parametrize("force", ["settings", "tag"])
def test_explicit_association_can_override_recording_version(tmp_path, force):
    song = tmp_path / "Artist - Song.mp3"
    lyric = tmp_path / "Artist - Song (Best Friend Remix).lrcx"
    tag = f"[attach:{song}]\n" if force == "tag" else ""
    lyric.write_text(tag + "[00:01]Text", encoding="utf-8")
    attachments = {Search._normalise_path_key(song): str(lyric)} if force == "settings" else {}
    assert find(song, attachments=attachments) == lyric
