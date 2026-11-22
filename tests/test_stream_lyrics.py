from threading import Event

from core import stream_lyrics


def test_automatic_lyrics_require_exact_unambiguous_artist_and_title(monkeypatch):
    rows = [
        {"trackName": "Song (Live)", "artistName": "Artist", "plainLyrics": "Other version"},
        {"trackName": "Song", "artistName": "Wrong Artist", "plainLyrics": "Other artist"},
        {"trackName": " SONG ", "artistName": "ＡＲＴＩＳＴ", "plainLyrics": "First line\nSecond line"},
        {"trackName": "Song", "artistName": "Artist", "plainLyrics": "first line\nsecond line"},
        {"trackName": "Song", "artistName": "Artist", "plainLyrics": "First line, second\nline!"},
    ]
    monkeypatch.setattr(stream_lyrics.lrclib, "search", lambda *_args, **_kwargs: rows)
    song = stream_lyrics.song_from_title(" Artist — Song ")
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ("First line", "Second line")
    rows.append({"trackName": "Song", "artistName": "Artist", "plainLyrics": "Different lyrics"})
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ()
    assert stream_lyrics.song_from_title("Station announcements") is None


def test_live_lookup_strips_timestamps_from_synced_only_result(monkeypatch):
    rows = [{
        "trackName": "Song", "artistName": "Artist", "plainLyrics": None,
        "syncedLyrics": "[ar:Artist]\n[00:01.00][00:20.00]Chorus\n[00:10.00]<00:10.00>Verse\n[00:21.00]",
    }]
    monkeypatch.setattr(stream_lyrics.lrclib, "search", lambda *_args, **_kwargs: rows)
    assert stream_lyrics.find_stream_lyrics(("Artist", "Song"), Event()) == ("Chorus", "Verse", "Chorus")


def test_truncated_title_requires_unique_completion_and_consistent_lyrics(monkeypatch):
    lyrics = "A long repeated chorus with a small missing word " * 8
    candidate = {"trackName": "Kaze ga Fuku Machi", "artistName": "Luck Life"}
    rows = [
        {**candidate, "plainLyrics": lyrics},
        {**candidate, "plainLyrics": lyrics.replace(" word", "", 1)},
        {**candidate, "plainLyrics": lyrics},
        {**candidate, "trackName": "Kaze ga Fuku Machi (TV size)", "plainLyrics": "Short edit"},
        {**candidate, "trackName": "Kaze ga Fuku Live", "plainLyrics": "Live recording"},
        {**candidate, "trackName": "Kaze ga Fuku Sora", "artistName": "Other Artist", "plainLyrics": "Different song"},
    ]
    monkeypatch.setattr(stream_lyrics.lrclib, "search", lambda *_args, **_kwargs: rows)
    song = ("LUCK LIFE", "Kaze ga Fuku")
    assert stream_lyrics.find_stream_lyrics(song, Event()) == (lyrics.strip(),)

    rows.append({**candidate, "trackName": "Kaze ga Fuku Sora", "plainLyrics": "Another match"})
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ()
    rows.append({**candidate, "trackName": "Kaze ga Fuku", "plainLyrics": "Exact match"})
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ("Exact match",)


def test_radio_title_can_include_a_confirmed_album(monkeypatch):
    base = {"trackName": "Zanzou Mirai", "artistName": "Suzuki Konomi"}
    rows = [
        {**base, "albumName": "CHOIR JAIL", "artistName": "Another artist", "plainLyrics": "Other artist"},
        {**base, "albumName": "Another release", "plainLyrics": "Other release"},
        {**base, "albumName": "choir jail", "plainLyrics": "Album lyrics"},
    ]
    queries = []

    def search(title, cancelled, *, artist):
        queries.append((title, artist))
        return rows

    monkeypatch.setattr(stream_lyrics.lrclib, "search", search)
    song = ("Suzuki Konomi", "Zanzou Mirai (CHOIR JAIL)")
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ("Album lyrics",)
    assert queries[-1] == ("Zanzou Mirai", "Suzuki Konomi")
    assert stream_lyrics.find_stream_lyrics((song[0], "Zanzou Mirai [CHOIR JAIL]"), Event()) == ("Album lyrics",)
    assert stream_lyrics.find_stream_lyrics((song[0], "Zanzou Mirai (Unknown Album)"), Event()) == ()

    rows.append({**base, "albumName": "Live", "plainLyrics": "Conflicting edition"})
    assert stream_lyrics.find_stream_lyrics((song[0], "Zanzou Mirai (Live)"), Event()) == ()
    assert queries[-1] == ("Zanzou Mirai (Live)", "Suzuki Konomi")
    rows.append({**base, "trackName": song[1], "plainLyrics": "Exact track title"})
    assert stream_lyrics.find_stream_lyrics(song, Event()) == ("Exact track title",)
