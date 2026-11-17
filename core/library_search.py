"""Small, on-demand typo fallback for music-library metadata."""

import re


_WORDS = re.compile(r"[^\W_]+")


def search_words(query):
    return _WORDS.findall(query.casefold())


def _one_typo(first, second):
    """Accept one insertion, deletion, substitution, or adjacent swap."""
    if abs(len(first) - len(second)) > 1:
        return False
    if len(first) == len(second):
        differences = [i for i, (a, b) in enumerate(zip(first, second)) if a != b]
        if len(differences) <= 1:
            return True
        if len(differences) != 2:
            return False
        a, b = differences
        return b == a + 1 and first[a] == second[b] and first[b] == second[a]
    if len(first) > len(second):
        first, second = second, first
    for i, (a, b) in enumerate(zip(first, second)):
        if a != b:
            return first[i:] == second[i + 1:]
    return True


def close_metadata_match(words, track):
    """Allow one misspelled word; other query words must match literally."""
    fields = tuple(value.casefold() for value in (
        track.display_title, track.artist, track.album, track.album_artist,
    ) if value)
    missing = [word for word in words if not any(word in field for field in fields)]
    if len(missing) != 1:
        return False
    word = missing[0]
    if len(word) < 4 or word.isdecimal():
        return False
    return any(_one_typo(word, candidate)
               for field in fields for candidate in _WORDS.findall(field))
