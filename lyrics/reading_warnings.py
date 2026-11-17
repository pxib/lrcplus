"""Describe English words left without pronunciation annotations."""
import re


_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def missing_english_readings(tokens, translation_lines=()):
    text = "".join(str(token.get("text", "")) for token in tokens)
    covered = []
    for token in tokens:
        covered.extend([bool(token.get("reading"))] * len(str(token.get("text", ""))))
    missing = {}
    offset = 0
    for row, value in enumerate(text.split("\n")):
        if row not in translation_lines:
            for match in _WORD.finditer(value):
                word = match.group().replace("’", "'")
                if word.isascii() and not all(covered[offset + match.start():offset + match.end()]):
                    missing.setdefault(word.casefold(), match.group())
        offset += len(value) + 1
    return list(missing.values())


def unconverted_words_message(words):
    unique = {}
    for word in words:
        unique.setdefault(word.replace("’", "'").casefold(), word)
    words = list(unique.values())
    if not words:
        return ""
    preview = ", ".join(words[:20])
    if len(words) > 20:
        preview += f" (and {len(words) - 20} more)"
    return (
        f"\n\n{len(words)} English word(s) could not be converted to katakana: {preview}."
        "\nAdd readings manually or through Reading Dictionaries."
    )
