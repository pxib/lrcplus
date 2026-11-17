"""Accessible ordering control for lyric display layers."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from core.lyrics_settings import normalize_layer_order


class LyricLayerOrder(QListWidget):
    def __init__(self, order=None, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Lyric layer order")
        self.setToolTip("Drag layers to reorder them. Alt+Up and Alt+Down move the selected layer.")
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setMaximumHeight(150)
        self.visibility_controls = {}
        self.itemChanged.connect(self._toggle_layer)
        self.setValue(order)

    def value(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def setValue(self, order):
        blocked = self.blockSignals(True)
        self.clear()
        for layer in normalize_layer_order(order):
            item = QListWidgetItem(layer.capitalize())
            item.setData(Qt.ItemDataRole.UserRole, layer)
            self.addItem(item)
            if layer in self.visibility_controls:
                self._sync_item(item, self.visibility_controls[layer].isChecked())
        self.blockSignals(blocked)

    def bind_visibility(self, controls):
        self.visibility_controls = controls
        for layer, control in controls.items():
            control.toggled.connect(lambda checked, layer=layer: self._sync_layer(layer, checked))
            self._sync_layer(layer, control.isChecked())

    def _sync_layer(self, layer, checked):
        blocked = self.blockSignals(True)
        for index in range(self.count()):
            item = self.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == layer:
                self._sync_item(item, checked)
        self.blockSignals(blocked)

    def _sync_item(self, item, checked):
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _toggle_layer(self, item):
        layer = item.data(Qt.ItemDataRole.UserRole)
        control = self.visibility_controls.get(layer)
        if control is not None:
            checked = item.checkState() == Qt.CheckState.Checked
            if not checked and not any(
                other.isChecked() for name, other in self.visibility_controls.items() if name != layer
            ):
                self._sync_layer(layer, True)
                return
            control.setChecked(checked)

    def move_current(self, delta):
        row = self.currentRow()
        target = row + delta
        if 0 <= row < self.count() and 0 <= target < self.count():
            item = self.takeItem(row)
            self.insertItem(target, item)
            self.setCurrentRow(target)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.move_current(-1 if event.key() == Qt.Key.Key_Up else 1)
                event.accept()
                return
        super().keyPressEvent(event)
