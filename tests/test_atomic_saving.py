import pytest

import core.file_io as file_io
from lyrics.models import LyricLine
from lyrics.writer import save_lyrics


def test_saving_lyrics_preserves_previous_file_as_backup(tmp_path):
    path = tmp_path / "nested" / "song.lrc"
    save_lyrics(path, [LyricLine(1, "Original")])
    original = path.read_bytes()
    save_lyrics(path, [LyricLine(2, "Changed")])
    assert "Changed" in path.read_text(encoding="utf-8")
    assert path.with_name("song.lrc.bak").read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))


def test_failed_replacement_preserves_original_and_cleans_temporary_files(tmp_path, monkeypatch):
    path = tmp_path / "song.lrc"
    path.write_text("original", encoding="utf-8")
    replace = file_io.os.replace

    def fail_target(source, destination):
        if destination == path:
            raise PermissionError("File is locked")
        return replace(source, destination)

    monkeypatch.setattr(file_io.os, "replace", fail_target)
    monkeypatch.setattr(file_io.time, "sleep", lambda _seconds: None)
    with pytest.raises(PermissionError):
        file_io.atomic_write_text(path, "replacement", backup=True)
    assert path.read_text(encoding="utf-8") == "original"
    assert path.with_name("song.lrc.bak").read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob("*.tmp"))


def test_write_failure_never_replaces_original(tmp_path, monkeypatch):
    path = tmp_path / "song.lrc"
    path.write_text("original", encoding="utf-8")

    def no_space(_fd):
        raise OSError("No space left")

    monkeypatch.setattr(file_io.os, "fsync", no_space)
    with pytest.raises(OSError, match="No space"):
        file_io.atomic_write_text(path, "replacement")
    assert path.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob("*.tmp"))
