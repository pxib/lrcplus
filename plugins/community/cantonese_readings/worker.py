"""Isolated PyCantonese worker for LyricsPlus.

The worker communicates through files rather than stdout. This is intentional:
PyCantonese/wordseg versions can emit diagnostic text, and a crashed or noisy
child must never corrupt the application's response protocol.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import traceback


def convert_words(text, style):
    # Capture Python-level stdout from PyCantonese/wordseg. The actual response
    # is written to the output file by main(), so even uncaptured native output
    # cannot corrupt the protocol.
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as handle:
            request = json.load(handle)

        texts = [str(value or "") for value in request.get("texts", [])]
        style = str(request.get("style") or "jyutping")

        result = []
        for text in texts:
            try:
                result.append(convert_words(text, style))
            except BaseException as error:
                # A normal Python exception is isolated to this line. Fatal
                # native crashes still terminate only this child process.
                print(
                    f"Cantonese worker line failed: {text!r}: {error!r}",
                    file=__import__("sys").stderr,
                )
                result.append([])

        # Write a complete JSON document only after the whole request has been
        # processed. The parent can distinguish a missing/partial file from a
        # valid response without depending on stdout.
        temp_output = args.output + ".tmp"
        with open(temp_output, "w", encoding="utf-8", newline="") as handle:
            json.dump(result, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
        import os
        os.replace(temp_output, args.output)
    except BaseException:
        # Diagnostics are deliberately stderr-only.
        traceback.print_exc(file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
