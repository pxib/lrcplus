"""Library rows with numeric sorting for formatted metadata columns."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem


LIBRARY_SORT_ROLE = Qt.ItemDataRole.UserRole + 20


class LibraryTreeItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        left = self.data(column, LIBRARY_SORT_ROLE)
        right = other.data(column, LIBRARY_SORT_ROLE)
        if left is None:
            left = self.text(column).casefold()
        if right is None:
            right = other.text(column).casefold()
        # Group rows have no numeric metadata. Use their heading as a stable
        # fallback when sorting a column whose group cells are empty.
        if left == right:
            return self.text(2).casefold() < other.text(2).casefold()
        return left < right
