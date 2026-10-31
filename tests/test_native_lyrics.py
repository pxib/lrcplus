import base64
import zlib

import pytest

from core import native_lyrics
from lyrics.lrcx_parser import load_lrcx


def encode_krc(text):
    compressed = zlib.compress(text.encode("utf-8"))
    key = b"@Gaw^2tGQ61-\xce\xd2ni"
    return base64.b64encode(b"krc1" + bytes(value ^ key[index % len(key)] for index, value in enumerate(compressed))).decode("ascii")


@pytest.mark.parametrize("kind, native", [
    ("yrc", '[1000,2000](1100,250,0)君(1600,300,0)の'),
    ("krc", '[1000,2000]<100,250,0>君<600,300,0>の'),
])
def test_absolute_and_relative_timing_preserve_word_and_line_ends(tmp_path, kind, native):
    lines = native_lyrics.parse_word_lyrics(native, kind)
    path = tmp_path / "native.lrcx"
    path.write_text(native_lyrics.to_lrcx(lines), encoding="utf-8")
    loaded = load_lrcx(path)
    assert len(loaded) == 1
    line = loaded[0]
    assert line.text == "君の"
    assert line.start == 1.0
    assert line.end == 3.0
    assert line.has_karaoke
    assert [(word.start, word.end, word.text) for word in line.segments] == [
        (1.1, 1.35, "君"), (1.6, 1.9, "の"),
    ]


def test_touching_and_zero_duration_words_keep_explicit_ends(tmp_path):
    lines = native_lyrics.parse_word_lyrics("[0,500](0,0,0)A(0,250,0)B(250,250,0)C", "yrc")
    path = tmp_path / "touching.lrcx"
    path.write_text(native_lyrics.to_lrcx(lines), encoding="utf-8")
    loaded = load_lrcx(path)[0]
    assert loaded.text == "ABC"
    assert [(word.start, word.end) for word in loaded.segments] == [(0, 0), (0, .25), (.25, .5)]


def test_native_metadata_is_not_converted_to_lyric_lines():
    text = '[ti:Title]\n{"t":0,"c":[{"tx":"作词：Name"}]}\n[1234,500](1234,500,0)歌'
    lines = native_lyrics.parse_word_lyrics(text, "yrc")
    assert len(lines) == 1
    assert lines[0].text == "歌"
    assert "Name" not in native_lyrics.to_lrcx(lines)


def test_leading_text_and_literal_formatting_survive_conversion(tmp_path):
    text = r"[1000,1000]Lead (1200,800,0)*literal* \path <note>"
    path = tmp_path / "literal.lrcx"
    path.write_text(native_lyrics.to_lrcx(native_lyrics.parse_word_lyrics(text, "yrc")), encoding="utf-8")
    loaded = load_lrcx(path)[0]
    assert loaded.text == r"Lead *literal* \path <note>"
    assert not loaded.style_spans
    assert loaded.segments[0].end == 1.2


def test_backwards_word_start_is_rejected():
    with pytest.raises(ValueError, match="invalid word timing"):
        native_lyrics.parse_word_lyrics("[1000,2000](1200,100,0)A(1100,100,0)B", "yrc")


def test_krc_decoding_roundtrip():
    text = "[ti:标题]\n[1234,500]<0,500,0>歌词"
    assert native_lyrics.decode_krc(encode_krc(text)) == text


@pytest.mark.parametrize("encoded", [None, "!invalid!", base64.b64encode(b"bad header").decode(), base64.b64encode(b"krc1bad").decode()])
def test_invalid_krc_does_not_reach_parser(encoded):
    with pytest.raises(ValueError):
        native_lyrics.decode_krc(encoded)


def test_krc_decompressed_size_is_bounded(monkeypatch):
    monkeypatch.setattr(native_lyrics, "MAX_TEXT_BYTES", 32)
    with pytest.raises(ValueError, match="oversized"):
        native_lyrics.decode_krc(encode_krc("A" * 1000))


def test_krc_encoded_size_is_bounded_before_decoding(monkeypatch):
    monkeypatch.setattr(native_lyrics, "MAX_KRC_BYTES", 16)
    with pytest.raises(ValueError, match="oversized"):
        native_lyrics.decode_krc("A" * 1000)
