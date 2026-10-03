from pathlib import Path

from lyrics.models import LyricLine
import ui.lrc_editor as lrc_editor
from ui.lrc_editor import LrcEditorDialog


class _FormatSelector:
    def __init__(self, value):
        self.value = value

    def currentData(self):
        return self.value


class _StatusLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _ShareHarness:
    _share_text = LrcEditorDialog._share_text
    _share_text_for_lines = staticmethod(LrcEditorDialog._share_text_for_lines)
    _lines_for_share = staticmethod(LrcEditorDialog._lines_for_share)
    export_share_text = LrcEditorDialog.export_share_text

    def __init__(self, lines, mode, path):
        self.lines = lines
        self.share_format_combo = _FormatSelector(mode)
        self.share_status_label = _StatusLabel()
        self.lrc_path = Path(path)
        self._source_dirty = False
        self.source_lines = []
        self.selected_indices = set()

    def reload_model_from_source(self):
        self.lines = self.source_lines
        self._source_dirty = False

    def _checked_share_line_indices(self):
        return self.selected_indices


def test_share_text_without_timestamps_preserves_lyric_text_and_newlines():
    lines = [
        LyricLine(start=1.25, text="First line\ncontinued"),
        LyricLine(start=3.5, text="Second line"),
    ]

    result = LrcEditorDialog._share_text_for_lines(lines)

    assert result == "First line\ncontinued\nSecond line\n"


def test_share_text_with_timestamps_uses_line_start_times():
    lines = [
        LyricLine(start=1.25, text="First line\ncontinued"),
        LyricLine(start=63.5, text="Second line"),
    ]

    result = LrcEditorDialog._share_text_for_lines(
        lines,
        include_timestamps=True,
    )

    assert result == "[00:01.250]First line\ncontinued\n[01:03.500]Second line\n"


def test_share_text_for_empty_document_is_empty():
    assert LrcEditorDialog._share_text_for_lines([]) == ""


def test_lines_for_share_uses_selected_indices_in_document_order():
    lines = [
        LyricLine(start=1.0, text="First"),
        LyricLine(start=2.0, text="Second"),
        LyricLine(start=3.0, text="Third"),
    ]

    assert LrcEditorDialog._lines_for_share(lines, {2, 0}) == [lines[0], lines[2]]
    assert LrcEditorDialog._lines_for_share(lines, set()) == lines


def test_share_text_uses_pending_source_edits():
    dialog = _ShareHarness([], "text", "song.lrc")
    dialog._source_dirty = True
    dialog.source_lines = [LyricLine(start=2.0, text="Unsaved source lyric")]

    assert dialog._share_text() == "Unsaved source lyric\n"
    assert dialog._source_dirty is False


def test_export_share_text_writes_plain_text_and_cancel_does_nothing(
    tmp_path,
    monkeypatch,
):
    output_path = tmp_path / "shared.txt"
    dialog = _ShareHarness(
        [LyricLine(start=2.0, text="Shared lyric")],
        "text",
        tmp_path / "song.lrc",
    )
    monkeypatch.setattr(
        lrc_editor.QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(output_path), ""),
    )

    dialog.export_share_text()

    assert output_path.read_text(encoding="utf-8") == "Shared lyric\n"
    assert "Exported to" in dialog.share_status_label.text

    dialog.share_status_label.text = ""
    monkeypatch.setattr(
        lrc_editor.QFileDialog,
        "getSaveFileName",
        lambda *_args: ("", ""),
    )
    dialog.export_share_text()

    assert dialog.share_status_label.text == ""


def test_export_timestamped_share_text_uses_lrc_extension(tmp_path, monkeypatch):
    output_path = tmp_path / "shared.lrc"
    dialog = _ShareHarness(
        [LyricLine(start=4.25, text="Timed lyric")],
        "lrc",
        tmp_path / "song.lrcx",
    )
    monkeypatch.setattr(
        lrc_editor.QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(output_path), ""),
    )

    dialog.export_share_text()

    assert output_path.read_text(encoding="utf-8") == "[00:04.250]Timed lyric\n"