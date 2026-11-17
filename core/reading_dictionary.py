"""Named reading dictionaries, stored atomically and indexed in memory."""

from copy import deepcopy
import csv
import io
import json
import threading
import uuid
import unicodedata
from pathlib import Path

from core.file_io import atomic_write_text
from core.settings import get_app_data_dir


READING_TYPES = {
    "furigana": "Japanese furigana", "pinyin": "Mandarin pinyin",
    "korean": "Korean romanization", "jyutping": "Cantonese Jyutping",
    "yale": "Cantonese Yale",
    "english_katakana": "English katakana",
}
MATCH_TYPES = {"word": "Whole word", "substring": "Inside words"}
_LOCK = threading.RLock()
_PATH = None
_DATA = None
_INDEX = {}


def new_dictionary(name="User dictionary"):
    return {"id": uuid.uuid4().hex, "name": name, "enabled": True, "entries": []}


def get_factory_dictionaries():
    """Return a fresh, editable starter set based on the supplied song dictionary."""
    return {"version": 1, "dictionaries": [{
        "id": "b05b1d04e57942109bbb5a757d054371",
        "name": "Japanese song readings",
        "enabled": True,
        "entries": [
            {"word": word, "reading": reading, "type": "furigana",
             "comment": "", "match": "word"}
            for word, reading in (("私", "わたし"), ("君", "きみ"), ("時", "とき"))
        ],
    }]}


def validate_dictionaries(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("dictionaries"), list):
        raise ValueError("This is not a supported LRCPlus reading dictionary.")
    result = {"version": 1, "dictionaries": []}
    ids, names = set(), set()
    for dictionary in data["dictionaries"]:
        if not isinstance(dictionary, dict):
            raise ValueError("Each dictionary must have a name and entries.")
        name = str(dictionary.get("name", "")).strip()
        identity = dictionary.get("id") or uuid.uuid4().hex
        if not name or len(name) > 100 or name.casefold() in names:
            raise ValueError("Give each dictionary a unique name of 1–100 characters.")
        if not isinstance(identity, str) or identity in ids:
            raise ValueError("Dictionary identifiers must be unique.")
        if not isinstance(dictionary.get("entries"), list):
            raise ValueError(f'Entries in "{name}" must be a list.')
        entries, words = [], set()
        for entry in dictionary["entries"]:
            if not isinstance(entry, dict):
                raise ValueError(f'Invalid entry in "{name}".')
            if any(not isinstance(entry.get(key, ""), str) for key in ("word", "reading", "type", "comment")):
                raise ValueError("Dictionary words, readings, types and comments must be text.")
            values = {key: str(entry.get(key, "")).strip() for key in ("word", "reading", "type", "comment")}
            if not values["type"]:
                values["type"] = "furigana"
            if not values["word"] or not values["reading"]:
                raise ValueError(f'Every entry in "{name}" needs both a word and a reading.')
            if any("\n" in value or "\r" in value or "\t" in value for value in values.values()):
                raise ValueError("Dictionary fields must stay on one line and cannot contain tabs.")
            if len(values["word"]) > 200 or len(values["reading"]) > 500 or len(values["comment"]) > 1000:
                raise ValueError("Keep words under 200 characters, readings under 500, and comments under 1,000.")
            if values["type"] not in READING_TYPES:
                raise ValueError(f'Unknown reading type: {values["type"]}')
            match = entry.get("match", "word")
            if not isinstance(match, str) or match not in MATCH_TYPES:
                raise ValueError("Choose Whole word or Inside words for each dictionary entry.")
            values["match"] = match
            key = values["type"], values["word"]
            if key in words:
                raise ValueError(f'Duplicate {READING_TYPES[key[0]]} entry for "{key[1]}" in "{name}".')
            words.add(key)
            entries.append(values)
        ids.add(identity)
        names.add(name.casefold())
        result["dictionaries"].append({"id": identity, "name": name,
                                        "enabled": dictionary.get("enabled", True) is not False,
                                        "entries": entries})
    return result


def _install(path, data):
    global _PATH, _DATA, _INDEX
    index = {}
    for dictionary in data["dictionaries"]:
        if not dictionary["enabled"]:
            continue
        for entry in dictionary["entries"]:
            node = index.setdefault(entry["type"], {})
            for char in entry["word"]:
                node = node.setdefault(char, {})
            # Earlier dictionaries take precedence for identical words.
            node.setdefault(None, (entry["reading"], entry["match"]))
    _PATH, _DATA, _INDEX = path, data, index


def load_dictionaries(*, reload=False):
    path = get_app_data_dir() / "reading-dictionaries.json"
    with _LOCK:
        if reload or path != _PATH or _DATA is None:
            try:
                data = validate_dictionaries(json.loads(path.read_text(encoding="utf-8-sig")))
            except FileNotFoundError:
                data = get_factory_dictionaries()
            _install(path, data)
        return deepcopy(_DATA)


def save_dictionaries(data):
    data = validate_dictionaries(data)
    path = get_app_data_dir() / "reading-dictionaries.json"
    with _LOCK:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", backup=True)
        _install(path, data)


def _current_index():
    path = get_app_data_dir() / "reading-dictionaries.json"
    with _LOCK:
        if path != _PATH or _DATA is None:
            try:
                load_dictionaries()
            except (OSError, ValueError, TypeError):
                # Preserve invalid files for repair. Reading playback must work.
                _install(path, {"version": 1, "dictionaries": []})
        return _INDEX


def has_dictionary_entries():
    return bool(_current_index())


def word_boundary_positions(text):
    """Conservative boundaries when no tokenizer provides lexical words.

    Keep adjacent CJK letters together; character/ruby boundaries alone cannot
    establish that 私 is a word inside 私立.
    """
    boundaries = {0, len(text)}
    for position, char in enumerate(text):
        if not (char.isalnum() or char == "_" or unicodedata.category(char).startswith("M")):
            boundaries.update((position, position + 1))
    return boundaries


def dictionary_matches(text, reading_type, *, word_starts=None, word_ends=None):
    root = _current_index().get(reading_type, {})
    if word_starts is None or word_ends is None:
        word_starts = word_ends = word_boundary_positions(text)
    matches, start = [], 0
    while start < len(text) and root:
        node, cursor, best = root, start, None
        while cursor < len(text) and text[cursor] in node:
            node = node[text[cursor]]
            cursor += 1
            if None in node:
                reading, match = node[None]
                if match == "substring" or (start in word_starts and cursor in word_ends):
                    best = (start, cursor, reading)
        if best is not None:
            matches.append(best)
            start = best[1]
        else:
            start += 1
    return matches


def import_dictionary_file(path):
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    if path.suffix.lower() == ".json":
        return validate_dictionaries(json.loads(text))["dictionaries"]
    dictionary = new_dictionary(path.stem)
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        if not row or not any(value.strip() for value in row) or row[0].startswith("!"):
            continue
        if row[0].strip().casefold() in {"reading", "よみ"} and len(row) > 1 and row[1].strip().casefold() in {"word", "単語"}:
            continue
        if len(row) < 2 or len(row) > 5:
            raise ValueError("Expected Reading, Word, Reading type / Part of speech, Comment, and optional Match columns.")
        kind = row[2].strip() if len(row) > 2 else "furigana"
        # Google Japanese Input exports a part of speech in column three.
        kind = kind if kind in READING_TYPES else "furigana"
        dictionary["entries"].append({"reading": row[0], "word": row[1], "type": kind,
                                       "comment": row[3] if len(row) > 3 else "",
                                       "match": row[4].strip() if len(row) > 4 else "word"})
    return validate_dictionaries({"version": 1, "dictionaries": [dictionary]})["dictionaries"]


def export_dictionary_file(path, dictionary):
    path = Path(path)
    data = validate_dictionaries({"version": 1, "dictionaries": [dictionary]})
    if path.suffix.lower() == ".json":
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    else:
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(["Reading", "Word", "Reading type", "Comment", "Match"])
        for entry in data["dictionaries"][0]["entries"]:
            writer.writerow([entry["reading"], entry["word"], entry["type"], entry["comment"], entry["match"]])
        text = output.getvalue()
    atomic_write_text(path, text, backup=True)
