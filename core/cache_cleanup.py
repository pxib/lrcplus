"""Protect loaded lyrics and visible covers before evicting idle memory caches."""

import sys

from PySide6.QtCore import Qt

from core.idle_cache import expire_idle_caches
from lyrics.line_breaks import reading_source


def _visible_cover_paths(player):
    tree = player.library_table
    if not tree.isVisible():
        return set()
    viewport = tree.viewport().rect()
    item = tree.itemAt(viewport.topLeft())
    if item is None:
        item = tree.topLevelItem(0)
    paths = set()
    while item is not None:
        rect = tree.visualItemRect(item)
        if rect.top() > viewport.bottom():
            break
        # Column zero carries the track path, even if the cover column is hidden.
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and rect.intersects(viewport):
            paths.add(str(path))
        item = tree.itemBelow(item)
    return paths


def clear_idle_caches(app):
    """Run on the GUI thread; closed features are never imported or rebuilt."""
    player_class = getattr(sys.modules.get("ui.main_window"), "Player", ())
    editor_class = getattr(sys.modules.get("ui.lrc_editor"), "LrcEditorDialog", ())
    active_texts = set()
    protected = {}
    for widget in app.topLevelWidgets():
        if isinstance(widget, player_class):
            lines = widget.lyric_lines
            protected[id(widget._library_cover_cache)] = _visible_cover_paths(widget)
        elif isinstance(widget, editor_class) and widget.isVisible():
            lines = widget.lines
        else:
            continue
        for line in lines:
            source = reading_source(line.text, line.translation_lines)
            active_texts.update((line.text, source))
            active_texts.update(line.text.splitlines())

    readings = sys.modules.get("lyrics.furigana")
    if readings is not None:
        segments = readings._SEGMENT_CACHE
        protected[id(segments)] = {key for key in segments if key[-1] in active_texts}
        yomi = readings._YOMI_CACHE
        protected[id(yomi)] = active_texts
    return expire_idle_caches(protected)
