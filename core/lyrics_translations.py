"""Attach provider translations at matching line timestamps without retiming lyrics."""

from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import re

from lyrics.formatting import format_emphasis
from core.native_lyrics import timestamp_ms


_STAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_END = re.compile(r"<(\d+:\d+(?:\.\d+)?)>$")


def _milliseconds(match):
    if len(match[0]) > 32:
        return None
    value = (Decimal(match[1]) * 60 + Decimal(match[2])) * 1000
    if value > 10_000_000_000:
        return None
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def timed_translations(text):
    """Ignore untimed/empty translations and ambiguous duplicate timestamps."""
    rows, ambiguous = {}, set()
    for line in str(text or "").splitlines():
        stamps = list(_STAMP.finditer(line))
        if not stamps or stamps[0].start() != 0:
            continue
        body = line[stamps[-1].end():].strip()
        if not body or body == "//":
            continue
        for stamp in stamps:
            start = _milliseconds(stamp)
            if start is None:
                continue
            if start in rows and rows[start] != body:
                ambiguous.add(start)
            rows[start] = body
    return {start: body for start, body in rows.items() if start not in ambiguous}


def with_translations(result, *candidates):
    """Keep original-only output and cache the best aligned bilingual variant."""
    original = result.get("syncedLyrics")
    if not original or not any(candidates):
        return result
    word_timed = result.get("lyricsFormat") == "lrcx"
    rows = []
    for line in original.splitlines():
        stamps = list(_STAMP.finditer(line))
        if not stamps or stamps[0].start() != 0:
            continue
        body = line[stamps[-1].end():]
        for stamp in stamps:
            start = _milliseconds(stamp)
            if start is None:
                return result
            rows.append((start, body))
    counts = Counter(start for start, _body in rows)
    best, best_count, best_rows = None, 0, {}
    for translations in candidates:
        output, matched, matched_rows = [], 0, {}
        for start, body in rows:
            prefix = f"[{timestamp_ms(start)}]"
            lyric = body if word_timed else format_emphasis(body, [])
            translated = translations.get(start) if counts[start] == 1 else None
            end = _END.search(body) if word_timed else None
            if translated and body.strip() and (not word_timed or end):
                translation = format_emphasis(translated.replace("\r\n", "\n").replace("\r", "\n"), [])
                translation = translation.replace("\n", r"{\nt}")
                if word_timed:
                    # Keep the original terminal boundary explicit, then reset
                    # to the line start so translation text doesn't sweep late.
                    lyric += (f"<{end[1]}>" + r"{\nt}"
                              + f"<{timestamp_ms(start)}><instant>{translation}<{end[1]}>")
                else:
                    lyric += r"{\nt}" + translation
                matched += 1
                matched_rows[start] = translated
            output.append(prefix + lyric)
        if matched > best_count:
            best, best_count = "\n".join(output), matched
            best_rows = matched_rows
    if not best_count:
        return result
    return {**result, "translatedLyrics": best, "translationCount": best_count,
            "translationRows": best_rows}


@lru_cache(maxsize=2)
def _chinese_converter(script):
    from opencc import OpenCC

    return OpenCC({"simplified": "t2s", "traditional": "s2t"}[script])


@lru_cache(maxsize=2048)
def _convert_translation(text, script):
    return _chinese_converter(script).convert(text)


def converted_translations(result, script="provided"):
    """Always convert from provider text, preserving originals and LRCX markup."""
    if script == "provided" or not result.get("translationRows"):
        return result
    if script not in {"simplified", "traditional"}:
        raise ValueError("Unknown Chinese translation script.")
    rows = {start: _convert_translation(text, script)
            for start, text in result["translationRows"].items()}
    return with_translations(result, rows)


def translation_preview(result):
    """Show only matched provider translation rows in their original order."""
    return "\n".join(f"[{timestamp_ms(start)}]{text}"
                     for start, text in result.get("translationRows", {}).items())
