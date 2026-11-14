"""Download queue controls and per-track status."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)


class DownloadQueueView(QWidget):
    def __init__(self, queue, parent=None):
        super().__init__(parent)
        self.queue = queue
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.run = QPushButton("Start / Resume")
        self.run.clicked.connect(lambda: queue.set_paused(not queue.paused))
        row.addWidget(self.run)
        row.addWidget(QLabel("Concurrent tracks:"))
        self.limit = QSpinBox()
        self.limit.setRange(1, 4)
        self.limit.setValue(queue.limit)
        self.limit.valueChanged.connect(queue.set_limit)
        row.addWidget(self.limit)
        row.addStretch()
        layout.addLayout(row)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(["Track", "State", "Progress / details"])
        self.table.setRootIsDecorated(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 100)
        self.table.itemSelectionChanged.connect(self._details)
        layout.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(100)
        layout.addWidget(self.details)
        controls = QHBoxLayout()
        for label, handler in (("Cancel selected", queue.cancel), ("Retry selected", queue.retry),
                               ("Remove selected", queue.remove)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, callback=handler: callback(self._selected()))
            controls.addWidget(button)
        clear = QPushButton("Clear finished")
        clear.clicked.connect(lambda: queue.remove({item["id"] for item in queue.items
                                                    if item["state"] in {"completed", "failed", "cancelled", "skipped"}}))
        controls.addWidget(clear)
        layout.addLayout(controls)
        queue.changed.connect(self.refresh)
        queue.error.connect(self.summary.setText)
        self.refresh()

    def _selected(self):
        return {row.data(0, Qt.ItemDataRole.UserRole) for row in self.table.selectedItems()}

    def _details(self):
        ids = self._selected()
        self.details.setPlainText("\n\n".join(item.get("details") or item.get("message", "")
                                              for item in self.queue.items if item["id"] in ids))

    def refresh(self):
        selected = self._selected()
        scroll = self.table.verticalScrollBar().value()
        self.table.blockSignals(True)
        ids = [self.table.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
               for index in range(self.table.topLevelItemCount())]
        rebuild = ids != [item["id"] for item in self.queue.items]
        if rebuild:
            self.table.clear()
        for index, item in enumerate(self.queue.items):
            values = [item["title"], item["state"].title(), item.get("message", "")]
            if rebuild:
                row = QTreeWidgetItem(values)
                row.setData(0, Qt.ItemDataRole.UserRole, item["id"])
                self.table.addTopLevelItem(row)
                row.setSelected(item["id"] in selected)
            else:
                row = self.table.topLevelItem(index)
                for column, value in enumerate(values):
                    if row.text(column) != value:
                        row.setText(column, value)
            row.setToolTip(2, item.get("message", ""))
        if rebuild:
            self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        self.run.setText("Start / Resume" if self.queue.paused else "Stop starting new downloads")
        complete = sum(item["state"] == "completed" for item in self.queue.items)
        waiting = sum(item["state"] == "waiting" for item in self.queue.items)
        failed = sum(item["state"] == "failed" for item in self.queue.items)
        text = f"{complete} completed · {len(self.queue.jobs)} active · {waiting} waiting · {failed} failed"
        self.summary.setText(text + ("\n" + self.queue.last_error if self.queue.last_error else ""))
        self._details()
