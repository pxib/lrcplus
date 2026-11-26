"""Literal word and typo matching for music-library metadata."""

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


def _metadata_fields(track):
    return tuple(value.casefold() for value in (
        track.display_title, track.artist, track.album, track.album_artist,
    ) if value)


def literal_metadata_match(words, track):
    """Match complete metadata words in any order."""
    candidates = {candidate for field in _metadata_fields(track) for candidate in _WORDS.findall(field)}
    return candidates.issuperset(words)


def _matches_typo(word, candidate):
    if _one_typo(word, candidate):
        return True
    if len(word) < 6:
        return False
    return any(_one_typo(word, candidate[:length])
               for length in range(len(word) - 1, len(word) + 2) if length < len(candidate))


def close_metadata_match(words, track):
    """Allow one misspelled word while keeping the other words literal."""
    fields = _metadata_fields(track)
    missing = [word for word in words if not any(word in field for field in fields)]
    if len(missing) != 1:
        return False
    word = missing[0]
    if len(word) < 3 or word.isdecimal():
        return False
    candidates = {candidate for field in fields for candidate in _WORDS.findall(field)}
    if len(word) == 3:
        anchored = any(len(other) >= 3 and other in candidates for other in words)
        return word.isalpha() and anchored and any(
            len(candidate) == 3 and candidate.isalpha()
            and sum(a != b for a, b in zip(word, candidate)) == 1
            for candidate in candidates
        )
    return any(_matches_typo(word, candidate) for candidate in candidates)
