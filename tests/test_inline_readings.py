from copy import deepcopy
from dataclasses import asdict

import pytest

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QLabel, QInputDialog, QMenu

from lyrics.loader import load_lyrics
from lyrics.models import LyricLine, LyricSegment
from ui.inline_readings import InlineReadingsEditor


def test_visual_edit_add_remove_and_cancel(qapp):
    widget = InlineReadingsEditor()
    widget.set_tokens([{"text": "貴方", "reading": "あなた"}, {"text": "の"},
                       {"text": "心", "reading": "こころ"}])
    changes = []
    widget.readingChanged.connect(lambda: changes.append(widget.tokens()))
    widget.show()
    qapp.processEvents()
    try:
        control = widget.controls[1]
        assert control["button"].text() == "+"
        control["button"].click()
        control["edit"].setText("no")
        QTest.keyClick(control["edit"], Qt.Key.Key_Return)
        assert widget.tokens()[1]["reading"] == "no"
        control["button"].click()
        control["edit"].setText("discard")
        QTest.keyClick(control["edit"], Qt.Key.Key_Escape)
        assert widget.tokens()[1]["reading"] == "no"
        assert len(changes) == 1
        control["button"].click()
        control["edit"].clear()
        QTest.keyClick(control["edit"], Qt.Key.Key_Return)
        assert widget.tokens()[1]["reading"] is None
        assert control["button"].text() == "+"
        assert len(changes) == 2
    finally:
        widget.close()
        widget.deleteLater()


def test_visual_reading_edit_saves_and_undoes_without_changing_text(editor, monkeypatch, qapp):
    tokens = [{"text": "貴方", "reading": "あなた"}, {"text": "の"}, {"text": "心", "reading": "こころ"}]
    monkeypatch.setattr("ui.lrc_editor.get_cached_furigana_segments", lambda text: deepcopy(tokens))
    editor.lines = [LyricLine(2, "貴方の心", [LyricSegment(2, "貴方", "あなた"),
                    LyricSegment(2, "の"), LyricSegment(2, "心", "こころ")])]
    editor._populate_lines(preserve_line_index=0)
    editor._reset_history_baseline()
    editor.show()
    qapp.processEvents()
    control = editor.readings_editor.controls[0]
    control["button"].click()
    control["edit"].setText("きみ")
    QTest.keyClick(control["edit"], Qt.Key.Key_Return)
    assert editor.lines[0].text == "貴方の心"
    assert editor.lines[0].segments[0].ruby == "きみ"
    assert editor.document_dirty
    assert [state["lines"][0].segments[0].ruby for state in editor._undo_stack] == ["あなた"]
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert restored.text == "貴方の心"
    assert restored.segments[0].ruby_spans == [(0, 2, "きみ"), (3, 4, "こころ")]
    editor.undo()
    assert editor.lines[0].segments[0].ruby == "あなた"


def test_provider_completion_does_not_discard_active_input(editor, monkeypatch, qapp):
    monkeypatch.setattr("ui.lrc_editor.get_cached_furigana_segments", lambda text: [{"text": text}])
    editor.lines = [LyricLine(2, "心", [LyricSegment(2, "心")])]
    editor._populate_lines(preserve_line_index=0)
    editor.show()
    qapp.processEvents()
    control = editor.readings_editor.controls[0]
    control["button"].click()
    control["edit"].setText("ここ")
    editor._on_reading_generation_finished(editor._reading_generation_epoch, 0, True)
    assert editor.readings_editor.controls[0] is control
    assert control["edit"].text() == "ここ"
    QTest.keyClick(control["edit"], Qt.Key.Key_Escape)


def test_long_rows_scroll_without_wrapping_or_expanding_window(qapp):
    widget = InlineReadingsEditor()
    widget.set_tokens([{"text": "貴方", "reading": "あなた"}] * 20)
    widget.resize(300, 300)
    widget.show()
    qapp.processEvents()
    try:
        assert widget.controls[-1]["card"].y() == widget.controls[0]["card"].y()
        assert widget.minimumSizeHint().width() < 300
        scrollbar = widget.horizontalScrollBar()
        assert scrollbar.isVisible()
        assert scrollbar.maximum() > 0
        scrollbar.setValue(scrollbar.maximum())
        last = widget.controls[-1]["card"]
        assert widget.viewport().rect().contains(last.mapTo(widget.viewport(), last.rect().bottomRight()))
        assert not widget.verticalScrollBar().isVisible()
        widget.set_tokens([{"text": "a"}])
        qapp.processEvents()
        assert not scrollbar.isVisible()
        assert scrollbar.value() == 0
    finally:
        widget.close()
        widget.deleteLater()


def test_editing_a_scrolled_word_keeps_field_visible_and_scroll_position(qapp):
    widget = InlineReadingsEditor()
    tokens = [{"text": "a"}] * 20
    widget.set_tokens(tokens)
    widget.resize(300, 100)
    widget.show()
    qapp.processEvents()
    try:
        control = widget.controls[-1]
        control["button"].click()
        qapp.processEvents()
        viewport = widget.viewport()
        assert viewport.rect().contains(control["edit"].mapTo(viewport, QPoint(0, 0)))
        assert viewport.rect().contains(control["edit"].mapTo(viewport, control["edit"].rect().bottomRight()))
        control["edit"].setText("reading")
        QTest.keyClick(control["edit"], Qt.Key.Key_Return)
        qapp.processEvents()
        assert widget.tokens()[-1]["reading"] == "reading"
        value = widget.horizontalScrollBar().value()
        assert value > 0
        widget.set_tokens(widget.tokens())
        qapp.processEvents()
        assert widget.horizontalScrollBar().value() == value
    finally:
        widget.close()
        widget.deleteLater()


def test_long_word_stays_on_one_row_and_scrollbar_tracks_resize(qapp):
    widget = InlineReadingsEditor()
    widget.set_tokens([{"text": "long" * 30, "reading": "reading" * 30}])
    widget.resize(300, 100)
    widget.show()
    qapp.processEvents()
    try:
        card = widget.controls[0]["card"]
        assert card.width() > 360
        assert not widget.controls[0]["label"].wordWrap()
        assert widget.horizontalScrollBar().isVisible()
        assert widget.height() >= widget.sizeHint().height()
        widget.resize(card.width() + 20, 100)
        qapp.processEvents()
        assert not widget.horizontalScrollBar().isVisible()
    finally:
        widget.close()
        widget.deleteLater()


def load_karaoke(editor, monkeypatch, qapp, line, tokens):
    monkeypatch.setattr("ui.lrc_editor.get_cached_furigana_segments", lambda text: deepcopy(tokens))
    editor.lines = [line]
    editor._populate_lines(preserve_line_index=0)
    editor._reset_history_baseline()
    editor.show()
    qapp.processEvents()
    assert editor.readings_editor.isEnabled()


def edit_reading(editor, index, value):
    control = editor.readings_editor.controls[index]
    control["button"].click()
    control["edit"].setText(value)
    QTest.keyClick(control["edit"], Qt.Key.Key_Return)


def without_readings(line):
    data = asdict(line)
    for segment in data["segments"]:
        for field in ("ruby", "ruby_source", "ruby_spans"):
            segment.pop(field)
    return data


def test_karaoke_word_edit_preserves_chunks_styles_and_translations(editor, monkeypatch, qapp):
    line = LyricLine(2, "貴方の心\nYour heart", [
        LyricSegment(2.1, "貴", "あな", end=2.4, ruby_source="manual"),
        LyricSegment(2.5, "方", "た", instant=True, end=3),
        LyricSegment(3.1, "の", end=3.2),
        LyricSegment(3.3, "心", "こころ", end=4.1, ruby_source="manual", needs_timing_review=True),
        LyricSegment(4.1, "\nYour heart", end=5, visual_line=1),
    ], end=5, has_karaoke=True, style_spans=[(0, 2, True, False, False, False)],
        is_blockquote=True, translation_lines=[1])
    tokens = [{"text": "貴方"}, {"text": "の"}, {"text": "心"}, {"text": "\nYour heart"}]
    load_karaoke(editor, monkeypatch, qapp, line, tokens)
    before = deepcopy(line)
    chunks = list(line.segments)
    assert editor.readings_editor.tokens()[0]["reading"] == "あなた"
    edit_reading(editor, 0, "きみ")
    assert without_readings(line) == without_readings(before)
    assert all(a is b for a, b in zip(chunks, line.segments))
    assert [part.ruby for part in line.segments[:2]] == ["き", "み"]
    assert line.segments[3:] == before.segments[3:]
    assert editor.segment_table.item(0, 3).text() == "き"
    assert editor.segment_table.item(1, 3).text() == "み"
    after = deepcopy(line)
    editor.undo()
    assert editor.lines[0] == before
    editor.redo()
    assert editor.lines[0] == after


def test_karaoke_visual_edit_survives_save_and_reload(editor, monkeypatch, qapp):
    line = LyricLine(2, "貴方の心", [LyricSegment(2.2, "貴方", "あなた", end=3),
        LyricSegment(3.1, "の", end=3.3), LyricSegment(3.5, "心", "こころ", end=4.5)],
        end=5, has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line,
                 [{"text": "貴方"}, {"text": "の"}, {"text": "心"}])
    edit_reading(editor, 0, "きみ")
    assert editor.save_button.isEnabled()
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert restored.has_karaoke
    assert restored.text == line.text
    assert [(s.start, s.end, s.text) for s in restored.segments] == [
        (s.start, s.end, s.text) for s in line.segments]
    assert restored.start == line.start and restored.end == line.end
    assert [s.ruby for s in restored.segments] == ["きみ", None, "こころ"]


def test_editing_one_annotation_keeps_other_spans_and_generated_readings(editor, monkeypatch, qapp):
    line = LyricLine(2, "猫と犬の声", [
        LyricSegment(2, "猫と犬", end=4, ruby_spans=[(0, 1, "ねこ"), (2, 3, "いぬ")]),
        LyricSegment(4.2, "の声", "のこえ", end=5, ruby_source="generated"),
    ], end=6, has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "猫と犬"}, {"text": "の声", "reading": "のこえ"}])
    untouched = deepcopy(line.segments[1])
    assert [token["text"] for token in editor.readings_editor.tokens()] == ["猫", "と", "犬", "の声"]
    edit_reading(editor, 0, "にゃん")
    assert line.segments[0].ruby_spans == [(0, 1, "にゃん"), (2, 3, "いぬ")]
    assert line.segments[1] == untouched
    edit_reading(editor, 2, "")
    assert line.segments[0].ruby_spans == [(0, 1, "にゃん")]
    assert line.segments[1] == untouched


@pytest.mark.parametrize("text,reading", [("かな", "kana"), ("마음", "maeum"), ("hello", "həˈləʊ")])
def test_add_karaoke_reading_without_kanji(editor, monkeypatch, qapp, text, reading):
    line = LyricLine(1, text, [LyricSegment(1.2, text, end=3)], end=4, has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": text}])
    edit_reading(editor, 0, reading)
    assert line.segments[0].ruby == reading
    assert (line.segments[0].start, line.segments[0].end) == (1.2, 3)


def test_edit_karaoke_reading_keeps_okurigana_outside_annotation(editor, monkeypatch, qapp):
    line = LyricLine(1, "描き", [LyricSegment(1.2, "描き", end=3, ruby_spans=[(0, 1, "か")])],
                    end=4, has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "描き"}])
    edit_reading(editor, 0, "えが")
    assert line.segments[0].ruby_spans == [(0, 1, "えが")]
    assert line.segments[0].ruby is None
    edit_reading(editor, 0, "")
    assert not line.segments[0].ruby_spans


def test_reset_karaoke_readings_preserves_timing_and_visual_metadata(editor, monkeypatch, qapp):
    line = LyricLine(1, "猫\n犬", [
        LyricSegment(1.2, "猫\n", end=2, ruby_spans=[(0, 1, "ねこ")]),
        LyricSegment(2.2, "犬", "いぬ", instant=True, end=3, visual_line=1, needs_timing_review=True),
    ], end=4, has_karaoke=True, translation_lines=[1])
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "猫\n犬"}])
    before = deepcopy(line)
    assert editor.reset_furigana_button.isEnabled()
    editor.reset_furigana_button.click()
    assert without_readings(line) == without_readings(before)
    assert all(not s.ruby and not s.ruby_spans for s in line.segments)
    assert editor.save_button.isEnabled()
    editor.undo()
    assert editor.lines[0] == before


def test_karaoke_table_reading_edits_refresh_visual_controls(editor, monkeypatch, qapp):
    line = LyricLine(1, "心", [LyricSegment(1.2, "心", "こころ", end=3)], end=4, has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "心"}])
    editor.segment_table.item(0, 3).setText("ハート")
    assert editor.readings_editor.tokens()[0]["reading"] == "ハート"
    assert editor.readings_editor.controls[0]["button"].text() == "ハート"


def test_edit_part_of_an_imported_reading_keeps_the_rest(editor, monkeypatch, qapp):
    line = LyricLine(1, "猫犬", [LyricSegment(1.2, "猫犬", "ねこいぬ", end=3)], end=4, has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "猫"}, {"text": "犬"}])
    assert [token["reading"] for token in editor.readings_editor.tokens()] == ["ねこ", "いぬ"]
    edit_reading(editor, 0, "にゃん")
    assert line.segments[0].ruby_spans == [(0, 1, "にゃん"), (1, 2, "いぬ")]
    assert (line.segments[0].start, line.segments[0].end) == (1.2, 3)


def test_visual_readings_follow_theme_changes(qapp):
    original = QPalette(qapp.palette())
    widget = InlineReadingsEditor()
    widget.set_tokens([{"text": "心", "reading": "こころ"}])
    widget.show()
    qapp.processEvents()
    try:
        dark = QPalette(original)
        foreground = QColor("#eceff4")
        for role in (QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
            dark.setColor(role, foreground)
        dark.setColor(QPalette.ColorRole.Window, QColor("#242b39"))
        qapp.setPalette(dark)
        qapp.processEvents()
        card = widget.controls[0]
        assert card["button"].palette().color(QPalette.ColorRole.ButtonText) == foreground
        assert card["card"].findChild(QLabel).palette().color(QPalette.ColorRole.WindowText) == foreground
    finally:
        widget.close()
        widget.deleteLater()
        qapp.setPalette(original)
        qapp.processEvents()


@pytest.mark.parametrize("text", [" ", "  \u3000  ", "\t", "\n\n", " \r\n\u3000\n "])
def test_whitespace_is_kept_without_reading_controls(qapp, text):
    widget = InlineReadingsEditor()
    changes = []
    widget.readingChanged.connect(changes.append)
    try:
        widget.set_tokens([{"text": text}])
        assert "".join(token["text"] for token in widget.tokens()) == text
        assert not widget.controls
        assert not widget.is_editing()
        assert not changes
        widget.set_tokens([{"text": "心"}])
        assert len(widget.controls) == 1
    finally:
        widget.deleteLater()


def test_spaces_have_literal_gap_widths(qapp):
    widget = InlineReadingsEditor()
    widget.set_tokens([{"text": "a b  c\u3000d"}])
    widget.resize(600, 100)
    widget.show()
    qapp.processEvents()
    try:
        a, b, c, d = [control["card"].geometry() for control in widget.controls]
        one = b.left() - a.right() - 1
        two = c.left() - b.right() - 1
        full = d.left() - c.right() - 1
        assert one > 0
        assert abs(two - 2 * one) <= 1
        assert full > one
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\u2028"])
def test_line_breaks_include_blank_and_trailing_rows(qapp, newline):
    widget = InlineReadingsEditor()
    text = f"a{newline}{newline}b{newline}c{newline}"
    widget.set_tokens([{"text": text}])
    widget.resize(600, 400)
    widget.show()
    qapp.processEvents()
    try:
        a, b, c = [control["card"].geometry() for control in widget.controls]
        assert "".join(token["text"] for token in widget.tokens()) == text
        assert [a.left(), b.left(), c.left()] == [a.left()] * 3
        assert b.top() - a.top() > c.top() - b.top() > 0
        assert widget.widget().sizeHint().height() > c.bottom() + c.height()
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("karaoke", [False, True])
def test_editing_with_spaces_preserves_text_timing_and_reading_spaces(editor, monkeypatch, qapp, karaoke):
    text = "  猫\u3000 と  犬\n心  "
    line = LyricLine(1, text, [LyricSegment(1.2, text, end=4)], end=5, has_karaoke=karaoke)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": text}])
    before = deepcopy(line)
    assert len(editor.readings_editor.controls) == 4
    reading = "inu  no\u3000koe"
    edit_reading(editor, 2, reading)
    assert without_readings(line) == without_readings(before)
    start = text.index("犬")
    assert line.segments[0].ruby_spans == [(start, start + 1, reading)]
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert restored.text == text
    assert reading in "".join(value for segment in restored.segments for _, _, value in segment.ruby_spans)
    editor.undo()
    assert editor.lines[0] == before
    assert "".join(token["text"] for token in editor.readings_editor.tokens()) == text


def test_authored_phrase_reading_keeps_its_scope_and_internal_spaces(qapp):
    widget = InlineReadingsEditor()
    token = {"text": "one two", "reading": "wan  too", "start": 4, "end": 11,
             "ruby_start": 4, "ruby_end": 11}
    try:
        widget.set_tokens([token])
        assert len(widget.controls) == 1
        assert widget.tokens() == [token]
    finally:
        widget.deleteLater()


def choose_combine(editor, monkeypatch, *, accepted=True, value="れんたい"):
    def entry(*args, **kwargs):
        assert kwargs["text"] == "れんたい"
        return value, accepted

    def menu_exec(menu, *args):
        action = next(a for a in menu.actions() if a.text() == "Combine reading")
        assert action.isEnabled()
        action.trigger()

    def menu_factory(parent):
        menu = QMenu(parent)
        # PySide binds QMenu.exec's overload onto each instance at construction.
        monkeypatch.setattr(menu, "exec", lambda *args: menu_exec(menu, *args))
        return menu

    monkeypatch.setattr(QInputDialog, "getText", entry)
    monkeypatch.setattr("ui.inline_readings.QMenu", menu_factory)
    controls = editor.readings_editor.controls
    QTest.mouseClick(controls[0]["label"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(controls[1]["label"], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    controls[1]["label"].customContextMenuRequested.emit(QPoint(1, 1))


@pytest.mark.parametrize("karaoke", [False, True])
def test_combine_reading_keeps_timing_and_survives_save_and_undo(editor, monkeypatch, qapp, karaoke):
    line = LyricLine(1, "連帯", [
        LyricSegment(1.2 if karaoke else 1, "連", "れん", end=2 if karaoke else None),
        LyricSegment(2.3 if karaoke else 1, "帯", "たい", instant=karaoke, end=3 if karaoke else None),
    ], end=4 if karaoke else None, has_karaoke=karaoke, style_spans=[(0, 2, True, False, False, False)])
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "連"}, {"text": "帯"}])
    original = deepcopy(line)
    chunks = list(line.segments)
    choose_combine(editor, monkeypatch)
    assert line.reading_groups == [(0, 2)]
    expected = without_readings(original)
    expected["reading_groups"] = [(0, 2)]
    assert without_readings(line) == expected
    assert [s.ruby for s in line.segments] == ["れん", "たい"]
    assert all(a is b for a, b in zip(chunks, line.segments))
    assert line.style_spans == original.style_spans
    assert len(editor.readings_editor.controls) == 1
    assert editor.readings_editor.tokens()[0]["text"] == "連帯"
    assert editor.readings_editor.controls[0]["button"].text() == "れんたい"
    assert "Reading word groups" in editor._lrcx_only_features()
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert restored.reading_groups == [(0, 2)]
    assert restored.text == original.text
    assert restored.style_spans == original.style_spans
    assert editor._furigana_editor_tokens(restored)[0]["reading"] == "れんたい"
    if karaoke:
        assert [(s.start, s.end, s.instant) for s in restored.segments] == [
            (s.start, s.end, s.instant) for s in original.segments]
    editor.undo()
    assert editor.lines[0] == original
    assert len(editor.readings_editor.controls) == 2
    editor.redo()
    assert editor.lines[0].reading_groups == [(0, 2)]
    assert len(editor.readings_editor.controls) == 1
    edit_reading(editor, 0, "つながり")
    assert editor.lines[0].reading_groups == [(0, 2)]
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert editor._furigana_editor_tokens(restored)[0]["reading"] == "つながり"


def test_cancel_combine_leaves_document_unchanged(editor, monkeypatch, qapp):
    line = LyricLine(1, "連帯", [LyricSegment(1, "連", "れん"), LyricSegment(2, "帯", "たい")],
                    has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "連"}, {"text": "帯"}])
    original = deepcopy(line)
    choose_combine(editor, monkeypatch, accepted=False)
    assert editor.lines[0] == original
    assert not editor._undo_stack
    assert not editor.document_dirty


@pytest.mark.parametrize("text", ["連 帯", "連\u3000帯", "連\n帯"])
def test_combine_cannot_cross_spaces_or_rows(qapp, text):
    widget = InlineReadingsEditor()
    widget.set_tokens([{"text": text}])
    widget.show()
    qapp.processEvents()
    try:
        first, last = widget.controls
        QTest.mouseClick(first["label"], Qt.MouseButton.LeftButton)
        QTest.mouseClick(last["label"], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
        assert not widget._can_combine()
    finally:
        widget.close()
        widget.deleteLater()


def test_combined_word_follows_text_edits_and_drops_when_word_changes(editor, monkeypatch, qapp):
    line = LyricLine(1, "連帯", [LyricSegment(1, "連", "れん"), LyricSegment(2, "帯", "たい")],
                    has_karaoke=True)
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "連"}, {"text": "帯"}])
    choose_combine(editor, monkeypatch)
    monkeypatch.setattr("ui.lrc_editor.get_cached_furigana_segments", lambda text: [{"text": text}])
    editor.line_text.setPlainText("前連帯後")
    assert line.reading_groups == [(1, 3)]
    assert any(t["text"] == "連帯" for t in editor.readings_editor.tokens())
    editor.line_text.setPlainText("前連絡後")
    assert not line.reading_groups


def choose_split(editor, monkeypatch, *, accepted=True, cut=1, values=None, index=0):
    from ui.reading_split_dialog import SplitReadingDialog

    def dialog_exec(dialog):
        dialog.boundary.setCurrentIndex(dialog.boundary.findData(cut))
        assert dialog.split_position == cut
        if values is not None:
            dialog.left_reading.setText(values[0])
            dialog.right_reading.setText(values[1])
        return dialog.DialogCode.Accepted if accepted else dialog.DialogCode.Rejected

    def menu_factory(parent):
        menu = QMenu(parent)
        def execute(*args):
            action = next(a for a in menu.actions() if a.text() == "Split reading...")
            assert action.isEnabled()
            assert not next(a for a in menu.actions() if a.text() == "Combine reading").isEnabled()
            action.trigger()
        monkeypatch.setattr(menu, "exec", execute)
        return menu

    monkeypatch.setattr(SplitReadingDialog, "exec", dialog_exec)
    monkeypatch.setattr("ui.inline_readings.QMenu", menu_factory)
    control = editor.readings_editor.controls[index]
    QTest.mouseClick(control["label"], Qt.MouseButton.LeftButton)
    control["label"].customContextMenuRequested.emit(QPoint(1, 1))


@pytest.mark.parametrize("karaoke", [False, True])
@pytest.mark.parametrize("forced_group", [False, True])
def test_split_one_reading_preserves_timing_and_custom_readings_after_reload(
        editor, monkeypatch, qapp, karaoke, forced_group):
    line = LyricLine(1, "連帯", [LyricSegment(1.2 if karaoke else 1, "連帯", "れんたい",
                    end=3 if karaoke else None, instant=karaoke)],
                    end=4 if karaoke else None, has_karaoke=karaoke,
                    style_spans=[(0, 2, True, False, False, False)],
                    reading_groups=[(0, 2)] if forced_group else [])
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "連帯"}])
    original = deepcopy(line)
    chunk = line.segments[0]
    # Deliberately choose a different allocation from the automatic suggestion.
    choose_split(editor, monkeypatch, values=("れ", "んたい"))
    assert line.reading_groups == [(0, 1), (1, 2)]
    assert line.segments[0] is chunk
    expected = without_readings(original)
    expected["reading_groups"] = [(0, 1), (1, 2)]
    assert without_readings(line) == expected
    assert [token["text"] for token in editor.readings_editor.tokens()] == ["連", "帯"]
    assert [token["reading"] for token in editor.readings_editor.tokens()] == ["れ", "んたい"]
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert restored.reading_groups == [(0, 1), (1, 2)]
    assert restored.text == original.text
    assert [token["reading"] for token in editor._furigana_editor_tokens(restored)] == ["れ", "んたい"]
    if karaoke:
        assert [(s.start, s.end, s.instant) for s in restored.segments] == [(1.2, 3, True)]
    editor.undo()
    assert editor.lines[0] == original
    assert len(editor.readings_editor.controls) == 1
    editor.redo()
    assert editor.lines[0].reading_groups == [(0, 1), (1, 2)]
    assert len(editor.readings_editor.controls) == 2


@pytest.mark.parametrize("values", [("れんたい", "こころ"), ("re  n", "ta i")])
def test_split_reading_can_cross_existing_karaoke_chunk_boundary(editor, monkeypatch, qapp, values):
    line = LyricLine(1, "連帯心", [LyricSegment(1.2, "連", "れん", end=2),
        LyricSegment(2.2, "帯心", "たいしん", end=3.5)], end=4, has_karaoke=True,
        reading_groups=[(0, 3)])
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "連帯心"}])
    chunks = list(line.segments)
    original = deepcopy(line)
    choose_split(editor, monkeypatch, cut=2, values=values)
    assert line.reading_groups == [(0, 2), (2, 3)]
    assert all(a is b for a, b in zip(chunks, line.segments))
    assert [(s.start, s.end, s.text) for s in line.segments] == [
        (s.start, s.end, s.text) for s in original.segments]
    assert [token["reading"] for token in editor.readings_editor.tokens()] == list(values)
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path)[0]
    assert [token["reading"] for token in editor._furigana_editor_tokens(restored)] == list(values)


def test_cancel_split_keeps_original_document(editor, monkeypatch, qapp):
    line = LyricLine(1, "連帯", [LyricSegment(1, "連帯", "れんたい")], reading_groups=[(0, 2)])
    load_karaoke(editor, monkeypatch, qapp, line, [{"text": "連帯"}])
    original = deepcopy(line)
    choose_split(editor, monkeypatch, accepted=False)
    assert editor.lines[0] == original
    assert not editor._undo_stack
    assert not editor.document_dirty


def test_split_selected_word_keeps_surrounding_text_and_translations(editor, monkeypatch, qapp):
    line = LyricLine(1, "の連帯\n心", [LyricSegment(1.2, "の", end=1.4),
        LyricSegment(1.5, "連帯", "れんたい", end=3), LyricSegment(3.2, "\n心", end=4)],
        end=5, has_karaoke=True, style_spans=[(1, 3, True, False, False, False)],
        is_blockquote=True, translation_lines=[1])
    load_karaoke(editor, monkeypatch, qapp, line,
                 [{"text": "の"}, {"text": "連帯"}, {"text": "\n"}, {"text": "心"}])
    original = deepcopy(line)
    choose_split(editor, monkeypatch, index=1)
    assert line.reading_groups == [(1, 2), (2, 3)]
    assert line.segments[0] == original.segments[0]
    assert line.segments[2] == original.segments[2]
    expected = without_readings(original)
    expected["reading_groups"] = [(1, 2), (2, 3)]
    assert without_readings(line) == expected


@pytest.mark.parametrize("text,selected,enabled", [("連帯", [0], True), ("心", [0], False),
    ("e\u0301", [0], False), ("連 帯", [0, 2], False)])
def test_split_requires_one_word_with_a_valid_boundary(qapp, text, selected, enabled):
    widget = InlineReadingsEditor()
    try:
        widget.set_tokens([{"text": text}])
        widget._selection = set(selected)
        assert widget._can_split() == enabled
    finally:
        widget.deleteLater()
