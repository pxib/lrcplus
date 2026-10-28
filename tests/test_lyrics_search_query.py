from types import SimpleNamespace

import ui.main_window as main_window


def test_lyrics_search_query_prefers_artist_and_title_metadata(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "misleading-filename.mp3"
    monkeypatch.setattr(
        main_window,
        "read_track",
        lambda _path: SimpleNamespace(
            artist="Tagged Artist",
            title="Tagged Song",
        ),
    )

    assert main_window._lyrics_search_query(audio_path) == (
        "Tagged Artist - Tagged Song"
    )


def test_lyrics_search_query_falls_back_to_filename_without_tags(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "filename-title.mp3"
    monkeypatch.setattr(
        main_window,
        "read_track",
        lambda _path: SimpleNamespace(artist="", title=audio_path.stem),
    )

    assert main_window._lyrics_search_query(audio_path) == "filename-title"


def test_lyrics_search_query_falls_back_when_metadata_cannot_be_read(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "filename-title.mp3"

    def unreadable_track(_path):
        raise OSError("unreadable audio file")

    monkeypatch.setattr(main_window, "read_track", unreadable_track)

    assert main_window._lyrics_search_query(audio_path) == "filename-title"