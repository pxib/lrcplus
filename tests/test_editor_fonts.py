"""Korean editor content must avoid the large Windows Gulim fallback."""

import unicodedata
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
from PySide6.QtGui import QFont, QFontDatabase, QTextDocument, QTextLayout
from PySide6.QtWidgets import QLabel, QStyleOptionViewItem

from lyrics.models import LyricSegment
from ui.content_fonts import FontRunResolver


KOREAN = "\uc0ac\ub791\ud574 \ud55c\uae00"


def glyph_families(text, font, formats=()):
    layout = QTextLayout(text, font)
    layout.setFormats(formats)
    layout.beginLayout()
    layout.createLine().setLineWidth(4000)
    layout.endLayout()
    runs = layout.glyphRuns()
    assert runs
    assert all(glyph for run in runs for glyph in run.glyphIndexes())
    families = {run.rawFont().familyName() for run in runs}
    assert not families.intersection({"Gulim", "Batang", "SimSun"})
    return families


def test_korean_editor_fields_and_sidebar_use_modern_fallback(editor, qapp):
    if not QFontDatabase.hasFamily("Malgun Gothic"):
        pytest.skip("Requires a modern Korean fallback font")
    text = KOREAN + " \u65e5\u672c\u8a9e " + unicodedata.normalize("NFD", "Ti\u1ebfng Vi\u1ec7t")
    editor.line_text.setPlainText(text)
    qapp.processEvents()
    assert editor.lines[0].text == text
    editor.line_text._preview_fonts.rehighlight()
    assert glyph_families(text, editor.line_text.font(),
                          editor.line_text.document().begin().layout().formats()) == {"Noto Sans JP", "Malgun Gothic"}

    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    editor.source_editor._preview_fonts.rehighlight()
    block = editor.source_editor.document().begin()
    while block.isValid() and KOREAN not in block.text():
        block = block.next()
    assert block.isValid()
    assert KOREAN in block.text()
    assert glyph_families(block.text(), editor.source_editor.font(),
                          block.layout().formats()) == {"Noto Sans JP", "Malgun Gothic"}

    option = QStyleOptionViewItem()
    option.initFrom(editor.line_list)
    delegate = editor.line_list.itemDelegate()
    delegate.initStyleOption(option, editor.line_list.model().index(0, 0))
    assert KOREAN in option.text
    assert glyph_families(option.text, option.font,
                          FontRunResolver(option.font).formats(option.text)) == {"Noto Sans JP", "Malgun Gothic"}


def test_korean_karaoke_controls_and_authored_readings_keep_covering_fonts(editor, qapp):
    if not QFontDatabase.hasFamily("Malgun Gothic"):
        pytest.skip("Requires a modern Korean fallback font")
    line = editor.lines[0]
    line.text = KOREAN
    line.segments = [
        LyricSegment(line.start, KOREAN[:3], ruby="\ud55c\uae00", needs_timing_review=True),
        LyricSegment(line.start + 1, KOREAN[3:]),
    ]
    editor.load_selected_line(0)
    for widget in (editor.full_line_label, editor.separation_preview):
        assert glyph_families(widget.text(), widget.font()) == {"Malgun Gothic"}
    assert glyph_families(editor.separator_widget.text(),
                          editor.separator_widget.font()) == {"Malgun Gothic"}
    for row in range(editor.segment_table.rowCount()):
        for column in (2, 3):
            item = editor.segment_table.item(row, column)
            if item.text():
                assert glyph_families(item.text(), item.font()) == {"Malgun Gothic"}

    editor._refresh_karaoke_preview()
    preview = editor.karaoke_renderer_preview
    glyph_families(KOREAN, preview.base_font)
    glyph_families("\ud55c\uae00", preview.reading_font)
    assert preview.base_font.styleStrategy() & QFont.StyleStrategy.NoFontMerging
    assert preview.reading_font.styleStrategy() & QFont.StyleStrategy.NoFontMerging


def test_editable_lyric_text_keeps_emoji_arabic_selection_and_undo(editor, qapp):
    if not QFontDatabase.hasFamily("Segoe UI Emoji"):
        pytest.skip("Requires native Windows fallback fonts")
    field = editor.line_text
    text = KOREAN + " \U0001f600 \u0627\u0644\u0633\u0644\u0627\u0645"
    field.setPlainText(text)
    field._preview_fonts.rehighlight()
    glyph_families(text, field.font(), field.document().begin().layout().formats())
    field.selectAll()
    assert field.textCursor().selectedText() == text
    field.insertPlainText(KOREAN)
    qapp.processEvents()
    assert editor.lines[0].text == KOREAN
    editor.undo()
    assert editor.lines[0].text == text
    assert field.toPlainText() == text


def test_karaoke_preview_restores_chosen_font_for_covered_text(editor):
    if not QFontDatabase.hasFamily("Yu Gothic UI"):
        pytest.skip("Requires native Windows fallback fonts")
    chosen = QFont("Yu Gothic UI", 27)
    chosen.setItalic(True)
    editor._karaoke_preview_fonts = chosen, QFont(chosen)
    line = editor.lines[0]
    line.text = KOREAN
    editor._refresh_karaoke_preview()
    preview = editor.karaoke_renderer_preview
    glyph_families(KOREAN, preview.base_font)

    line.text = "Hello"
    editor._refresh_karaoke_preview()
    assert preview.base_font.family() == chosen.family()
    assert preview.base_font.pointSize() == 27
    assert preview.base_font.italic()
    assert chosen.family() == "Yu Gothic UI"


def test_song_association_labels_keep_mixed_filenames_and_covering_fonts(
    make_lyrics_editor, monkeypatch, tmp_path, qapp,
):
    if not QFontDatabase.hasFamily("Segoe UI Emoji"):
        pytest.skip("Requires native Windows fallback fonts")
    from ui.lrc_editor import LrcEditorDialog

    song = tmp_path / (KOREAN + " <&>  \U0001f49b.mp3")
    monkeypatch.setattr(LrcEditorDialog, "_current_song_path", lambda self: song)
    dialog = make_lyrics_editor()
    label = dialog.song_association_label
    assert label.text() == str(song)
    label.setSelection(0, len(str(song).encode("utf-16-le")) // 2)
    assert label.selectedText() == str(song)

    document = QTextDocument()
    document.setDefaultFont(label.font())
    document.setHtml(QLabel.text(label))
    assert document.toPlainText() == str(song)
    block = document.begin()
    formats = []
    fragment_iterator = block.begin()
    while not fragment_iterator.atEnd():
        fragment = fragment_iterator.fragment()
        value = QTextLayout.FormatRange()
        value.start = fragment.position() - block.position()
        value.length = fragment.length()
        value.format = fragment.charFormat()
        formats.append(value)
        fragment_iterator += 1
    families = glyph_families(block.text(), label.font(), formats)
    assert {"Noto Sans JP", "Malgun Gothic", "Segoe UI Emoji"} <= families
    label.clear()
    assert label.text() == "" and label.selectedText() == ""


def test_korean_song_path_does_not_load_large_font_cache_in_fresh_process(qapp, tmp_path):
    if sys.platform != "win32" or qapp.platformName() != "windows":
        pytest.skip("Requires the native Windows font database")
    # Other rendering tests may already have loaded Gulim. Use a fresh process
    # and an English editor baseline so that the filename is the only change.
    script = textwrap.dedent(r'''
        import ctypes
        from ctypes import wintypes
        import gc
        import json
        from pathlib import Path
        import sys
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication, QWidget
        from ui.lrc_editor import LrcEditorDialog

        class Memory(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'peak_working', 'working', 'peak_paged', 'paged',
                    'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]

        def private_bytes():
            info = Memory()
            info.cb = ctypes.sizeof(info)
            assert ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.c_void_p(-1), ctypes.byref(info), info.cb)
            return info.private

        app = QApplication([])
        LrcEditorDialog._schedule_auto_reading_generation = lambda self: None
        LrcEditorDialog._start_background_reading_generation = lambda *args, **kwargs: None
        parent = QWidget()
        folder = Path(sys.argv[1])
        path = folder / 'song.lrc'
        path.write_text('[00:01.000]Hello', encoding='utf-8')
        parent.current_audio_path = folder / 'english.mp3'
        baseline = LrcEditorDialog(path, parent)
        baseline.grab()
        baseline.close()
        baseline.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        del baseline
        gc.collect()
        before = private_bytes()

        parent.current_audio_path = folder / '\uc0ac\ub791\ud574 \U0001f49b.mp3'
        editor = LrcEditorDialog(path, parent)
        editor.grab()
        after = private_bytes()
        assert editor.song_association_label.text() == str(parent.current_audio_path)
        print(json.dumps({'increase': after - before}))
        editor.close()
    ''')
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONIOENCODING="utf-8",
               LRCPLUS_SETTINGS_PATH=str(tmp_path / "settings.json"),
               LRCPLUS_DATA_DIR=str(tmp_path / "data"))
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=25,
    )
    assert result.returncode == 0, result.stderr
    increase = json.loads(result.stdout.strip().splitlines()[-1])["increase"]
    # The broken hidden label allocates about 35 MiB of private memory.
    # Allow ample room for modern Korean/emoji fonts and a new editor's widgets.
    assert increase < 16 * 1024 * 1024, f"Korean filename allocated {increase / 1048576:.1f} MiB"
