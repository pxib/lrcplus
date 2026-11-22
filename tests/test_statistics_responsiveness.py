"""Statistics refreshes avoid repeated measurements of the entire rankings."""

from concurrent.futures import Future
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal, QSize, Qt
from PySide6.QtWidgets import QWidget

from ui.statistics_page import StatisticsPage


class Store(QObject):
    changed = Signal()
    generation = 0

    def snapshot(self, *args):
        return Future()


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.statistics_store = Store(self)
        self.statistics = SimpleNamespace(enabled=False)
        self.appearance_settings = {}

    def _statistics_checkpoint(self):
        pass

    def _clear_statistics(self):
        pass


@pytest.fixture
def page(qapp):
    host = Host()
    page = StatisticsPage(host)
    host.resize(1200, 800)
    page.resize(1200, 800)
    host.show()
    page.show()
    qapp.processEvents()
    yield page
    page.shutdown()
    host.close()
    host.deleteLater()
    qapp.processEvents()


def rankings(count=100):
    stamp = datetime.now().astimezone().isoformat()

    def name(index):
        return f"{index} 日本語 Tiếng Việt 사랑해 " + "Long metadata " * 4

    return {
        "summary": {"listened_ms": count * 1000, "plays": count, "songs": count},
        "daily": [],
        "songs": [dict(title=name(i), artist=name(i + 100), album=name(i + 200),
                       plays=i, listened_ms=i * 1000, last_played=stamp) for i in range(count)],
        "artists": [dict(name=name(i), plays=i, listened_ms=i * 1000) for i in range(count)],
        "albums": [dict(name=name(i), artist=name(i + 100), plays=i,
                        listened_ms=i * 1000) for i in range(count)],
        "stations": [dict(name=name(i), country=name(i + 100), plays=i,
                          listened_ms=i * 1000, last_played=stamp) for i in range(count)],
    }


def test_populated_refresh_does_not_measure_all_ranking_cells(page, qapp, monkeypatch):
    measured = []

    def size_hint(option, index):
        # Count work without making the old quadratic measurement path stall
        # the suite by actually shaping multilingual metadata each time.
        measured.append((index.row(), index.column()))
        return QSize(160, 28)

    for table in page.tables.values():
        monkeypatch.setattr(table.itemDelegate(), "sizeHint", size_hint)
    result = rankings()
    page._apply(result)
    qapp.processEvents()
    changed = deepcopy(result)
    for key in page.tables:
        changed[key][-1]["listened_ms"] += 5000
    page._apply(changed)
    qapp.processEvents()
    assert not measured
    for key, table in page.tables.items():
        assert table.rowCount() == 100
        column = next(index for index in range(table.columnCount())
                      if table.horizontalHeaderItem(index).text() == "Listening time")
        assert table.item(0, column).sort_value == 104000
        # Full metadata remains available when a column elides its text.
        assert table.item(0, 0).text() == table.item(0, 0).toolTip()


def test_unchanged_refresh_keeps_items_selection_sort_and_column_width(page):
    result = rankings(10)
    page._apply(result)
    songs = page.tables["songs"]
    songs.sortItems(3, Qt.SortOrder.AscendingOrder)
    songs.setColumnWidth(1, 211)
    songs.setCurrentCell(4, 0)
    item = songs.currentItem()
    changes = []
    for table in page.tables.values():
        table.model().dataChanged.connect(lambda *args: changes.append(args))

    page._apply(deepcopy(result))

    assert not changes
    assert songs.currentItem() is item
    assert songs.item(0, 3).text() == "0"
    assert songs.columnWidth(1) == 211


def test_changed_rankings_keep_user_sort_and_clear_removed_rows(page):
    result = rankings(10)
    page._apply(result)
    songs = page.tables["songs"]
    songs.sortItems(3, Qt.SortOrder.AscendingOrder)
    changed = deepcopy(result)
    changed["songs"][0]["plays"] = 100
    page._apply(changed)
    assert songs.item(0, 3).text() == "1"
    assert songs.item(9, 3).text() == "100"

    page._apply(rankings(0))
    assert all(table.rowCount() == 0 for table in page.tables.values())
    assert page.summary_labels["plays"].text() == "0"
