from threading import Event, get_ident
from pathlib import Path
import os
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtGui import QFont, QTextCursor

from core.library_lyrics import LibraryLyricsIndex
from core.music_library import LibraryTrack
from core.settings import save_lrc_search_folders, set_lyrics_attachment
from ui.main_window import Player
from ui.library_lyrics_search import LyricMatchDelegate


def search(index, paths, query, **kwargs):
    return set(index.search([str(path) for path in paths], query, Event(), **kwargs)[1])


def test_matches_visible_text_in_all_local_formats(tmp_path):
    paths = [tmp_path / f"song{i}.mp3" for i in range(4)]
    paths[0].with_suffix(".lrc").write_text(
        "[ti:Metadata only]\n[00:01]Hello WORLD\n[00:02]Next line", encoding="utf-8",
    )
    paths[1].with_suffix(".lrcx").write_text(
        "[format:lrcx]\n[00:01]Hello <00:02>world\n[00:03]{世界|せかい}", encoding="utf-8",
    )
    paths[2].with_suffix(".txt").write_text("hello\nworld", encoding="utf-8-sig")
    index = LibraryLyricsIndex()
    assert search(index, paths, "  HELLO   world  ") == set(map(str, paths[:3]))
    assert search(index, paths, "世界") == {str(paths[1])}
    assert search(index, paths, "metadata only") == set()
    assert search(index, paths, "00:01") == set()


def test_uses_configured_folders_attachments_and_current_lyrics(tmp_path):
    audio = tmp_path / "music" / "song.mp3"
    audio.parent.mkdir()
    folder = tmp_path / "lyrics"
    folder.mkdir()
    lyric = folder / "song.lrc"
    lyric.write_text("[00:01]Found in configured folder", encoding="utf-8")
    save_lrc_search_folders([str(folder)])
    index = LibraryLyricsIndex()
    assert search(index, [audio], "configured folder") == {str(audio)}
    forced = tmp_path / "unrelated.txt"
    forced.write_text("Explicit association", encoding="utf-8")
    set_lyrics_attachment(audio, forced)
    assert search(index, [audio], "explicit association") == {str(audio)}
    assert search(index, [audio], "configured folder") == set()
    assert search(index, [audio], "configured folder",
                  current_lyrics=(str(audio), str(lyric))) == {str(audio)}


def test_cached_text_refreshes_after_edit_and_deleted_files_are_skipped(tmp_path, monkeypatch):
    audio = tmp_path / "song.mp3"
    lyric = audio.with_suffix(".lrc")
    lyric.write_text("[00:01]Old words", encoding="utf-8")
    import core.library_lyrics as module
    original = module.load_lyrics
    reads = []

    def load(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(module, "load_lyrics", load)
    index = LibraryLyricsIndex()
    assert search(index, [audio], "old") == {str(audio)}
    assert search(index, [audio], "words") == {str(audio)}
    assert len(reads) == 1
    stamp = lyric.stat().st_mtime_ns
    lyric.write_text("[00:01]New words", encoding="utf-8")
    os.utime(lyric, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))
    assert search(index, [audio], "new") == {str(audio)}
    assert search(index, [audio], "old") == set()
    assert len(reads) == 2
    lyric.unlink()
    assert search(index, [audio], "new") == set()


def test_bad_file_does_not_stop_search_and_cancellation_skips_work(tmp_path, monkeypatch):
    bad, good = tmp_path / "bad.mp3", tmp_path / "good.mp3"
    bad.with_suffix(".lrc").write_bytes(b"\xff\xfe")
    good.with_suffix(".lrc").write_text("[00:01]Still found", encoding="utf-8")
    index = LibraryLyricsIndex()
    assert search(index, [bad, good], "found") == {str(good)}
    cancel = Event()
    cancel.set()
    monkeypatch.setattr(index, "_text", lambda path: pytest.fail("Cancelled search read a file"))
    assert index.search([str(good)], "found", cancel)[1] == {}


def test_rescan_refreshes_edited_attachment_tags(tmp_path):
    first, second = tmp_path / "first.mp3", tmp_path / "second.mp3"
    lyric = tmp_path / "unrelated.lrc"
    lyric.write_text(f"[attach:{first}]\n[00:01]Attached phrase", encoding="utf-8")
    index = LibraryLyricsIndex()
    assert search(index, [first, second], "attached phrase") == {str(first)}
    lyric.write_text(f"[attach:{second}]\n[00:01]Attached phrase", encoding="utf-8")
    index.invalidate()
    assert search(index, [first, second], "attached phrase") == {str(second)}


@pytest.mark.parametrize("source, query, expected, highlighted", [
    ("Intro\n好きだよ。\n好きだね。", "好き", "好きだよ。", "好き"),
    ("First\nStraße und STRASSE", "STRASSE", "Straße und STRASSE", "Straße"),
    ("😀好きだよ。", "好き", "😀好きだよ。", "好き"),
    ("Intro\nHello\tWORLD\nLast", "hello world", "Hello\tWORLD", "Hello\tWORLD"),
    ("Intro\nHello\n WORLD\nLast", "hello world", "Hello  WORLD", "Hello  WORLD"),
    ("Before\nLiteral <b>word</b> & more", "word", "Literal <b>word</b> & more", "word"),
])
def test_first_match_preserves_original_lyric_and_highlight_span(tmp_path, source, query, expected, highlighted):
    audio = tmp_path / "song.mp3"
    audio.with_suffix(".txt").write_text(source, encoding="utf-8")
    matches = LibraryLyricsIndex().search([str(audio)], query, Event())[1]
    match = matches[str(audio)]
    assert match.text == expected
    assert match.text[match.start:match.end] == highlighted


def test_long_matching_lines_keep_first_hit_in_excerpt(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.with_suffix(".txt").write_text("a" * 1000 + "好き" + "b" * 1000 + "好き", encoding="utf-8")
    match = LibraryLyricsIndex().search([str(audio)], "好き", Event())[1][str(audio)]
    assert match.text.startswith("…") and match.text.endswith("…")
    assert len(match.text) < 150
    assert match.text[match.start:match.end] == "好き"
    assert match.text.count("好き") == 1


def test_delegate_bolds_only_match_and_preserves_literal_text(qapp):
    text = "😀 <b>好き</b> & 好きだよ。"
    start = text.index("好き")
    document = LyricMatchDelegate._document(text, (start, start + 2), QFont())
    assert document.toPlainText() == text
    # QTextCursor uses UTF-16 positions, including two units for the emoji.
    for i, char in enumerate(text):
        position = len(text[:i].encode("utf-16-le")) // 2
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        cursor.setPosition(position + len(char.encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor)
        assert (cursor.charFormat().fontWeight() == QFont.Weight.Bold) == (start <= i < start + 2)


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Player, "_restore_startup_state", lambda self: None)
    player = Player()
    qapp.processEvents()
    player.library_tracks = [LibraryTrack(str(tmp_path / f"song{i}.mp3"), f"Title {i}", album=f"Album {i}")
                             for i in range(2)]
    for i, track in enumerate(player.library_tracks):
        Path(track.path).with_suffix(".lrc").write_text(f"[00:01]Lyric phrase {i}", encoding="utf-8")
    player._populate_library_view()
    yield player
    player.close()
    player.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait_for(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_lyrics_mode_filters_albums_and_clearing_restores_expansion(window, qapp):
    first, second = [window.library_table.topLevelItem(i) for i in range(2)]
    first.setExpanded(False)
    window.library_search_filter.setCurrentIndex(1)
    window.library_search.setText("PHRASE 0")
    window._filter_library_view()
    wait_for(qapp, lambda: window.library_lyrics_status.text() == "1 song(s) match the lyrics.")
    assert first.isExpanded() and not first.isHidden()
    assert not first.child(0).isHidden() and second.isHidden()
    column = window.library_lyrics_column
    assert not window.library_table.isColumnHidden(column)
    assert first.child(0).text(column) == "Lyric phrase 0"
    span = first.child(0).data(column, Qt.ItemDataRole.UserRole)
    assert first.child(0).text(column)[span[0]:span[1]] == "phrase 0"
    window.library_search.clear()
    window._filter_library_view()
    assert not first.isExpanded() and not second.isHidden()
    assert window.library_lyrics_status.isHidden()
    assert first.child(0).text(column) == ""
    assert first.child(0).data(column, Qt.ItemDataRole.UserRole) is None
    window.library_search_filter.setCurrentIndex(0)
    assert window.library_table.isColumnHidden(column)
    window.library_search.setText("Title 1")
    window._filter_library_view()
    assert first.isHidden() and not second.isHidden()


def test_existing_column_layouts_keep_their_indexes_and_new_column_is_near_title(window):
    header = window.library_table.header()
    old_order = [3, 2, 0, 1, *range(4, 12)]
    window.library_column_state = {"order": old_order, "hidden": [6, 7], "widths": {"2": 275}}
    window._apply_library_column_state()
    column = window.library_lyrics_column
    assert header.visualIndex(column) == header.visualIndex(2) + 1
    assert [header.logicalIndex(i) for i in range(13) if header.logicalIndex(i) != column] == old_order
    assert window.library_table.columnWidth(2) == 275
    assert window.library_table.isColumnHidden(6)
    window.library_search_filter.setCurrentIndex(1)
    window._reset_library_columns()
    assert not window.library_table.isColumnHidden(column)
    assert header.visualIndex(column) == header.visualIndex(2) + 1


def test_column_renders_first_japanese_match(window, qapp, tmp_path):
    Path(window.library_tracks[0].path).with_suffix(".lrc").write_text(
        "[00:01]Intro\n[00:02]好きだよ。\n[00:03]好きだね。", encoding="utf-8",
    )
    window.library_search_filter.setCurrentIndex(1)
    window.library_search.setText("好き")
    window._filter_library_view()
    window.show()
    wait_for(qapp, lambda: window.library_lyrics_status.text() == "1 song(s) match the lyrics.")
    qapp.processEvents()
    item = window.library_table.topLevelItem(0).child(0)
    column = window.library_lyrics_column
    assert item.text(column) == "好きだよ。"
    assert item.data(column, Qt.ItemDataRole.UserRole) == (0, 2)
    assert window.library_table.grab().save(str(tmp_path / "lyric-column.png"))
    item.setSelected(True)
    qapp.processEvents()
    assert not window.library_table.grab().isNull()


@pytest.mark.parametrize("action", ["query", "clear", "mode", "rebuild", "close"])
def test_background_search_stays_responsive_and_discards_stale_results(window, qapp, monkeypatch, action):
    entered, release = Event(), Event()
    worker_threads = []
    original = window._library_lyrics_index.search

    def blocked(*args):
        worker_threads.append(get_ident())
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(window._library_lyrics_index, "search", blocked)
    window.library_search_filter.setCurrentIndex(1)
    window.library_search.setText("phrase 0")
    window._filter_library_view()
    try:
        assert entered.wait(2)
        assert worker_threads[0] != get_ident()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks
        if action == "query":
            window.library_search.setText("phrase 1")
        elif action == "clear":
            window.library_search.clear()
        elif action == "mode":
            window.library_search_filter.setCurrentIndex(0)
            window.library_search.setText("Title 1")
        elif action == "rebuild":
            window.library_tracks = window.library_tracks[1:]
            window._populate_library_view()
        else:
            window.close()
            assert not release.is_set()
            return
        window._filter_library_view()
        release.set()
        if action in {"query", "rebuild"}:
            count = 1 if action == "query" else 0
            wait_for(qapp, lambda: window.library_lyrics_status.text() == f"{count} song(s) match the lyrics.")
            if action == "query":
                assert window.library_table.topLevelItem(0).isHidden()
                assert not window.library_table.topLevelItem(1).isHidden()
        elif action == "clear":
            qapp.processEvents()
            assert all(not window.library_table.topLevelItem(i).isHidden() for i in range(2))
        else:
            qapp.processEvents()
            assert window.library_table.topLevelItem(0).isHidden()
            assert not window.library_table.topLevelItem(1).isHidden()
    finally:
        release.set()
