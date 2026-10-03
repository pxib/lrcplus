"""Lightweight Japanese kana -> Hepburn romaji conversion.

The converter intentionally operates on kana readings, not kanji.  Furigana
segments therefore remain the single source of truth for pronunciation.
"""

_BASIC = {
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
}

_COMBINED = {
    "きゃ":"kya","きゅ":"kyu","きょ":"kyo",
    "ぎゃ":"gya","ぎゅ":"gyu","ぎょ":"gyo",
    "しゃ":"sha","しゅ":"shu","しょ":"sho",
    "じゃ":"ja","じゅ":"ju","じょ":"jo",
    "ちゃ":"cha","ちゅ":"chu","ちょ":"cho",
    "ぢゃ":"ja","ぢゅ":"ju","ぢょ":"jo",
    "にゃ":"nya","にゅ":"nyu","にょ":"nyo",
    "ひゃ":"hya","ひゅ":"hyu","ひょ":"hyo",
    "びゃ":"bya","びゅ":"byu","びょ":"byo",
    "ぴゃ":"pya","ぴゅ":"pyu","ぴょ":"pyo",
    "みゃ":"mya","みゅ":"myu","みょ":"myo",
    "りゃ":"rya","りゅ":"ryu","りょ":"ryo",
    "ゔぁ":"va","ゔぃ":"vi","ゔぇ":"ve","ゔぉ":"vo",
    "いぇ":"ye","うぃ":"wi","うぇ":"we","うぉ":"wo",
    "くぁ":"kwa","くぃ":"kwi","くぇ":"kwe","くぉ":"kwo",
    "ぐぁ":"gwa","ぐぃ":"gwi","ぐぇ":"gwe","ぐぉ":"gwo",
    "しぇ":"she","じぇ":"je","ちぇ":"che",
    "てぃ":"ti","でぃ":"di","とぅ":"tu","どぅ":"du",
    "つぁ":"tsa","つぃ":"tsi","つぇ":"tse","つぉ":"tso",
    "ふぁ":"fa","ふぃ":"fi","ふぇ":"fe","ふぉ":"fo",
}

# Convert the ordinary katakana range to hiragana by Unicode offset.
def _hiragana(text):
    return "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch
        for ch in str(text or "")
    )


def kana_to_romaji(text):
    """Convert hiragana/katakana to readable Hepburn-style romaji."""
    text = _hiragana(text)
    out = []
    i = 0
    geminate = False

    while i < len(text):
        ch = text[i]

        if ch == "っ":
            geminate = True
            i += 1
            continue

        pair = text[i:i + 2]
        if pair in _COMBINED:
            value = _COMBINED[pair]
            i += 2
        elif ch in _BASIC:
            value = _BASIC[ch]
            i += 1
        elif ch == "ー":
            # Keep long-vowel marks readable instead of dropping them.
            if out:
                last = out[-1]
                vowel = next((v for v in "aeiou" if last.endswith(v)), "")
                value = vowel
            else:
                value = ""
            i += 1
        else:
            value = ch
            i += 1

        if geminate and value:
            # Double the first consonant of the following syllable.  Do not
            # double vowels or special punctuation.
            first = value[0]
            if first.isalpha() and first.lower() not in "aeiou":
                value = first + value
            geminate = False

        # Hepburn convention: n' before a vowel or y avoids ambiguity.
        if out and out[-1] == "n" and value and value[0] in "aeiouy":
            out[-1] = "n'"

        out.append(value)

    return "".join(out)
