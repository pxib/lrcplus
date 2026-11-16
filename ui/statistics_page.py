"""Local listening dashboard with bounded tables and a Qt-painted chart."""

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QTimer, QSignalBlocker, QRectF
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core.theme import build_theme_palette
from core.fonts import library_font
from ui.content_list import ContentListDelegate
from ui.settings_controls import ThemedComboBox, THEMED_INPUT_STYLE


def listening_time(milliseconds):
    seconds = max(0, round(milliseconds / 1000))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:,}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"


class StatisticItem(QTableWidgetItem):
    def __init__(self, text, value=None):
        super().__init__(str(text))
        self.sort_value = value if value is not None else str(text).casefold()

    def __lt__(self, other):
        return self.sort_value < other.sort_value


class DailyListeningChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.colors = {}
        self.setMinimumHeight(135)
        self.setMaximumHeight(160)
        self.setAccessibleName("Daily listening time chart")
        self.setMouseTracking(True)

    def set_values(self, rows, days):
        today = date.today()
        lookup = {row["day"]: row["listened_ms"] for row in rows}
        first = today - timedelta(days=(days or 365) - 1)
        if days is None and rows:
            first = max(first, date.fromisoformat(rows[0]["day"]))
        self.values = [(first + timedelta(days=i), lookup.get((first + timedelta(days=i)).isoformat(), 0))
                       for i in range((today - first).days + 1)]
        self.update()

    def _plot(self):
        return QRectF(self.rect()).adjusted(56, 12, -12, -25)

    def paintEvent(self, event):
        if not self.colors:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot()
        painter.setPen(QColor(self.colors["border"]))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        maximum = max((amount for _, amount in self.values), default=0)
        painter.setPen(QColor(self.colors["muted"]))
        if not maximum:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No listening time in this range")
            return
        painter.drawText(QRectF(0, 6, 53, 32), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                         listening_time(maximum))
        width = rect.width() / len(self.values)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.colors["accent"]))
        for index, (_day, amount) in enumerate(self.values):
            height = amount / maximum * rect.height()
            painter.drawRoundedRect(QRectF(rect.left() + index * width + width * 0.12,
                                          rect.bottom() - height, max(1, width * 0.76), height), 2, 2)
        painter.setPen(QColor(self.colors["muted"]))
        painter.drawText(QRectF(rect.left(), rect.bottom() + 5, 100, 20),
                         Qt.AlignmentFlag.AlignLeft, self.values[0][0].strftime("%d %b"))
        painter.drawText(QRectF(rect.right() - 100, rect.bottom() + 5, 100, 20),
                         Qt.AlignmentFlag.AlignRight, self.values[-1][0].strftime("%d %b"))

    def mouseMoveEvent(self, event):
        rect = self._plot()
        if self.values and rect.contains(event.position()):
            index = min(len(self.values) - 1, int((event.position().x() - rect.left()) / rect.width() * len(self.values)))
            day, amount = self.values[index]
            self.setToolTip(f"{day:%d %b %Y}: {listening_time(amount)}")
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)


class StatisticsPage(QWidget):
    def __init__(self, player):
        super().__init__(player)
        self.player = player
        self.store = player.statistics_store
        self._future = None
        self._future_generation = None
        self._refresh_again = False
        self._closed = False
        self._storage_error = None
        self.setObjectName("statisticsPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Listening statistics")
        title.setObjectName("statisticsTitle")
        top.addWidget(title)
        top.addStretch()
        self.range_combo = ThemedComboBox()
        self.range_combo.setAccessibleName("Statistics date range")
        for label, days in (("Today", 1), ("Last 7 days", 7), ("Last 30 days", 30), ("All time", None)):
            self.range_combo.addItem(label, days)
        self.range_combo.setCurrentIndex(2)
        self.range_combo.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.range_combo)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)

        cards = QHBoxLayout()
        self.summary_labels = {}
        for key, label in (("listened_ms", "Listening time"), ("plays", "Plays"), ("songs", "Songs heard")):
            card = QFrame()
            card.setObjectName("statisticCard")
            body = QVBoxLayout(card)
            value = QLabel("—")
            value.setObjectName("statisticValue")
            value.setAccessibleName(label)
            self.summary_labels[key] = value
            body.addWidget(value)
            body.addWidget(QLabel(label))
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        self.chart_label = QLabel()
        layout.addWidget(self.chart_label)
        self.chart = DailyListeningChart()
        layout.addWidget(self.chart)

        self.rank_tabs = QTabWidget()
        self.tables = {}
        for key, label, headers in (
            ("songs", "Songs", ["Title", "Artist", "Album", "Plays", "Listening time", "Last played"]),
            ("artists", "Artists", ["Artist", "Plays", "Listening time"]),
            ("albums", "Albums", ["Album", "Artist", "Plays", "Listening time"]),
        ):
            table = QTableWidget(0, len(headers))
            # Metadata needs the same glyph-checked rendering as the library;
            # Qt's default fallback can load large legacy CJK fonts on Windows.
            font = library_font(table.font())
            font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
            table.setFont(font)
            table.setItemDelegate(ContentListDelegate(table))
            table.setWordWrap(False)
            table.setHorizontalHeaderLabels(headers)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.setAlternatingRowColors(True)
            table.verticalHeader().hide()
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.setAccessibleName(f"Top 100 {label.lower()} by listening time")
            self.tables[key] = table
            self.rank_tabs.addTab(table, label)
        layout.addWidget(self.rank_tabs, 1)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        bottom = QHBoxLayout()
        self.tracking_checkbox = QCheckBox("Record listening on this device")
        self.tracking_checkbox.toggled.connect(self._tracking_changed)
        bottom.addWidget(self.tracking_checkbox)
        bottom.addStretch()
        self.clear_button = QPushButton("Clear statistics…")
        self.clear_button.clicked.connect(player._clear_statistics)
        bottom.addWidget(self.clear_button)
        layout.addLayout(bottom)
        self.sync_tracking()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(150)
        self._dirty_timer.timeout.connect(self._refresh_saved)
        self.store.changed.connect(self._mark_dirty)
        self.apply_theme(player.appearance_settings)
        self.status_label.setText("Statistics stay on this device. Recording starts now; earlier listening cannot be recovered.")

    def sync_tracking(self):
        with QSignalBlocker(self.tracking_checkbox):
            self.tracking_checkbox.setChecked(self.player.statistics.enabled)

    def _tracking_changed(self, enabled):
        try:
            self.player._set_statistics_enabled(enabled)
        except OSError as error:
            self.show_error(f"Could not save tracking preference: {error}")
            self.sync_tracking()
        self.refresh()

    def show_error(self, message, *, persistent=False):
        if not self._closed:
            if persistent:
                self._storage_error = message
            self.status_label.setText(message)

    def _mark_dirty(self):
        self._storage_error = None
        if self.isVisible() and not self._closed:
            self._dirty_timer.start()

    def _refresh_saved(self):
        self.refresh(checkpoint=False)

    def refresh(self, *args, checkpoint=True):
        if self._closed or not self.isVisible():
            return
        if self._future is not None:
            self._refresh_again = True
            return
        if checkpoint:
            self.player._statistics_checkpoint()
        self._future_generation = self.store.generation
        self._future = self.store.snapshot(self.range_combo.currentData())
        self._poll_timer.start()

    def _poll(self):
        if self._future is None or not self._future.done():
            return
        future, self._future = self._future, None
        self._poll_timer.stop()
        try:
            result = future.result()
        except Exception as error:
            self.show_error(f"Could not read listening statistics: {error}")
        else:
            if self._future_generation == self.store.generation:
                self._apply(result)
            else:
                self._refresh_again = True
        if self._refresh_again:
            self._refresh_again = False
            self.refresh(checkpoint=False)

    def _apply(self, result):
        summary = result["summary"]
        for key, label in self.summary_labels.items():
            label.setText(listening_time(summary[key]) if key == "listened_ms" else f"{summary[key]:,}")
        days = self.range_combo.currentData()
        self.chart_label.setText("Daily activity · last 365 days" if days is None else "Daily activity")
        self.chart.set_values(result["daily"], days)
        for key, table in self.tables.items():
            table.setSortingEnabled(False)
            table.setRowCount(len(result[key]))
            for row, entry in enumerate(result[key]):
                if key == "songs":
                    stamp = datetime.fromisoformat(entry["last_played"]).astimezone()
                    cells = [(entry["title"], None), (entry["artist"] or "Unknown artist", None),
                             (entry["album"] or "Unknown album", None), (entry["plays"], entry["plays"]),
                             (listening_time(entry["listened_ms"]), entry["listened_ms"]),
                             (stamp.strftime("%d %b %Y, %H:%M"), entry["last_played"])]
                else:
                    cells = [(entry["name"] or ("Unknown artist" if key == "artists" else "Unknown album"), None)]
                    if key == "albums":
                        cells.append((entry["artist"] or "Unknown artist", None))
                    cells += [(entry["plays"], entry["plays"]),
                              (listening_time(entry["listened_ms"]), entry["listened_ms"])]
                for column, (text, value) in enumerate(cells):
                    item = StatisticItem(text, value)
                    item.setToolTip(str(text))
                    table.setItem(row, column, item)
            table.setSortingEnabled(True)
            table.sortItems(table.columnCount() - (2 if key == "songs" else 1), Qt.SortOrder.DescendingOrder)
        if self._storage_error:
            self.status_label.setText(self._storage_error)
        elif summary["listened_ms"]:
            self.status_label.setText("Stored locally · top 100 in each list. A play counts after 30 seconds or half the song, whichever is shorter.")
        else:
            self.status_label.setText("No listening recorded in this range. Play a song with tracking enabled; earlier listening cannot be recovered.")

    def apply_theme(self, settings):
        palette = build_theme_palette(settings.get("theme_mode", "light"), settings.get("theme_color", "#3a9879"),
                                      settings.get("theme_surface", "classic"), settings.get("theme_overrides", {}))
        self.chart.colors = palette
        self.chart.update()
        style = """
            QWidget#statisticsPage { color: @text@; background: @window@; }
            QWidget#statisticsPage QLabel, QWidget#statisticsPage QCheckBox { color: @text@; }
            QLabel#statisticsTitle { font-size: 21px; font-weight: 600; }
            QFrame#statisticCard { background: @surface@; border: 1px solid @border@; border-radius: 7px; }
            QLabel#statisticValue { font-size: 24px; font-weight: 600; }
            QPushButton, QComboBox { color: @text@; background: @surface@; border: 1px solid @field_border@;
                                      border-radius: 5px; padding: 6px 9px; }
            QPushButton:hover { background: @raised_hover@; }
            QTableWidget { color: @text@; background: @surface@; alternate-background-color: @surface_alt@;
                           gridline-color: @border@; selection-background-color: @selection@;
                           selection-color: @selection_text@; border: 1px solid @border@; }
            QHeaderView::section { background: @raised@; color: @muted@; padding: 6px; border: none; }
            QTabWidget::pane { border: 1px solid @border@; }
            QTabBar::tab { color: @muted@; background: @raised@; padding: 7px 16px; }
            QTabBar::tab:selected { color: @text@; background: @surface@; }
            QComboBox QAbstractItemView { color: @text@; background: @surface@;
                                        selection-background-color: @selection@; }
            QScrollBar:vertical { background: @surface_alt@; width: 12px; margin: 0; }
            QScrollBar:horizontal { background: @surface_alt@; height: 12px; margin: 0; }
            QScrollBar::handle { background: @scroll_handle@; border-radius: 4px; min-height: 20px; min-width: 20px; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
        """ + THEMED_INPUT_STYLE
        for key, value in palette.items():
            style = style.replace(f"@{key}@", value)
        self.setStyleSheet(style)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._refresh_timer.start()
        if self._future:
            self._poll_timer.start()

    def hideEvent(self, event):
        self._refresh_timer.stop()
        self._dirty_timer.stop()
        self._poll_timer.stop()
        super().hideEvent(event)

    def shutdown(self):
        self._closed = True
        self._refresh_timer.stop()
        self._dirty_timer.stop()
        self._poll_timer.stop()
        if self._future:
            self._future.cancel()
