import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton

from lyrics.lrcx_parser import load_lrcx, parse_lrcx
from lyrics.ttml import load_ttml, parse_ttml, serialize_ttml
from lyrics.writer import serialize_lrcx
from ui.main_window import Player


def test_import_ttml_media_offsets_and_line_end_survive_lrcx_save():
    line, = parse_ttml(
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div begin="1s">'
        '<p begin="500ms" dur="2s">Audio &amp; lyrics<br/>Second row</p>'
        '</div></body></tt>'
    )
    assert (line.start, line.end) == (1.5, 3.5)
    assert line.text == "Audio & lyrics\nSecond row"
    assert not line.has_karaoke
    saved, = parse_lrcx(serialize_lrcx([line]), infer_ends=False)
    assert (saved.start, saved.end, saved.text, saved.has_karaoke) == (
        line.start, line.end, line.text, False,
    )


def test_import_ttml_relative_segments_keep_gaps():
    line, = parse_ttml(
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="10s" end="14s">'
        '<span begin="500ms" dur="500ms">First</span> '
        '<span begin="2s" end="3s">second</span></p></div></body></tt>'
    )
    assert line.text == "First second"
    assert line.has_karaoke
    assert [(chunk.start, chunk.end) for chunk in line.segments] == [(10.5, 11), (12, 13)]


def test_default_lrcx_line_end_stays_implicit_on_save():
    lyrics = "[00:01.000]First\n[00:02.000]Second\n"
    lines = parse_lrcx(lyrics)
    assert lines[0].end == 2
    assert parse_lrcx(serialize_lrcx(lines), infer_ends=False)[0].end is None


def test_ttml_exchange_preserves_blank_rows_before_translations():
    line, = parse_lrcx(r"[00:01.000]First{\n}{\nt}Translation{\n}Second", infer_ends=False)
    result, = parse_ttml(serialize_ttml([line]))
    assert result.text == line.text
    assert result.translation_lines == line.translation_lines


def test_apple_style_import_supports_linked_translation_and_absolute_words():
    line, = parse_ttml(
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:itunes="http://itunes.apple.com/lyric-ttml-extensions" itunes:timing="Word">'
        '<head><metadata><itunes:iTunesMetadata><itunes:translations><itunes:translation>'
        '<itunes:text for="L1">Translation</itunes:text>'
        '</itunes:translation></itunes:translations></itunes:iTunesMetadata></metadata></head>'
        '<body><div begin="10.0"><p begin="10.5" end="12.5" itunes:key="L1">'
        '<span begin="10.5" end="11.0">First</span> '
        '<span begin="00:11.500" end="12.0">line</span>'
        '</p></div></body></tt>'
    )
    assert (line.start, line.end) == (10.5, 12.5)
    assert line.text == "First line\nTranslation"
    assert line.translation_lines == [1]
    assert [(chunk.start, chunk.end) for chunk in line.segments[:2]] == [(10.5, 11), (11.5, 12)]


def test_ttml_round_trip_keeps_authored_ruby_mora_pauses_translations_and_offset():
    lines = parse_lrcx(
        r"[00:12.000]{穴|あ<00:12.350><00:12.650>な}<00:12.900><00:13.000> & next{\nt}Translation<00:14.000>"
        "\n[00:15.000]{君|きみ} < literally",
        infer_ends=False,
    )
    result = parse_ttml(serialize_ttml(lines, offset_ms=250))
    assert [line.text for line in result] == [line.text for line in lines]
    assert [line.start for line in result] == [12.25, 15.25]
    assert result[0].translation_lines == [1]
    assert result[0].end == 14.25
    word = result[0].segments[0]
    assert (word.start, word.end, word.ruby, word.mora_cues) == (
        12.25, 13.15, "あな", ((1, 0.35), (1, 0.65)),
    )
    assert result[1].segments[0].ruby_spans == [(0, 1, "きみ")]
    assert not result[1].has_karaoke


@pytest.mark.parametrize("contents", [
    "<html/>",
    "<tt><body>",
    '<!DOCTYPE tt [<!ENTITY text "lyrics">]><tt><body><p>&text;</p></body></tt>',
    '<tt><body><p begin="2frames">Lyrics</p></body></tt>',
])
def test_invalid_or_unsupported_ttml_reports_a_parse_error(contents):
    with pytest.raises(ValueError):
        parse_ttml(contents)


def _click_source_button(editor, text):
    source_page = editor.source_editor.parentWidget()
    editor.tabs.setCurrentWidget(source_page)
    button, = (button for button in source_page.findChildren(QPushButton) if button.text() == text)
    button.click()


@pytest.mark.parametrize(("suffix", "source"), [
    (".ttml", '<tt><body><div><p begin="3s" end="5s">Imported</p></div></body></tt>'),
    (".ass", "[Events]\nDialogue: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,{\\k50}Imported\n"),
])
def test_source_import_dispatches_is_undoable_and_keeps_the_editable_target(
    editor, tmp_path, monkeypatch, suffix, source,
):
    target = tmp_path / ("exchange" + suffix)
    target.write_text(source, encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(target), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    original_path = editor.lrc_path
    editor.lyrics_offset.setValue(200)
    _click_source_button(editor, "Import…")
    assert editor.lines[0].text == "Imported"
    assert editor.lrc_path == original_path
    assert editor.lyrics_offset_ms == (0 if suffix == ".ttml" else 200)
    assert editor.save_button.isEnabled()
    assert not editor._source_dirty

    editor.undo()
    assert (editor.lines[0].text, editor.lyrics_offset_ms) == ("Hello", 200)
    editor.redo()
    assert editor.save_document()
    saved = load_lrcx(original_path, infer_ends=False)[0]
    assert saved.text == "Imported"
    if suffix == ".ttml":
        assert (saved.end, saved.has_karaoke) == (5, False)


def test_source_export_writes_ttml_copy_and_keeps_the_document_dirty(editor, tmp_path, monkeypatch):
    assert editor.save_document()
    original_path = editor.lrc_path
    original = original_path.read_bytes()
    editor.lyrics_offset.setValue(250)
    destination = tmp_path / "exchange"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)

    _click_source_button(editor, "Export…")

    assert editor.lrc_path == original_path
    assert editor.document_dirty
    assert original_path.read_bytes() == original
    assert load_ttml(destination.with_suffix(".ttml"))[0].start == 2.25


@pytest.mark.parametrize(("selected_filter", "extension"), [
    ("TTML Files (*.ttml)", ".ttml"),
    ("LRCX Files (*.lrcx)", ".lrcx"),
])
def test_player_save_as_uses_the_selected_format(qapp, tmp_path, monkeypatch, selected_filter, extension):
    source = tmp_path / "song.lrcx"
    source.write_text("[offset:250]\n[00:01.000]{君|きみ}<00:02.000> next<00:03.000>\n", encoding="utf-8")
    destination = tmp_path / "copy"
    target = destination.with_suffix(extension)
    reloaded = []

    def choose_target(_parent, _title, default, filters, default_filter):
        assert default == str(source)
        assert selected_filter in filters.split(";;")
        assert default_filter == "LRCX Files (*.lrcx)"
        return str(destination), selected_filter

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_target)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: pytest.fail(args[-1]))
    player = Player()
    try:
        player.current_lyrics_path = source
        monkeypatch.setattr(player, "load_lrc_file", reloaded.append)
        action, = (action for action in player.lyrics_menu.actions() if action.text().startswith("Save As"))
        action.trigger()
        if extension == ".ttml":
            line, = load_ttml(target)
            assert (line.start, line.end, line.segments[0].ruby) == (1.25, 3.25, "きみ")
            assert player.current_lyrics_path == source
            assert reloaded == []
        else:
            assert load_lrcx(target)[0].text == "君 next"
            assert player.current_lyrics_path == target
            assert reloaded == [str(target)]
    finally:
        player.close()
        player.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
