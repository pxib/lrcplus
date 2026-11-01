"""Conservative spoken readings of Arabic numbers in Japanese lyrics.

Shared by Japanese furigana generation and the All Romaji view. Dates, clock
notation, decimal notation, identifiers and leading-zero strings stay numeric.
"""

import re
import unicodedata


_DIGITS = ("ぜろ", "いち", "に", "さん", "よん", "ご", "ろく", "なな", "はち", "きゅう")
_LARGE_UNITS = ("", "まん", "おく", "ちょう")
_COUNTERS = {"回", "人", "歳", "才", "個", "枚", "台", "本", "匹", "冊", "円", "番", "つ"}
_INTEGER = re.compile(r"[0-9０-９]+")
_LITERAL = re.compile(
    r"[0-9０-９]+(?:[.,:/_：．，／\-−－][0-9０-９]+)+"
    r"|[+＋\-−－][0-9０-９]+"
    r"|[0-9０-９]+[%％]"
    r"|[A-Za-zＡ-Ｚａ-ｚ][A-Za-zＡ-Ｚａ-ｚ0-9０-９_-]*[0-9０-９][A-Za-zＡ-Ｚａ-ｚ0-9０-９_-]*"
    r"|[0-9０-９]+[A-Za-zＡ-Ｚａ-ｚ][A-Za-zＡ-Ｚａ-ｚ0-9０-９_-]*"
    r"|[0-9０-９]+(?:年|月|日|時|分|秒)"
)


def _under_ten_thousand(number):
    parts = []
    thousands, remainder = divmod(number, 1000)
    hundreds, remainder = divmod(remainder, 100)
    tens, ones = divmod(remainder, 10)
    if thousands:
        parts.append({1: "せん", 3: "さんぜん", 8: "はっせん"}.get(thousands, _DIGITS[thousands] + "せん"))
    if hundreds:
        parts.append({1: "ひゃく", 3: "さんびゃく", 6: "ろっぴゃく", 8: "はっぴゃく"}.get(hundreds, _DIGITS[hundreds] + "ひゃく"))
    if tens:
        parts.append(("" if tens == 1 else _DIGITS[tens]) + "じゅう")
    if ones:
        parts.append(_DIGITS[ones])
    return "".join(parts)


def integer_reading(number):
    """Return a cardinal reading through 兆, or None outside that range."""
    if not 0 <= number < 10 ** 16:
        return None
    if number == 0:
        return _DIGITS[0]
    groups = []
    for unit in _LARGE_UNITS:
        number, group = divmod(number, 10000)
        if group:
            groups.append(_under_ten_thousand(group) + unit)
    return "".join(reversed(groups))


def _contract(reading, endings):
    for ending, replacement in endings:
        if reading.endswith(ending):
            return reading[:-len(ending)] + replacement
    return reading


def counter_reading(number, counter):
    reading = integer_reading(number)
    if reading is None:
        return None
    if counter == "人":
        if number in {1, 2}:
            return {1: "ひとり", 2: "ふたり"}[number]
        return _contract(reading, [("よん", "よ")]) + "にん"
    if counter in {"歳", "才"}:
        if number == 20:
            return "はたち"
        return _contract(reading, [("いち", "いっ"), ("はち", "はっ"), ("じゅう", "じゅっ")]) + "さい"
    if counter == "つ":
        return {1: "ひとつ", 2: "ふたつ", 3: "みっつ", 4: "よっつ", 5: "いつつ",
                6: "むっつ", 7: "ななつ", 8: "やっつ", 9: "ここのつ", 10: "とお"}.get(number)
    contractions = [("いち", "いっ"), ("ろく", "ろっ"), ("はち", "はっ"),
                    ("じゅう", "じゅっ"), ("ひゃく", "ひゃっ"), ("びゃく", "びゃっ"), ("ぴゃく", "ぴゃっ")]
    if counter in {"回", "個"}:
        return _contract(reading, contractions) + {"回": "かい", "個": "こ"}[counter]
    if counter in {"本", "匹"}:
        contracted = _contract(reading, contractions)
        if contracted != reading:
            return contracted + {"本": "ぽん", "匹": "ぴき"}[counter]
        if reading.endswith(("さん", "せん", "ぜん", "まん")):
            return reading + {"本": "ぼん", "匹": "びき"}[counter]
        return reading + {"本": "ほん", "匹": "ひき"}[counter]
    if counter == "冊":
        return _contract(reading, [("いち", "いっ"), ("はち", "はっ"), ("じゅう", "じゅっ")]) + "さつ"
    return reading + {"枚": "まい", "台": "だい", "円": "えん", "番": "ばん"}[counter]


def number_spans(text):
    """Return (start, end, kana-or-None), including protected numeric literals.

    A bare integer needs Japanese context on its physical line and at most four
    digits; longer bare strings could be telephone numbers or identifiers.
    An explicit supported counter allows larger numbers.
    """
    spans = [(match.start(), match.end(), None) for match in _LITERAL.finditer(text)]
    for match in _INTEGER.finditer(text):
        start, end = match.span()
        if any(a < end and start < b for a, b, _ in spans):
            continue
        digits = unicodedata.normalize("NFKC", match.group())
        counter = text[end:end + 1]
        if counter not in _COUNTERS:
            counter = ""
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line = text[line_start:line_end if line_end >= 0 else len(text)]
        japanese = any("\u3041" <= char <= "\u3096" or "\u30a1" <= char <= "\u30fa"
                       or "\u3400" <= char <= "\u9fff" or "\uff66" <= char <= "\uff9f" for char in line)
        reading = None
        if (len(digits) <= 16 and (len(digits) == 1 or not digits.startswith("0"))
                and (counter or (japanese and len(digits) <= 4))):
            # Unknown kanji units need their own pronunciation rules.
            following = text[end:end + 1]
            if counter:
                reading = counter_reading(int(digits), counter)
            elif not following or not "\u3400" <= following <= "\u9fff":
                reading = integer_reading(int(digits))
        spans.append((start, end + len(counter), reading))
    return sorted(spans)
