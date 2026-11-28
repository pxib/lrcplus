import pytest

from lyrics import native_formats
from lyrics.lrcx_parser import parse_lrcx
from lyrics.models import LyricLine
from lyrics.parser import parse_lrc_lines
from lyrics.writer import serialize_lrc, serialize_lrcx
from ui.lrc_editor import LrcEditorDialog


RICH_LRCX = (
    "[format:LRCX]\n[version:1]\n"
    '[reading_groups:{"0":[[0,1,"\u97f3"]]}]\n'
    '[line_ends:{"1":7000}]\n'
    "[g:romaji]\n[offset:-125]\n"
    "[00:01.000]> **{\u97f3|\u304a<00:01.250><00:01.500>\u3068}**"
    r"<00:02.000><00:02.000>{\nt}<00:03.000><instant>Sound"
    r"{\n}<00:04.000><u>Ending</u><00:04.500><00:05.000>" "\n"
    "[00:06.000]Plain \U0001f3b5\n"
)


@pytest.fixture
def native_operations(monkeypatch):
    if not native_formats.available():
        pytest.skip("Requires the built Rust lyric formats DLL.")
    transform = native_formats._transform
    operations = []

    def track(operation, data):
        output = transform(operation, data)
        operations.append((operation, output is not None))
        return output

    monkeypatch.setattr(native_formats, "_transform", track)
    return operations


def test_lrc_native_sorting_and_writing_match_python(native_operations, monkeypatch):
    source = [
        "[ar:Artist]\n", "[00:03.000][00:01.500]  \u97f3  \r\n",
        "[00:03.000][00:01.500](reading)\n", "[00:02.000]\n",
    ]
    parsed = parse_lrc_lines(iter(source))
    lines = [LyricLine(start, text) for start, text in parsed]
    lines.append(LyricLine(59.99999999999999, "another\nrow"))
    serialized = serialize_lrc(iter(lines), generation_mode="jyut")
    assert parsed == [(1.5, "\u97f3  "), (1.5, "(reading)"), (2.0, ""),
                      (3.0, "\u97f3  "), (3.0, "(reading)")]
    assert native_operations == [(0, True), (2, True)]
    with monkeypatch.context() as python_only:
        python_only.setattr(native_formats, "_api", lambda: None)
        assert parse_lrc_lines(iter(source)) == parsed
        assert serialize_lrc(iter(lines), generation_mode="jyut") == serialized

    assert parse_lrc_lines(["[\uff10\uff10:\uff10\uff11]Unicode timestamp"]) == [
        (1.0, "Unicode timestamp"),
    ]
    assert native_operations[-1] == (0, False)


@pytest.mark.parametrize("infer_ends", [False, True])
def test_lrcx_native_format_matches_python(native_operations, monkeypatch, infer_ends):
    source = "\ufeff" + RICH_LRCX.replace("\n", "\r\n")
    parsed = parse_lrcx(source, infer_ends=infer_ends)
    serialized = serialize_lrcx(parsed, offset_ms=-125, generation_mode="romaji")
    rich, plain = parsed
    assert native_operations == [(1, True), (3, True)]
    assert rich.is_blockquote and rich.translation_lines == [1]
    assert rich.reading_groups == [(0, 1)] and rich.style_spans
    assert rich.segments[0].ruby == "\u304a\u3068"
    assert rich.segments[0].mora_cues == ((1, 0.25), (1, 0.5))
    assert rich.segments[0].end == 2.0 and rich.end == 5.0
    assert any(segment.instant for segment in rich.segments)
    assert plain.end == 7.0 and not plain.end_inferred
    with monkeypatch.context() as python_only:
        python_only.setattr(native_formats, "_api", lambda: None)
        assert parse_lrcx(source, infer_ends=infer_ends) == parsed
        assert serialize_lrcx(parsed, offset_ms=-125, generation_mode="romaji") == serialized
    assert parse_lrcx(serialized, infer_ends=infer_ends) == parsed


def test_editor_retains_authored_lrcx_while_editing_a_neighbor(qapp, tmp_path, native_operations):
    path = tmp_path / "rich.lrcx"
    path.write_text(RICH_LRCX, encoding="utf-8")
    original = parse_lrcx(RICH_LRCX, infer_ends=False)
    editor = LrcEditorDialog(path)
    try:
        editor.go_to_lyric_line(1)
        editor.line_text.setPlainText("Edited lyric")
        assert editor.save_document()
    finally:
        editor.done(editor.DialogCode.Rejected)
        editor.deleteLater()

    reopened = LrcEditorDialog(path)
    try:
        assert reopened.lines[0] == original[0]
        assert reopened.lines[1].text == "Edited lyric"
        assert reopened.lines[1].end == original[1].end
        assert reopened.lyrics_offset_ms == -125
        assert "[g:romaji]" in path.read_text(encoding="utf-8")
    finally:
        reopened.done(reopened.DialogCode.Rejected)
        reopened.deleteLater()
    assert all(success for _, success in native_operations)
    assert {operation for operation, _ in native_operations} == {1, 3}
