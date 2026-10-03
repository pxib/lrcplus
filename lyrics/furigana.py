"""Japanese furigana generation via the Yomi API.

The app intentionally keeps this module dependency-light: requests is enough.
If the service is unavailable, callers receive the original text without ruby
instead of failing or requiring a 248 MB local dictionary.
"""
import re
import threading
import unicodedata
from collections import OrderedDict
import json
import math
import requests

from lyrics.reading_providers import (
    available_reading_providers,
    generate_reading_segments,
    generate_reading_segments_batch,
    find_reading_provider,
)

try:
    from pypinyin import Style, pinyin as _pypinyin
except Exception:
    Style = None
    _pypinyin = None

_ACTIVE_PARSER = "yomi"
_MECAB_PARSE_MODE = "batch"
_MECAB_TAGGER = None
_MECAB_TAGGER_LOCK = threading.Lock()

def set_mecab_parse_mode(mode):
    global _MECAB_PARSE_MODE
    mode = str(mode or "batch").lower()
    _MECAB_PARSE_MODE = mode if mode in {"batch", "line"} else "batch"

def get_mecab_parse_mode():
    return _MECAB_PARSE_MODE
_READING_MODE = "auto"
_AUTO_LANGUAGE_CONTEXT = None

# Per-song gate for expensive automatic reading providers.
_AUTOMATIC_READINGS_ENABLED = True

def set_automatic_readings_enabled(enabled):
    global _AUTOMATIC_READINGS_ENABLED
    _AUTOMATIC_READINGS_ENABLED = bool(enabled)

def automatic_readings_enabled():
    return _AUTOMATIC_READINGS_ENABLED

def set_reading_mode(mode):
    """Select automatic reading generation: auto, furigana, pinyin, or an optional provider."""
    global _READING_MODE
    mode = str(mode or "auto").lower()
    if mode not in {"auto", "furigana", "pinyin"} and mode not in available_reading_providers():
        mode = "auto"
    if mode != _READING_MODE:
        _READING_MODE = mode
        with _SEGMENT_CACHE_LOCK:
            _SEGMENT_CACHE.clear()

def get_reading_mode():
    return _READING_MODE

def set_auto_language_context(language):
    """Set the resolved language for Auto mode for the current lyric collection.

    Han-only lines are ambiguous by themselves. Once the surrounding lyrics
    establish Japanese or Chinese context, every ambiguous line should inherit
    that decision instead of being independently classified as Chinese.
    """
    global _AUTO_LANGUAGE_CONTEXT
    language = str(language or "").lower()
    if language not in {"japanese", "chinese"}:
        language = None
    if language != _AUTO_LANGUAGE_CONTEXT:
        _AUTO_LANGUAGE_CONTEXT = language
        with _SEGMENT_CACHE_LOCK:
            _SEGMENT_CACHE.clear()

def get_auto_language_context():
    return _AUTO_LANGUAGE_CONTEXT

def available_reading_modes():
    modes = {
        "auto": "Auto",
        "furigana": "Furigana (Japanese)",
        "pinyin": "Pinyin (Mandarin)",
    }
    modes.update(available_reading_providers())
    return modes

def set_active_parser(parser):
    global _ACTIVE_PARSER
    parser = str(parser or "yomi").lower()
    _ACTIVE_PARSER = parser if parser in {"yomi", "mecab_local"} else "yomi"

def get_active_parser():
    return _ACTIVE_PARSER

def available_parsers():
    return {
        "yomi": {"name": "Yomi", "available": True, "online": True},
        "mecab_local": {
            "name": "MeCab (local)",
            "available": _mecab_available(),
            "online": False,
        },
    }

def _mecab_available():
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
        return True
    except Exception:
        return False


YOMI_URL = "https://yomi.onrender.com/analyze"
# Render-hosted Yomi can take longer than a few seconds, especially after
# idling. The old 6-second read timeout was too aggressive for a cold start.
YOMI_TIMEOUT = (5.0, 30.0)
YOMI_RETRIES = 2

_YOMI_CACHE = OrderedDict()
_YOMI_CACHE_LIMIT = 1000
_CACHE_LOCK = threading.Lock()

# Several lyric widgets/workers can request analyses at once. Serializing the
# remote calls prevents a newly opened lyric file from hammering the small API
# with many simultaneous POST requests.
_YOMI_REQUEST_LOCK = threading.Lock()



# ASCII Hepburn-style kana romanization used for display-only romaji.
# This intentionally operates on an already-resolved reading so kanji
# ambiguity is handled by the furigana generator, not by the romanizer.
_KANA_ROMAJI = {
    "あ":"a","い":"i","う":"u","え":"e","お":"o",
    "か":"ka","き":"ki","く":"ku","け":"ke","こ":"ko",
    "さ":"sa","し":"shi","す":"su","せ":"se","そ":"so",
    "た":"ta","ち":"chi","つ":"tsu","て":"te","と":"to",
    "な":"na","に":"ni","ぬ":"nu","ね":"ne","の":"no",
    "は":"ha","ひ":"hi","ふ":"fu","へ":"he","ほ":"ho",
    "ま":"ma","み":"mi","む":"mu","め":"me","も":"mo",
    "や":"ya","ゆ":"yu","よ":"yo",
    "ら":"ra","り":"ri","る":"ru","れ":"re","ろ":"ro",
    "わ":"wa","を":"wo","ん":"n",
    "が":"ga","ぎ":"gi","ぐ":"gu","げ":"ge","ご":"go",
    "ざ":"za","じ":"ji","ず":"zu","ぜ":"ze","ぞ":"zo",
    "だ":"da","ぢ":"ji","づ":"zu","で":"de","ど":"do",
    "ば":"ba","び":"bi","ぶ":"bu","べ":"be","ぼ":"bo",
    "ぱ":"pa","ぴ":"pi","ぷ":"pu","ぺ":"pe","ぽ":"po",
    "ゔ":"vu",
    "ぁ":"a","ぃ":"i","ぅ":"u","ぇ":"e","ぉ":"o",
}
_KANA_DIGRAPHS = {
    "きゃ":"kya","きゅ":"kyu","きょ":"kyo",
    "ぎゃ":"gya","ぎゅ":"gyu","ぎょ":"gyo",
    "しゃ":"sha","しゅ":"shu","しょ":"sho",
    "じゃ":"ja","じゅ":"ju","じょ":"jo",
    "ちゃ":"cha","ちゅ":"chu","ちょ":"cho",
    "にゃ":"nya","にゅ":"nyu","にょ":"nyo",
    "ひゃ":"hya","ひゅ":"hyu","ひょ":"hyo",
    "びゃ":"bya","びゅ":"byu","びょ":"byo",
    "ぴゃ":"pya","ぴゅ":"pyu","ぴょ":"pyo",
    "みゃ":"mya","みゅ":"myu","みょ":"myo",
    "りゃ":"rya","りゅ":"ryu","りょ":"ryo",
    "てぃ":"ti","でぃ":"di","とぅ":"tu","どぅ":"du",
    "うぃ":"wi","うぇ":"we","うぉ":"wo",
    "ゔぁ":"va","ゔぃ":"vi","ゔぇ":"ve","ゔぉ":"vo",
}

def kana_to_romaji(text):
    """Convert hiragana/katakana readings to simple ASCII romaji.

    Non-kana text is preserved.  The conversion is deliberately display-only:
    no attempt is made to infer how kanji should be read.
    """
    text = katakana_to_hiragana(str(text or ""))
    out = []
    i = 0
    sokuon = False
    while i < len(text):
        ch = text[i]
        if ch == "っ":
            sokuon = True
            i += 1
            continue
        if ch == "ー":
            # Repeat the most recent vowel for a katakana long-vowel mark.
            joined = "".join(out)
            last_vowel = next((c for c in reversed(joined) if c in "aeiou"), "")
            if last_vowel:
                out.append(last_vowel)
            i += 1
            continue

        pair = text[i:i+2]
        roma = _KANA_DIGRAPHS.get(pair)
        consumed = 2 if roma else 1
        if not roma:
            roma = _KANA_ROMAJI.get(ch)

        if roma is None:
            # Preserve punctuation, spaces, Latin text, emoji, etc.
            if sokuon and ch.strip():
                sokuon = False
            out.append(ch)
            i += 1
            continue

        if sokuon:
            # Geminate the first consonant.  Don't geminate vowel-initial
            # morae; apostrophe handling for ん is dealt with separately.
            first = roma[0]
            if first.lower() in "bcdfghjklmnpqrstvwxyz":
                out.append(first)
            sokuon = False

        # Render ん as n' before vowel/y to avoid ambiguity (e.g. しんよう).
        if ch == "ん":
            next_text = text[i+consumed:i+consumed+1]
            next_romaji = _KANA_ROMAJI.get(next_text, "")
            if next_romaji and next_romaji[0] in "aeiouy":
                roma = "n'"

        out.append(roma)
        i += consumed
    return "".join(out)


def is_kanji(char):
    return "\u3400" <= char <= "\u9fff"


def _is_hanzi(char):
    return (
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
    )


def _is_latin_letter(char):
    """Return whether *char* belongs to the Latin script.

    ``str.isalpha()`` alone would classify Cyrillic, Greek, etc. as Latin,
    so use the Unicode character name to keep this a script-level check.
    """
    if not char or not char.isalpha():
        return False
    return "LATIN" in unicodedata.name(char, "")


def detect_lyrics_language(text):
    """Detect the script category relevant to lyric rendering/generation.

    Built-in CJK handling keeps its existing priority. If no CJK evidence is
    present, Latin-script text is identified separately instead of falling into
    ``unknown``. Unsupported or symbol-only text remains ``unknown`` so
    existing provider fallback behavior stays intact.
    """
    text = str(text or "")
    has_hanzi = False
    has_latin = False

    for char in text:
        if "\u3040" <= char <= "\u309f" or "\u30a0" <= char <= "\u30ff":
            return "japanese"
        if _is_hanzi(char):
            has_hanzi = True
        elif _is_latin_letter(char):
            has_latin = True

    if has_hanzi and _AUTO_LANGUAGE_CONTEXT:
        return _AUTO_LANGUAGE_CONTEXT
    if has_hanzi:
        return "chinese"
    return "latin" if has_latin else "unknown"


def detect_lyrics_collection_language(lines):
    """Detect the dominant built-in script category across a lyric collection.

    CJK evidence keeps priority over Latin so mixed lines such as
    ``Hello 世界`` continue to use the existing CJK-aware behavior.
    """
    has_hanzi = False
    has_latin = False

    for value in lines:
        for char in str(value or ""):
            if "\u3040" <= char <= "\u309f" or "\u30a0" <= char <= "\u30ff":
                return "japanese"
            if _is_hanzi(char):
                has_hanzi = True
            elif _is_latin_letter(char):
                has_latin = True

    if has_hanzi:
        return "chinese"
    return "latin" if has_latin else "unknown"


def is_japanese_candidate(char):
    return (
        is_kanji(char)
        or "\u3040" <= char <= "\u309f"
        or "\u30a0" <= char <= "\u30ff"
    )


def is_kana(text):
    return bool(text) and all(
        "\u3040" <= char <= "\u309f"
        or "\u30a0" <= char <= "\u30ff"
        or char == "\u30fc"
        for char in text
    )


def katakana_to_hiragana(text):
    return "".join(
        chr(ord(char) - 0x60)
        if "\u30a1" <= char <= "\u30f6"
        else char
        for char in text
    )


def split_reading_by_text_ranges(text, reading, ranges):
    """Split one token reading across sub-ranges of its source text.

    Karaoke boundaries are independent from furigana-token boundaries, so a
    single ruby-bearing token may be divided into several timed pieces.  In
    that case we keep the reading instead of silently dropping it.  For
    fixed-width character mappings (for example 何千回/なんぜんかい), this
    also gives the intuitive per-piece distribution.

    The mapping is necessarily heuristic when a token contains a different
    number of source characters and reading characters (e.g. 今日/きょう).
    Those cases are uncommon for already-tokenized ruby segments; preserving
    the full reading on the first piece would be less useful than a stable
    proportional split for karaoke display.
    """
    text = str(text or "")
    reading = katakana_to_hiragana(str(reading or ""))
    if not reading or not text:
        return [None for _ in ranges]

    total_text = len(text)
    total_reading = len(reading)
    result = []
    previous = 0

    for start, end in ranges:
        start = max(0, min(total_text, int(start)))
        end = max(start, min(total_text, int(end)))
        r0 = math.floor(total_reading * start / total_text)
        r1 = math.floor(total_reading * end / total_text)
        r0 = max(previous, min(total_reading, r0))
        r1 = max(r0, min(total_reading, r1))
        piece = reading[r0:r1]
        result.append(piece or None)
        previous = r1

    return result


def token_to_furigana_segments(surface, reading):
    """Map a tokenizer's reading onto the kanji portions of one token."""

    reading = katakana_to_hiragana(reading)

    if not reading or reading == "*" or not any(map(is_kanji, surface)):
        return [{"text": surface, "reading": None}]

    # Handle repetition mark tokens such as 図々しく.  The okurigana (しく)
    # belongs after the repeated kanji, so first align the trailing kana with
    # the *end* of the reading.  Only then split the repeated-kanji reading.
    # This also avoids confusing an earlier occurrence, e.g. the first い in
    # 痛い -> いたい, with the visible trailing い.
    if "々" in surface:
        trailing_match = re.search(r"([\u3040-\u309f\u30a0-\u30ff\u30fc]+)$", surface)
        trailing = trailing_match.group(1) if trailing_match else ""
        core_surface = surface[:-len(trailing)] if trailing else surface
        core_reading = reading
        if trailing:
            hiragana_trailing = katakana_to_hiragana(trailing)
            suffix_position = reading.rfind(hiragana_trailing)
            if suffix_position >= 0 and suffix_position + len(hiragana_trailing) == len(reading):
                core_reading = reading[:suffix_position]
        if core_surface.endswith("々") and len(core_surface) >= 2:
            base = core_surface[:-1]
            if base and core_reading:
                # For the common repetition pattern, split the reading into
                # the first-kanji and repeated-kanji portions.
                if len(core_reading) % 2 == 0:
                    split = len(core_reading) // 2
                    first_reading = core_reading[:split]
                    second_reading = core_reading[split:]
                    if first_reading and second_reading:
                        result = [
                            {"text": base, "reading": first_reading},
                            {"text": "々", "reading": second_reading},
                        ]
                        if trailing:
                            result.append({"text": trailing, "reading": None})
                        return result

    segments = []
    text_position = 0
    reading_position = 0
    pattern = re.compile(
        r"([\u3400-\u9fff]+)([\u3040-\u309f\u30a0-\u30ff\u30fc]*)"
    )

    for match in pattern.finditer(surface):
        if match.start() > text_position:
            prefix = surface[text_position:match.start()]
            segments.append({
                "text": prefix,
                "reading": None,
            })

            # Consume kana already visible before this kanji from the
            # tokenizer reading before assigning ruby to the kanji.
            hiragana_prefix = katakana_to_hiragana(prefix)
            if hiragana_prefix and all(
                ("\u3040" <= char <= "\u309f")
                or ("\u30a0" <= char <= "\u30ff")
                or char == "\u30fc"
                for char in prefix
            ) and reading.startswith(hiragana_prefix, reading_position):
                reading_position += len(hiragana_prefix)

        kanji = match.group(1)
        suffix = match.group(2)
        hiragana_suffix = katakana_to_hiragana(suffix)

        if hiragana_suffix:
            # For an intermediate kanji, use the first matching suffix so
            # repeated kana are not accidentally consumed from a later
            # kanji.  For the final kanji, use the trailing match so cases
            # like 痛い -> いたい do not mistake the initial い for the
            # visible okurigana.
            has_later_kanji = bool(re.search(r"[\u3400-\u9fff]", surface[match.end():]))
            finder = reading.find if has_later_kanji else reading.rfind
            suffix_position = finder(
                hiragana_suffix,
                reading_position,
            )
            ruby = (
                reading[reading_position:suffix_position]
                if suffix_position >= 0
                else ""
            )
            if suffix_position >= 0:
                reading_position = suffix_position + len(hiragana_suffix)
        else:
            ruby = reading[reading_position:]
            reading_position = len(reading)

        segments.append({"text": kanji, "reading": ruby or None})

        if suffix:
            segments.append({"text": suffix, "reading": None})

        text_position = match.end()

    if text_position < len(surface):
        segments.append({
            "text": surface[text_position:],
            "reading": None,
        })

    return segments

def build_pinyin_segments(text):
    """Build ruby-compatible pinyin segments, one reading per Han character."""
    text = str(text or "")
    if not text:
        return []
    if _pypinyin is None or Style is None:
        return _fallback(text)

    segments = []
    index = 0
    while index < len(text):
        if not _is_hanzi(text[index]):
            start = index
            while index < len(text) and not _is_hanzi(text[index]):
                index += 1
            segments.append({"text": text[start:index], "reading": None})
            continue

        start = index
        while index < len(text) and _is_hanzi(text[index]):
            index += 1
        run = text[start:index]
        readings = _pypinyin(run, style=Style.TONE, heteronym=False, strict=False)
        for char, values in zip(run, readings):
            reading = str(values[0]) if values else None
            segments.append({"text": char, "reading": reading or None})
    return segments


_SEGMENT_CACHE = OrderedDict()
_SEGMENT_CACHE_LIMIT = 2000
_SEGMENT_CACHE_LOCK = threading.Lock()

def _segment_cache_key(text, mode=None):
    """Cache automatic readings by parser, mode, and Auto language context.

    Auto-mode output depends on the lyric collection's resolved language.
    A fallback created before/under a different context must never be reused
    for Korean romanization (or another language).
    """
    resolved_mode = str(mode or get_reading_mode())
    context = get_auto_language_context() if resolved_mode == "auto" else None
    parser = get_active_parser()
    parse_mode = get_mecab_parse_mode() if parser == "mecab_local" else None
    return (parser, parse_mode, resolved_mode, context, str(text or ""))

def clear_reading_segment_cache():
    """Clear cached automatic reading results.

    Needed when an optional reading provider becomes available after earlier
    fallback results for the same text were cached.
    """
    with _SEGMENT_CACHE_LOCK:
        _SEGMENT_CACHE.clear()

def get_cached_furigana_segments(text):
    text = str(text or "")
    with _SEGMENT_CACHE_LOCK:
        key = _segment_cache_key(text)
        cached = _SEGMENT_CACHE.get(key)
        if cached is not None:
            _SEGMENT_CACHE.move_to_end(key)
    return None if cached is None else [dict(segment) for segment in cached]

def _cache_furigana_segments(text, segments):
    text = str(text or "")
    if text:
        with _SEGMENT_CACHE_LOCK:
            key = _segment_cache_key(text)
            _SEGMENT_CACHE[key] = [dict(segment) for segment in segments]
            _SEGMENT_CACHE.move_to_end(key)
            while len(_SEGMENT_CACHE) > _SEGMENT_CACHE_LIMIT:
                _SEGMENT_CACHE.popitem(last=False)

def _fallback(text):
    return [{"text": str(text), "reading": None}] if text else []


def yomi_analyze(text):
    """Analyze a complete lyric line and return Yomi's word records.

    Successful results are cached. Failed requests are deliberately *not*
    cached: a temporary timeout should not permanently disable furigana for a
    lyric until the application is restarted.

    Requests are serialized because opening a lyric file may otherwise launch
    many worker threads against the same small remote service simultaneously.
    """
    text = str(text or "")
    if not text:
        return []

    with _CACHE_LOCK:
        cached = _YOMI_CACHE.get(text)
        if cached is not None:
            _YOMI_CACHE.move_to_end(text)
    if cached is not None:
        return cached

    last_error = None

    with _YOMI_REQUEST_LOCK:
        # Another worker may have completed this exact request while we were
        # waiting for the network slot.
        with _CACHE_LOCK:
            cached = _YOMI_CACHE.get(text)
            if cached is not None:
                _YOMI_CACHE.move_to_end(text)
        if cached is not None:
            return cached

        for attempt in range(1, YOMI_RETRIES + 1):
            try:
                print(
                    f"[Yomi] Request {attempt}/{YOMI_RETRIES}: {text!r}"
                )
                response = requests.post(
                    YOMI_URL,
                    data={"text": text},
                    headers={
                        "Content-Type":
                            "application/x-www-form-urlencoded"
                    },
                    timeout=YOMI_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()

                # Yomi currently returns its analysis JSON wrapped inside a
                # JSON string on some responses. Decode that second layer.
                if isinstance(payload, str):
                    payload = json.loads(payload)

                words = (
                    payload.get("words")
                    if isinstance(payload, dict)
                    else None
                )
                if not isinstance(words, list):
                    preview = repr(payload)[:300]
                    raise ValueError(
                        "Yomi response did not contain a words list: "
                        f"{preview}"
                    )

                with _CACHE_LOCK:
                    _YOMI_CACHE[text] = words
                    _YOMI_CACHE.move_to_end(text)
                    while len(_YOMI_CACHE) > _YOMI_CACHE_LIMIT:
                        _YOMI_CACHE.popitem(last=False)

                print(f"[Yomi] OK {text!r}: {len(words)} words")
                return words

            except (
                requests.RequestException,
                ValueError,
                TypeError,
            ) as error:
                last_error = error
                print(
                    f"[Yomi] Attempt {attempt}/{YOMI_RETRIES} failed "
                    f"for {text!r}: {error}"
                )

    print(f"[Yomi] FAILED {text!r}: {last_error}")
    return None


def mecab_analyze(text):
    """Optional local MeCab backend using fugashi + UniDic-lite.

    Kept optional so the packaged app does not have to ship the large dictionary.
    """
    text = str(text or "")
    if not text:
        return []
    try:
        import fugashi
        import unidic_lite
        global _MECAB_TAGGER
        with _MECAB_TAGGER_LOCK:
            if _MECAB_TAGGER is None:
                _MECAB_TAGGER = fugashi.Tagger(f'-d "{unidic_lite.DICDIR}"')
            tagger = _MECAB_TAGGER
        words = []
        for token in tagger(text):
            surface = str(token.surface)
            feature = token.feature
            reading = getattr(feature, "kana", None) or getattr(feature, "pron", None)
            if not reading:
                reading = getattr(feature, "pronBase", None)
            words.append({"surface": surface, "reading": str(reading or "")})
        return words
    except Exception as error:
        print(f"[MeCab] FAILED {text!r}: {error}")
        return None

def _analyze(text):
    parser = get_active_parser()
    if parser == "mecab_local":
        words = mecab_analyze(text)
        if words is not None:
            print(f"[Furigana] Using MeCab local for {text!r}")
            return words
        print("[Furigana] Local MeCab unavailable; falling back to Yomi")
    return yomi_analyze(text)

def yomi_word_surfaces(text):
    """Return tokenizer-like surfaces for karaoke auto-separation."""
    words = _analyze(text)
    if not words:
        return []
    return [str(word.get("surface", "")) for word in words if word.get("surface")]


def analyze_lyric_words(lines):
    """Return tokenizer words aligned with lyric lines and timestamps."""
    normalized = []
    for line in lines or []:
        if isinstance(line, dict):
            text = str(line.get("text", "") or "")
            start_ms = int(line.get("start_ms", 0) or 0)
        else:
            text = str(getattr(line, "text", "") or "")
            start_ms = round(float(getattr(line, "start", 0) or 0) * 1000)
        normalized.append({"text": text, "start_ms": start_ms})

    if not normalized:
        return []

    offsets = []
    source_parts = []
    source_position = 0
    for index, line in enumerate(normalized):
        offsets.append(source_position)
        source_parts.append(line["text"])
        source_position += len(line["text"])
        if index < len(normalized) - 1:
            source_parts.append("\n")
            source_position += 1

    source = "".join(source_parts)
    words = _analyze(source)
    if not words:
        return []

    result = []
    source_position = 0
    for word in words:
        surface = str(word.get("surface", "") or "")
        if not surface.strip():
            continue
        found_at = source.find(surface, source_position)
        if found_at < 0:
            continue
        source_position = found_at + len(surface)

        line_index = -1
        for index, offset in enumerate(offsets):
            line_end = offset + len(normalized[index]["text"])
            if offset <= found_at < line_end:
                line_index = index
                break
        if line_index < 0 or not any(char.isalnum() for char in surface):
            continue

        reading = str(
            word.get("reading_raw") or word.get("reading") or ""
        ).strip()
        if reading == "*":
            reading = ""
        result.append({
            "text": surface,
            "reading": reading,
            "context": normalized[line_index]["text"],
            "line_index": line_index,
            "start_ms": normalized[line_index]["start_ms"],
        })
    return result


def _build_segments_from_words(text, words, should_continue=None):
    segments = []
    source_position = 0
    for word in words:
        if should_continue and not should_continue():
            return _fallback(text)
        surface = str(word.get("surface", ""))
        if not surface:
            continue
        found_at = text.find(surface, source_position)
        if found_at < 0:
            print(f"[Yomi] Could not align {surface!r} in {text!r}; preserving remaining source text")
            if source_position < len(text):
                segments.append({"text": text[source_position:], "reading": None})
            source_position = len(text)
            break
        if found_at > source_position:
            segments.append({"text": text[source_position:found_at], "reading": None})
        reading = word.get("reading_raw") or word.get("reading")
        if isinstance(reading, str) and reading and reading != "*":
            parts = token_to_furigana_segments(surface, reading)
        else:
            parts = [{"text": surface, "reading": None}]

        # Keep the tokenizer's word boundaries available to the display layer.
        # All-Romaji needs these boundaries because ordinary Japanese lyrics
        # often have no spaces between words.  The marker is display metadata
        # only and never changes the source lyric.
        if parts:
            parts[0]["_romaji_word_start"] = True
            for part in parts[1:]:
                part["_romaji_word_start"] = False
        segments.extend(parts)
        source_position = found_at + len(surface)
    if source_position < len(text):
        segments.append({"text": text[source_position:], "reading": None})
    return segments or _fallback(text)

def _split_segments_by_lines(segments, line_count):
    result = [[] for _ in range(line_count)]
    line_index = 0
    for segment in segments:
        pieces = str(segment.get("text", "")).split("\n")
        reading = segment.get("reading")
        word_start = bool(segment.get("_romaji_word_start", False))
        for i, piece in enumerate(pieces):
            if piece and line_index < line_count:
                result[line_index].append({
                    "text": piece,
                    "reading": reading,
                    "_romaji_word_start": word_start and i == 0,
                })
            if i < len(pieces) - 1:
                line_index += 1
    return result

def prefetch_furigana_batches(lines, batch_size=10, progress_callback=None, should_continue=None, force=False):
    if not automatic_readings_enabled() and not force:
        if progress_callback: progress_callback(0, 0)
        return 0, 0
    if get_reading_mode() == "auto":
        set_auto_language_context(detect_lyrics_collection_language(lines))
    unique, seen = [], set()
    for value in lines:
        text = str(value or "").lstrip()
        if text and text not in seen and get_cached_furigana_segments(text) is None:
            unique.append(text); seen.add(text)
    total = len(unique); completed = 0
    if progress_callback: progress_callback(completed, total)
    japanese = []
    mode = get_reading_mode()

    # Explicit providers may offer a real batch implementation. This matters
    # for expensive engines such as PyCantonese, where one invocation for an
    # entire lyric collection is dramatically cheaper than spawning tokenizer
    # machinery once per line.
    if mode in available_reading_providers():
        try:
            batches = generate_reading_segments_batch(mode, unique)
            if len(batches) != len(unique):
                raise ValueError("provider batch result length mismatch")
        except Exception:
            batches = [_fallback(text) for text in unique]
        for text, segments in zip(unique, batches):
            _cache_furigana_segments(text, segments or _fallback(text))
            completed += 1
            if progress_callback: progress_callback(completed, total)
        return completed, total

    for text in unique:
        if should_continue and not should_continue(): break
        language = detect_lyrics_language(text)
        provider_id = find_reading_provider(text, language=language) if mode == "auto" else None
        try:
            if provider_id:
                segments = generate_reading_segments(provider_id, text)
            elif mode == "pinyin" or (mode == "auto" and language == "chinese"):
                segments = build_pinyin_segments(text)
            elif mode == "furigana" or (mode == "auto" and language == "japanese"):
                japanese.append(text); continue
            else:
                segments = _fallback(text)
        except Exception:
            segments = _fallback(text)
        _cache_furigana_segments(text, segments or _fallback(text))
        completed += 1
        if progress_callback: progress_callback(completed, total)
    # MeCab can tokenize the same lyric differently when surrounding lines are
    # included in the input. Let the user choose between the faster batch mode
    # and parsing each lyric line independently.
    individual_mecab = (
        get_active_parser() == "mecab_local"
        and get_mecab_parse_mode() == "line"
    )

    if individual_mecab:
        for text in japanese:
            if should_continue and not should_continue():
                break
            words = _analyze(text)
            segments = _build_segments_from_words(text, words, should_continue) if words else _fallback(text)
            _cache_furigana_segments(text, segments or _fallback(text))
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
    else:
        size=max(1,int(batch_size))
        for start in range(0,len(japanese),size):
            if should_continue and not should_continue(): break
            batch=japanese[start:start+size]; combined="\n".join(batch); words=_analyze(combined)
            if words:
                split=_split_segments_by_lines(_build_segments_from_words(combined, words, should_continue),len(batch))
                for text,segments in zip(batch,split): _cache_furigana_segments(text,segments or _fallback(text))
            else:
                for text in batch: _cache_furigana_segments(text,_fallback(text))
            completed += len(batch)
            if progress_callback: progress_callback(completed,total)
    return completed,total

def build_furigana_segments(text, progress_callback=None, should_continue=None, force=False):
    """Build automatic ruby: furigana for Japanese, pinyin for Chinese."""
    text = str(text or "")
    if not text:
        return []
    if should_continue and not should_continue():
        return _fallback(text)

    mode = get_reading_mode()
    language = detect_lyrics_language(text)

    cached = get_cached_furigana_segments(text)
    if cached is not None:
        return cached

    if not automatic_readings_enabled() and not force:
        return _fallback(text)

    if mode in available_reading_providers():
        try:
            segments = generate_reading_segments(mode, text)
        except Exception:
            segments = _fallback(text)
    else:
        provider_id = (
            find_reading_provider(text, language=language)
            if mode == "auto" else None
        )
        if provider_id:
            try:
                segments = generate_reading_segments(provider_id, text)
            except Exception:
                segments = _fallback(text)
        elif mode == "pinyin" or (mode == "auto" and language == "chinese"):
            segments = build_pinyin_segments(text)
        elif mode == "furigana" or (mode == "auto" and language == "japanese"):
            words = _analyze(text)
            if not words:
                return _fallback(text)
            segments = _build_segments_from_words(text, words, should_continue)
        else:
            segments = _fallback(text)

    _cache_furigana_segments(text, segments)
    if progress_callback:
        progress_callback(segments)
    return segments

