from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "lyrics" / "furigana_widget.py"


def test_karaoke_manual_ruby_never_disables_whole_line_generation():
    source = SOURCE.read_text(encoding="utf-8")

    # A saved manual ruby can coexist with automatically generated ruby on the
    # same lyric line. Generation must therefore still run when the automatic
    # cache is cold after reopening the file.
    assert "and not any(item.get(\"reading\") for item in raw_segments)" not in source
    assert "self._karaoke_furigana_pending != text" in source
    assert '{"karaoke": original_segments, "segments": result' in source
