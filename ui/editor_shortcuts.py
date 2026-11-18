"""Searchable keyboard and mouse reference for the lyrics editor."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class EditorShortcutsDialog(QDialog):
    def __init__(self, sections, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editor Shortcuts")
        self.resize(760, 620)
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)
        intro = QLabel("Keyboard shortcuts and waveform gestures. Search by control or action.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search controls…")
        self.search.setAccessibleName("Search shortcuts")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Control", "Action"])
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(12)
        self.tree.setAlternatingRowColors(True)
        self.tree.setWordWrap(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        for title, entries in sections:
            group = QTreeWidgetItem(self.tree, [title])
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setFirstColumnSpanned(True)
            for control, action in entries:
                item = QTreeWidgetItem(group, [control, action])
                item.setToolTip(0, control)
                item.setToolTip(1, action)
            group.setExpanded(True)
        layout.addWidget(self.tree, 1)
        self.empty_label = QLabel("No matching controls.")
        self.empty_label.hide()
        layout.addWidget(self.empty_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.search.setFocus()

    def _filter(self, text):
        terms = text.casefold().split()
        matches = 0
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            visible = 0
            for row in range(group.childCount()):
                item = group.child(row)
                haystack = " ".join((group.text(0), item.text(0), item.text(1))).casefold()
                match = all(term in haystack for term in terms)
                item.setHidden(not match)
                visible += int(match)
            group.setHidden(not visible)
            group.setExpanded(True)
            matches += visible
        self.empty_label.setVisible(not matches)
