import pytest

from PySide6.QtCore import QCoreApplication, QEvent

from core.favorites import favorite_key
from core.library_search import close_metadata_match, search_words
from core.music_library import LibraryTrack
from ui.main_window import Player


@pytest.mark.parametrize("query", [
    "Hatsne Miku",       # Missing letter.
    "Hatsunne Miku",     # Extra letter.
    "Hatzune Miku",      # Wrong letter.
    "Hatusne Miku",      # Adjacent swapped letters.
    "HATSNE MIKU",
    "hatsne mik",        # Keep literal partial-word matches.
    "northern ligths",   # Album metadata.
    "starligth",         # Title metadata.
])
def test_one_typo_in_track_metadata(query):
    track = LibraryTrack("C:/music/track.mp3", "Starlight", artist="Hatsune Miku", album="Northern Lights")
    assert close_metadata_match(search_words(query), track)


@pytest.mark.parametrize("query", [
    "hatsne mikuu",      # Two misspelled words.
    "hatsn miku",        # Two missing letters.
    "mki",              # Short searches stay strict.
    "hatsne rin",        # Every other word must match.
    "20266",             # Dates are not fuzzy metadata.
    "speciall",          # Paths are not fuzzy metadata.
    "?!", "",           # No searchable words.
])
def test_typo_fallback_is_conservative(query):
    track = LibraryTrack("C:/special/track.mp3", "Starlight", artist="Hatsune Miku", date="2026")
    assert not close_metadata_match(search_words(query), track)


def test_numeric_words_require_literal_match():
    track = LibraryTrack("C:/music/track.mp3", "Song 12345")
    assert not close_metadata_match(search_words("song 12346"), track)
    assert close_metadata_match(search_words("snog 12345"), track)


def test_unicode_metadata_and_album_artist_are_searchable():
    track = LibraryTrack("C:/music/track.mp3", "夜空を駆ける", album_artist="Beyoncé")
    assert close_metadata_match(search_words("夜空を掛ける"), track)
    assert close_metadata_match(search_words("Beyonce"), track)
    assert not close_metadata_match(search_words("yozora"), track)


@pytest.fixture
def library(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Player, "_restore_startup_state", lambda self: None)
    window = Player()
    qapp.processEvents()
    window.library_tracks = [
        LibraryTrack(str(tmp_path / "first.mp3"), "Starlight", artist="Hatsune Miku", album="Northern Lights"),
        LibraryTrack(str(tmp_path / "second.mp3"), "Love Story", artist="Taylor Swift", album="Fearless"),
    ]
    window._populate_library_view()
    yield window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def search(window, query):
    window.library_search.setText(query)
    window._filter_library_view()
    return [item.track.title for parent, rows in window._library_filter_groups
            for item, _ in rows if not parent.isHidden() and not item.isHidden()]


def test_typo_results_are_labeled_and_clearing_restores_albums(library):
    parent = library.library_table.topLevelItem(0)
    parent.setExpanded(False)
    assert search(library, "hatsne miku") == ["Starlight"]
    assert parent.isExpanded()
    assert not library.library_close_matches.isHidden()
    assert library.library_close_matches.text() == "Close matches"
    assert search(library, "love") == ["Love Story"]
    assert library.library_close_matches.isHidden()
    assert len(search(library, "")) == 2
    assert not parent.isExpanded()
    assert library.library_close_matches.isHidden()
    assert search(library, "nothing remotely similar") == []
    assert library.library_close_matches.isHidden()


def test_ordinary_matches_take_priority_without_running_fallback(library, monkeypatch):
    library.library_tracks.append(LibraryTrack("C:/music/third.mp3", "Hatsne Miku"))
    library._populate_library_view()

    def unexpected(*args):
        pytest.fail("Ordinary results must not run typo matching")

    monkeypatch.setattr("ui.player_library.close_metadata_match", unexpected)
    assert search(library, "hatsne miku") == ["Hatsne Miku"]
    assert library.library_close_matches.isHidden()


def test_favorites_constrain_both_normal_and_close_matches(library):
    first, second = library.library_tracks
    library.favorite_paths = {favorite_key(first.path)}
    library.library_favorites_filter.setChecked(True)
    assert search(library, "hatsne miku") == ["Starlight"]
    assert not library.library_close_matches.isHidden()
    assert search(library, "lovve story") == []
    assert library.library_close_matches.isHidden()
    # An exact result outside Favorites must not suppress a close favorite.
    second.title = "Hatsne Miku"
    assert search(library, "hatsne miku") == ["Starlight"]
    library.library_favorites_filter.setChecked(False)
    assert search(library, "hatsne miku") == ["Hatsne Miku"]
    assert library.library_close_matches.isHidden()


def test_lyrics_mode_never_uses_metadata_typo_fallback(library, monkeypatch):
    monkeypatch.setattr(library, "_search_library_lyrics", lambda query: {})
    library.library_search_filter.setCurrentIndex(1)
    assert search(library, "hatsne miku") == []
    assert library.library_close_matches.isHidden()
    library.library_search_filter.setCurrentIndex(0)
    assert search(library, "hatsne miku") == ["Starlight"]


def test_rescan_and_metadata_changes_refresh_close_matches(library):
    assert search(library, "hatsne miku") == ["Starlight"]
    library._populate_library_view()
    assert not library.library_close_matches.isHidden()
    library.library_tracks[0].artist = "Megurine Luka"
    assert search(library, "hatsne miku") == []
    assert library.library_close_matches.isHidden()
    assert search(library, "megurine lukaa") == ["Starlight"]
