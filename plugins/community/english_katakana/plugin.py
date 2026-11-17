"""Opt-in English katakana readings from alkana's offline dictionary."""
from __future__ import annotations

import re
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


# Match whole words before checking for English letters, so an accented word
# such as café cannot accidentally receive the reading for its ASCII prefix.
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def build_segments(text, lookup):
    text = str(text or "")
    segments = []
    position = 0
    for match in _WORD.finditer(text):
        if match.start() > position:
            segments.append({"text": text[position:match.start()], "reading": None})
        surface = match.group(0)
        word = surface.replace("’", "'")
        reading = lookup(word.lower()) if word.isascii() else None
        segments.append({"text": surface, "reading": reading or None})
        position = match.end()
    if position < len(text):
        segments.append({"text": text[position:], "reading": None})
    return segments


def _isolated_convert(texts):
    values = [str(text or "") for text in texts]
    if not values:
        return []
    python = sys.executable
    if getattr(sys, "frozen", False):
        python = os.environ.get("LRCPLUS_DEPENDENCY_PYTHON")
        if not python or not Path(python).is_file():
            raise RuntimeError("Install English Katakana dependencies and restart LRCPlus to prepare its private Python runtime")
    with tempfile.TemporaryDirectory(prefix="lrcplus-alkana-") as directory:
        request = Path(directory) / "request.json"
        response = Path(directory) / "response.json"
        request.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            [python, "-I", str(Path(__file__).with_name("worker.py")),
             "--input", str(request), "--output", str(response)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=60, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"English Katakana worker failed: {detail}")
        result = json.loads(response.read_text(encoding="utf-8"))
        if not isinstance(result, list) or len(result) != len(values):
            raise RuntimeError("English Katakana worker returned an invalid result")
        for text, segments in zip(values, result):
            if (not isinstance(segments, list)
                    or any(not isinstance(part, dict)
                           or not isinstance(part.get("text"), str)
                           or (part.get("reading") is not None and not isinstance(part["reading"], str))
                           for part in segments)
                    or "".join(part["text"] for part in segments) != text):
                raise RuntimeError("English Katakana worker did not preserve lyric text")
        return result


class EnglishKatakanaPlugin:
    name = "English Katakana"
    version = "1.0.1"
    description = "Optional offline English pronunciation readings using alkana."

    def on_load(self, context):
        # Deliberately omit can_handle: Auto must not start annotating English
        # merely because this optional dependency has been installed.
        context.api.register_reading_provider(
            "english_katakana",
            "English → Katakana (alkana)",
            lambda text: _isolated_convert([text])[0],
            batch_generator=lambda texts: _isolated_convert(texts),
            description=(
                "Offline English katakana dictionary readings. "
                "Select explicitly; unknown words have no generated reading."
            ),
        )


PLUGIN_CLASS = EnglishKatakanaPlugin
