from threading import Event

from core import stream_lyrics


def test_automatic_lyrics_require_exact_unambiguous_artist_and_title(monkeypatch):
    rows = [
        {"trackName": "Song (Live)", "artistName": "Artist", "plainLyrics": "Other version"},
        {"trackName": "Song", "artistName": "Wrong Artist", "plainLyrics": "Other artist"},
        {"trackName": " SONG ", "artistName": "ＡＲＴＩＳＴ", "plainLyrics": "First line\nSecond line"},
        {"trackName": "Song", "artistName": "Artist", "plainLyrics": "first line\nsecond line"},
    ]
    monkeypatch.setattr(stream_lyrics.lrclib, "search", lambda *_args, **_kwargs: rows)
    song = stream_lyrics.song_from_title(" Artist — Song ")
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ("First line", "Second line")
    rows.append({"trackName": "Song", "artistName": "Artist", "plainLyrics": "Different lyrics"})
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ()
    assert stream_lyrics.song_from_title("Station announcements") is None
