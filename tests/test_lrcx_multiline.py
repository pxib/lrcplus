from pathlib import Path
from lyrics.lrcx_parser import load_lrcx
from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx


def test_lrcx_multiline_plain_roundtrip(tmp_path):
    path = tmp_path / "multi.lrcx"
    path.write_text("[format:LRCX]\n[version:1]\n[00:10.000]Hello{\\n}World\n", encoding="utf-8")
    lines = load_lrcx(path)
    assert len(lines) == 1
    assert lines[0].text == "Hello\nWorld"
    assert lines[0].segments[0].text == "Hello\nWorld"
    assert "[00:10.000]Hello{\\n}World" in serialize_lrcx(lines)


def test_lrcx_multiline_karaoke_roundtrip(tmp_path):
    path = tmp_path / "karaoke_multi.lrcx"
    path.write_text("[format:LRCX]\n[version:1]\n[00:10.000]Hello<00:10.500>{\\n}<00:11.000]", encoding="utf-8")
    # Use a valid terminal timestamp form for the actual parser test.
    path.write_text("[format:LRCX]\n[version:1]\n[00:10.000]Hello<00:10.500>{\\n}<00:11.000>World\n", encoding="utf-8")
    lines = load_lrcx(path)
    assert lines[0].text == "Hello\nWorld"
    assert [s.text for s in lines[0].segments] == ["Hello", "\n", "World"]
    assert serialize_lrcx(lines).count("{\\n}") == 1


def test_multiline_ruby_stays_inline():
    line = LyricLine(
        start=1.0,
        text="描き\n埋め",
        has_karaoke=True,
        segments=[
            LyricSegment(1.0, "描き", ruby_spans=[(0, 1, "が")]),
            LyricSegment(2.0, "\n"),
            LyricSegment(3.0, "埋め", ruby_spans=[(0, 1, "う")]),
        ],
    )
    text = serialize_lrcx([line])
    assert "{描|が}" in text
    assert "{\\n}" in text
    assert "{埋|う}" in text


def test_newline_followed_by_real_timestamp_gets_new_visual_line(tmp_path):
    path = tmp_path / "timestamped_multi.lrcx"
    path.write_text(
        "[format:LRCX]\n[version:1]\n[00:10.000]らららー{\\n}<00:12.000>hello\n",
        encoding="utf-8",
    )
    line = load_lrcx(path)[0]

    real = [segment for segment in line.segments if segment.text != "\n"]
    assert [segment.text for segment in real] == ["らららー", "hello"]
    assert [segment.start for segment in real] == [10.0, 12.0]
    assert [segment.visual_line for segment in real] == [0, 1]
    assert line.segments[1].text == "\n"
