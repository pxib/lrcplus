"""Isolated PyCantonese worker for LyricsPlus."""
from __future__ import annotations

import contextlib
import io
import json
import sys


def convert_words(text, style):
    # Some PyCantonese/wordseg versions (or their dependencies) can write to
    # stdout. The worker protocol uses stdout exclusively for JSON, so capture
    # *all* third-party stdout and leave only the final JSON response on stdout.
    noise = io.StringIO()
    with contextlib.redirect_stdout(noise):
        import pycantonese

        text = str(text or "")
        if not text.strip():
            return []

        words = pycantonese.characters_to_jyutping(text)
        result, position = [], 0
        for surface, jyutping in words:
            surface = str(surface or "")
            if not surface:
                continue
            found = text.find(surface, position)
            if found < 0:
                found = position
            if found > position:
                result.append((text[position:found], None))
            position = found + len(surface)
            if not jyutping:
                result.append((surface, None))
                continue

            try:
                syllables = [str(x) for x in pycantonese.parse_jyutping(str(jyutping))]
            except Exception:
                syllables = []

            if style == "yale":
                try:
                    readings = pycantonese.jyutping_to_yale(str(jyutping))
                except TypeError:
                    readings = pycantonese.jyutping_to_yale(
                        str(jyutping), return_as="list"
                    )
                if isinstance(readings, str):
                    readings = readings.split()
                readings = list(readings)
            else:
                readings = syllables

            if len(readings) == len(surface):
                result.extend(zip(surface, readings))
            else:
                result.append((surface, " ".join(readings) if readings else None))

        if position < len(text):
            result.append((text[position:], None))
        return result


def main():
    # Keep request/response handling outside the captured region. stdout must
    # contain exactly one JSON document; diagnostics belong on stderr.
    request = json.load(sys.stdin)
    texts = [str(value or "") for value in request.get("texts", [])]
    style = str(request.get("style") or "jyutping")

    result = []
    for text in texts:
        try:
            result.append(convert_words(text, style))
        except BaseException as error:
            # Never let one bad line prevent a valid response for the batch.
            print(f"Cantonese worker line failed: {text!r}: {error!r}", file=sys.stderr)
            result.append([])

    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
