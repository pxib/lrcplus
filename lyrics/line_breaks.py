"""LRCX visual breaks, including explicitly marked translation rows."""

import re


_BREAK = re.compile(r"\{\\n(t?)\}|\n")


def decode_line_breaks(markup):
    translation_lines = []
    row = 0

    def replace(match):
        nonlocal row
        row += 1
        if match.group(1) == "t":
            translation_lines.append(row)
        return "\n"

    return _BREAK.sub(replace, markup), translation_lines


def encode_line_breaks(text, translation_lines=(), first_row=0):
    row = first_row

    def replace(match):
        nonlocal row
        row += 1
        return r"{\nt}" if row in translation_lines else r"{\n}"

    return re.sub("\n", replace, text)


def reading_source(text, translation_lines=()):
    """Keep translation metadata in reading cache keys without changing offsets."""
    rows = str(text or "").split("\n")
    return "".join(
        (r"{\nt}" if row in translation_lines else "\n") + value
        if row else value
        for row, value in enumerate(rows)
    )


def original_reading_text(source):
    text, translations = decode_line_breaks(source)
    return "\n".join(value for row, value in enumerate(text.split("\n"))
                     if row not in translations)
