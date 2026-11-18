"""Compact word-aligned reading controls for the selected lyric."""

import re

from PySide6.QtCore import Qt, Signal, QRect, QSize, QEvent, QSignalBlocker
from PySide6.QtGui import QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QWidget, QLayout, QVBoxLayout, QLabel, QToolButton, QLineEdit,
    QStackedWidget, QSizePolicy, QSpacerItem, QMenu, QInputDialog, QScrollArea,
)
from core.fonts import content_font


class _Whitespace(QSpacerItem):
    def __init__(self, text, font):
        self.line_break = text in ("\n", "\r", "\r\n", "\u2028", "\u2029")
        metrics = QFontMetrics(font)
        width = 0 if self.line_break else metrics.horizontalAdvance(text.replace("\t", "    "))
        super().__init__(width, 26 + 1 + metrics.height() + 4,
                         QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class _ReadingButton(QToolButton):
    def paintEvent(self, event):
        # Resolve colors while painting so switching themes also updates
        # existing controls instead of retaining stylesheet palette colors.
        painter = QPainter(self)
        palette = self.palette()
        group = palette.currentColorGroup() if self.isEnabled() else QPalette.ColorGroup.Disabled
        active = self.isEnabled() and (self.underMouse() or self.hasFocus())
        role = (QPalette.ColorRole.Highlight if active else
                QPalette.ColorRole.Mid if self.text() == "+" else QPalette.ColorRole.ButtonText)
        painter.setPen(palette.color(group, role))
        if active:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 3, 3)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width() - 6)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class _WordLabel(QLabel):
    selected = False

    def paintEvent(self, event):
        if self.selected:
            painter = QPainter(self)
            color = self.palette().color(QPalette.ColorRole.Highlight)
            color.setAlpha(65)
            painter.fillRect(self.rect(), color)
            painter.end()
        super().paintEvent(event)


class _WordFlow(QLayout):
    def __init__(self, parent):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(4, 4, 4, 4)
        self.setSpacing(6)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def sizeHint(self):
        return self._arrange(QRect(), False)

    def minimumSize(self):
        return self.sizeHint()

    def _arrange(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, height = area.x(), area.y(), 0
        right = x
        previous_word = False
        for item in self.items:
            size = item.sizeHint()
            whitespace = isinstance(item, _Whitespace)
            if whitespace and item.line_break:
                if apply:
                    item.setGeometry(QRect(x, y, 0, size.height()))
                x = area.x()
                y += max(height, size.height()) + self.spacing()
                height = size.height()  # Preserve trailing and empty rows.
                previous_word = False
                continue
            width = size.width()
            padding = self.spacing() if previous_word and not whitespace else 0
            x += padding
            if apply:
                item.setGeometry(QRect(x, y, width, size.height()))
            x += width
            right = max(right, x)
            height = max(height, size.height())
            previous_word = not whitespace
        return QSize(right - rect.x() + margins.right(),
                     y + height - rect.y() + margins.bottom())


class InlineReadingsEditor(QScrollArea):
    readingChanged = Signal(int)
    combineRequested = Signal(int, int, str)
    splitRequested = Signal(int, int, int, str, str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tokens = []
        self.controls = []
        self._updating = False
        self._selection_anchor = None
        self._selection = set()
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._flow = _WordFlow(self._content)
        self._flow.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.setWidget(self._content)
        # setWidget enables a separate background fill on the content.
        # Keep every scroll-area layer transparent to the surrounding panel.
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self._content.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.horizontalScrollBar().rangeChanged.connect(self.updateGeometry)

    def sizeHint(self):
        size = self._flow.sizeHint()
        if self.horizontalScrollBar().maximum() > 0:
            size.setHeight(size.height() + self.horizontalScrollBar().sizeHint().height())
        return QSize(min(640, size.width()), size.height())

    def minimumSizeHint(self):
        return QSize(0, self.sizeHint().height())

    def tokens(self):
        return [dict(token) for token in self._tokens]

    def is_editing(self):
        return any(control["stack"].currentWidget() is control["edit"] for control in self.controls)

    def set_tokens(self, tokens):
        previous_text = "".join(token["text"] for token in self._tokens)
        self._updating = True
        try:
            while self._flow.count():
                widget = self._flow.takeAt(0).widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            self.controls = []
            self._selection_anchor = None
            self._selection = set()
            self._tokens = self._layout_tokens(tokens)
            for index, token in enumerate(self._tokens):
                if token["text"].isspace():
                    self._flow.addItem(_Whitespace(token["text"], self._base_font(token["text"])))
                else:
                    self._add_word(index, token)
        finally:
            self._updating = False
        self._flow.invalidate()
        self._flow.activate()
        if "".join(token["text"] for token in self._tokens) != previous_text:
            self.horizontalScrollBar().setValue(0)
        self.updateGeometry()

    @staticmethod
    def _layout_tokens(tokens):
        """Separate layout whitespace without changing text or annotation scope."""
        result = []
        position = 0
        for token in tokens:
            text = str(token.get("text") or "")
            if not text:
                continue
            start = token.get("start", position)
            reading = token.get("reading")
            # An authored reading may intentionally cover a phrase. Keep that
            # annotation together horizontally, including spaces in its reading.
            pattern = (r"\r\n|[\r\n\u2028\u2029]|[^\r\n\u2028\u2029]+" if reading else
                       r"\r\n|[\r\n\u2028\u2029]|[^\S\r\n\u2028\u2029]+|\S+")
            for match in re.finditer(pattern, text):
                part = dict(token, text=match.group(), start=start + match.start(), end=start + match.end())
                if reading and (match.start() != 0 or match.end() != len(text)):
                    from lyrics.furigana import split_reading_by_text_ranges
                    a = token.get("ruby_start", start)
                    b = token.get("ruby_end", start + len(text))
                    first, last = max(part["start"], a), min(part["end"], b)
                    part["reading"] = (split_reading_by_text_ranges(
                        text[a - start:b - start], reading, [(first - a, last - a)],
                    )[0] if first < last else None)
                    part.pop("ruby_start", None)
                    part.pop("ruby_end", None)
                    if first < last:
                        part.update(ruby_start=first, ruby_end=last)
                result.append(part)
            position += len(text)
        return result

    def _base_font(self, text):
        font = content_font(self.font(), text)
        font.setPointSizeF(max(16, font.pointSizeF() * 1.4))
        return font

    def _add_word(self, index, token):
        base = str(token["text"])
        reading = str(token.get("reading") or "")
        card = QWidget(self._content)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        stack = QStackedWidget(card)
        button = _ReadingButton(stack)
        button.setAutoRaise(True)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setFixedHeight(26)
        button.setFont(content_font(self.font(), reading or "+"))
        button.setText(reading or "+")
        button.setObjectName("readingValue" if reading else "readingAdd")
        button.setAccessibleName(f"Edit reading for {base}" if reading else f"Add reading for {base}")
        button.setToolTip(f"Reading for {base}. Click to edit; clear the field to remove.")
        edit = QLineEdit(reading, stack)
        edit.setFont(content_font(self.font(), reading + base))
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setAccessibleName(f"Reading for {base}")
        edit.setFixedHeight(26)
        edit.installEventFilter(self)
        stack.addWidget(button)
        stack.addWidget(edit)
        stack.setFixedHeight(26)
        label = _WordLabel(base, card)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._base_font(base)
        label.setFont(font)
        label.setWordWrap(False)
        label.setMinimumHeight(QFontMetrics(font).height() + 4)
        layout.addWidget(stack)
        layout.addWidget(label)
        width = max(QFontMetrics(font).horizontalAdvance(base),
                    QFontMetrics(button.font()).horizontalAdvance(reading), 24) + 16
        card.setFixedWidth(width)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        label.setToolTip("Right-click one word to split its reading. Shift-click adjacent pieces to combine readings.")
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        control = dict(button=button, edit=edit, stack=stack, card=card, label=label, index=index)
        self.controls.append(control)
        button.clicked.connect(lambda: self._begin(control))
        edit.editingFinished.connect(lambda: self._finish(control))
        for target in (label, button):
            target.installEventFilter(self)
            target.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            target.customContextMenuRequested.connect(
                lambda point, target=target: self._reading_menu(control, target, point))
        self._flow.addWidget(card)
        # QLayout queues showing new children when the content is already
        # visible. Until then QWidgetItem reports an empty size, allowing the
        # outer panel to cache an 8 px height during an initial cache refresh.
        # Make the card participate before set_tokens measures the new row.
        card.show()

    def _select_word(self, index, extend=False):
        if not extend or self._selection_anchor is None:
            self._selection_anchor = index
        first, last = sorted((self._selection_anchor, index))
        self._selection = {control["index"] for control in self.controls if first <= control["index"] <= last}
        for control in self.controls:
            control["label"].selected = control["index"] in self._selection
            control["label"].update()

    def _can_combine(self):
        if len(self._selection) < 2:
            return False
        first, last = min(self._selection), max(self._selection)
        return not any(char.isspace() for token in self._tokens[first:last + 1] for char in token["text"])

    def _reading_menu(self, control, target, point):
        for item in self.controls:
            if item["stack"].currentWidget() is item["edit"]:
                self._finish(item)
        if control["index"] not in self._selection:
            self._select_word(control["index"])
        menu = QMenu(self)
        action = menu.addAction("Combine reading")
        action.setEnabled(self._can_combine())
        action.triggered.connect(self._combine_selection)
        split = menu.addAction("Split reading...")
        split.setEnabled(self._can_split())
        split.triggered.connect(self._split_selection)
        try:
            menu.exec(target.mapToGlobal(point))
        finally:
            menu.deleteLater()

    def _can_split(self):
        if len(self._selection) != 1:
            return False
        from ui.reading_split_dialog import reading_split_points

        text = self._tokens[next(iter(self._selection))]["text"]
        return not any(char.isspace() for char in text) and bool(reading_split_points(text))

    def _split_selection(self):
        if not self._can_split():
            return
        from ui.reading_split_dialog import SplitReadingDialog

        token = dict(self._tokens[next(iter(self._selection))])
        start, end = token["start"], token["end"]
        a, b = token.get("ruby_start", start), token.get("ruby_end", end)
        dialog = SplitReadingDialog(token["text"], token.get("reading"), self,
                                    reading_range=(a - start, b - start))
        try:
            if dialog.exec() == dialog.DialogCode.Accepted:
                self.splitRequested.emit(start, start + dialog.split_position, end,
                                         dialog.left_reading.text().strip(), dialog.right_reading.text().strip(), a, b)
        finally:
            dialog.deleteLater()

    def _combine_selection(self):
        if not self._can_combine():
            return
        first, last = min(self._selection), max(self._selection)
        tokens = self._tokens[first:last + 1]
        text = "".join(token["text"] for token in tokens)
        reading = "".join(token.get("reading") or
                          (token["text"] if re.fullmatch(r"[\u3040-\u30ff]+", token["text"]) else "")
                          for token in tokens)
        value, accepted = QInputDialog.getText(self, "Combine reading", f"Reading for {text}:", text=reading)
        if accepted:
            self.combineRequested.emit(tokens[0]["start"], tokens[-1]["end"], value.strip())

    def _begin(self, control):
        if not self.isEnabled():
            return
        self._select_word(control["index"])
        edit = control["edit"]
        edit.setText(str(self._tokens[control["index"]].get("reading") or ""))
        control["card"].setFixedWidth(max(control["card"].width(), 96))
        control["stack"].setCurrentWidget(edit)
        edit.setFocus()
        edit.selectAll()
        self._flow.activate()
        self.ensureWidgetVisible(control["card"], 8, 0)

    def _finish(self, control, cancel=False):
        if self._updating or control not in self.controls:
            return
        if control["stack"].currentWidget() is not control["edit"]:
            return
        index = control["index"]
        old = str(self._tokens[index].get("reading") or "")
        value = old if cancel else control["edit"].text().strip()
        # Hiding a focused line edit emits editingFinished synchronously.
        # Block it so one Enter/Escape produces exactly one document operation.
        with QSignalBlocker(control["edit"]):
            control["stack"].setCurrentWidget(control["button"])
        button = control["button"]
        button.setText(value or "+")
        button.setObjectName("readingValue" if value else "readingAdd")
        button.setAccessibleName(f"{'Edit' if value else 'Add'} reading for {self._tokens[index]['text']}")
        button.style().unpolish(button)
        button.style().polish(button)
        font = control["card"].findChild(QLabel).font()
        control["card"].setFixedWidth(max(QFontMetrics(font).horizontalAdvance(self._tokens[index]["text"]),
            QFontMetrics(button.font()).horizontalAdvance(value), 24) + 16)
        if value != old:
            self._tokens[index]["reading"] = value or None
            self._tokens[index]["source"] = "manual" if value else None
            self.readingChanged.emit(index)
        self.updateGeometry()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            control = next((item for item in self.controls if watched in (item["label"], item["button"])), None)
            extend = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if control and (watched is control["label"] or extend):
                for item in self.controls:
                    if item["stack"].currentWidget() is item["edit"]:
                        self._finish(item)
                self._select_word(control["index"], extend)
                return True
        if event.type() == QEvent.Type.KeyPress:
            control = next((item for item in self.controls if item["edit"] is watched), None)
            if control and event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish(control, cancel=event.key() == Qt.Key.Key_Escape)
                control["button"].setFocus()
                return True
        return super().eventFilter(watched, event)
