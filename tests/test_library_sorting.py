import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget

from ui.library_sorting import LIBRARY_SORT_ROLE, LibraryTreeItem


@pytest.mark.parametrize("column,values,labels", [
    (1, [10, 2], ["10/12", "2/12"]),
    (5, [600, 120], ["10:00", "2:00"]),
    (7, [128, 32], ["128 kbps", "32 kbps"]),
    (8, [1048576, 2048], ["1.00 MB", "2.00 KB"]),
    (11, [1000, 100], ["later", "earlier"]),
])
def test_numeric_columns_sort_by_value_in_both_directions(qapp, column, values, labels):
    tree = QTreeWidget()
    tree.setColumnCount(13)
    group = LibraryTreeItem(["", "", "Album"])
    tree.addTopLevelItem(group)
    for value, label in zip(values, labels):
        item = LibraryTreeItem()
        item.setText(column, label)
        item.setData(column, LIBRARY_SORT_ROLE, value)
        group.addChild(item)
    for direction, expected in [(Qt.SortOrder.AscendingOrder, sorted(values)),
                                (Qt.SortOrder.DescendingOrder, sorted(values, reverse=True))]:
        tree.sortItems(column, direction)
        assert [group.child(i).data(column, LIBRARY_SORT_ROLE) for i in range(2)] == expected


def test_text_sorting_keeps_album_groups_and_ignores_case(qapp):
    tree = QTreeWidget()
    tree.setColumnCount(13)
    for heading in ["Zulu album", "alpha album"]:
        group = LibraryTreeItem(["", "", heading])
        tree.addTopLevelItem(group)
        for title in ["Zoo", "apple"]:
            group.addChild(LibraryTreeItem(["", "", title]))
    tree.sortItems(2, Qt.SortOrder.AscendingOrder)
    assert tree.topLevelItem(0).text(2) == "alpha album"
    assert tree.topLevelItem(0).child(0).text(2) == "apple"
    tree.sortItems(2, Qt.SortOrder.DescendingOrder)
    assert tree.topLevelItem(0).text(2) == "Zulu album"
    assert tree.topLevelItem(0).child(0).text(2) == "Zoo"


def test_player_preserves_sort_when_rebuilding_and_restores_saved_order(qapp):
    from core.music_library import LibraryTrack
    from core.settings import load_music_library
    from ui.main_window import Player

    window = Player()
    try:
        window.library_tracks = [
            LibraryTrack("C:/music/ten.mp3", "Ten", album="Album", track_number="10", duration=600),
            LibraryTrack("C:/music/two.mp3", "Two", album="Album", track_number="2", duration=120),
        ]
        window._populate_library_view()
        window.library_table.sortItems(5, Qt.SortOrder.DescendingOrder)
        window._populate_library_view()
        group = window.library_table.topLevelItem(0)
        assert [group.child(i).text(2) for i in range(2)] == ["Ten", "Two"]
        state = load_music_library()["columns"]
        assert state["sort_column"] == 5
        assert state["sort_descending"] is True
        window.library_table.sortItems(1, Qt.SortOrder.AscendingOrder)
        assert group.child(0).text(2) == "Two"
        window.library_column_state = state
        window._apply_library_column_state()
        assert group.child(0).text(2) == "Ten"
    finally:
        window.close()
        window.deleteLater()
