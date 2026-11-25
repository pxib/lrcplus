"""Local audio drops shared by the player and queue, always copying references."""

from math import expm1
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QElapsedTimer, QEvent, QItemSelectionModel,
    QPropertyAnimation, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QTreeWidget

from core.music_library import SUPPORTED_EXTENSIONS
from core.playlists import path_key


def audio_drop_paths(mime, *, folders=False):
    paths, seen = [], set()
    for url in mime.urls() if mime.hasUrls() else ():
        if not url.isLocalFile():
            continue
        try:
            path = Path(url.toLocalFile()).resolve()
            valid = (folders and path.is_dir()) or (
                path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            key = path_key(path)
        except (OSError, ValueError):
            continue
        if valid and key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


class DropIndicator(QLabel):
    """A mouse-transparent destination preview that follows its target's size."""

    def __init__(self, target):
        super().__init__(target)
        self.setObjectName("fileDropIndicator")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        target.installEventFilter(self)

    def preview(self, message):
        palette = self.parentWidget().palette()
        background = palette.color(QPalette.ColorRole.Window)
        accent = palette.color(QPalette.ColorRole.Highlight).name()
        text = palette.color(QPalette.ColorRole.WindowText).name()
        self.setStyleSheet(
            f"QLabel#fileDropIndicator {{ background: rgba({background.red()}, {background.green()}, "
            f"{background.blue()}, 242); color: {text}; border: 3px dashed {accent}; "
            "border-radius: 12px; padding: 24px; font-size: 18px; font-weight: 600; }"
        )
        self.setText(message)
        self.setGeometry(self.parentWidget().rect().adjusted(8, 8, -8, -8))
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect().adjusted(8, 8, -8, -8))
        return super().eventFilter(watched, event)


class LibraryTrackTree(QTreeWidget):
    """Library tree with click and hold gestures for auto-scroll."""

    _AUTOSCROLL_HOLD_MS = 250
    _AUTOSCROLL_DEAD_ZONE = 12
    _AUTOSCROLL_FULL_SPEED_DISTANCE = 220
    _AUTOSCROLL_MAX_PAGES_PER_SECOND = 1.5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._autoscroll_origin = None
        self._autoscroll_press = QElapsedTimer()
        self._autoscroll_dragged = False
        self._autoscroll_remainder = 0.0
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(16)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)
        self.viewport().setMouseTracking(True)

        bar = self.verticalScrollBar()
        self._wheel_animation = QPropertyAnimation(bar, b"value", self)
        self._wheel_animation.setDuration(150)
        self._wheel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        bar.actionTriggered.connect(self._wheel_animation.stop)
        bar.rangeChanged.connect(self._wheel_animation.stop)
        bar.valueChanged.connect(self._cancel_interrupted_wheel_animation)

    def set_smooth_scrolling(self, enabled):
        mode = self.ScrollMode.ScrollPerPixel if enabled else self.ScrollMode.ScrollPerItem
        if self.verticalScrollMode() != mode:
            self._wheel_animation.stop()
            self.setVerticalScrollMode(mode)

    def _cancel_interrupted_wheel_animation(self, value):
        animation = self._wheel_animation
        if animation.state() == QAbstractAnimation.State.Running and value != animation.currentValue():
            animation.stop()

    def wheelEvent(self, event):
        animation = self._wheel_animation
        delta = event.angleDelta()
        if (self.verticalScrollMode() != self.ScrollMode.ScrollPerPixel
                or self._autoscroll_origin is not None or not event.pixelDelta().isNull()
                or not delta.y() or delta.x() or event.modifiers() != Qt.KeyboardModifier.NoModifier):
            animation.stop()
            super().wheelEvent(event)
            return

        bar = self.verticalScrollBar()
        distance = -delta.y() * QApplication.wheelScrollLines() * bar.singleStep() / 120
        current = bar.value()
        base = current
        if animation.state() == QAbstractAnimation.State.Running:
            pending = animation.endValue()
            if (pending - current) * distance > 0:
                base = pending
        target = max(bar.minimum(), min(bar.maximum(), round(base + distance)))
        animation.stop()
        if target != current:
            animation.setStartValue(current)
            animation.setEndValue(target)
            animation.start()
        event.accept()

    def mousePressEvent(self, event):
        self._wheel_animation.stop()
        if self._autoscroll_origin is not None and event.button() != Qt.MouseButton.MiddleButton:
            self._stop_autoscroll()
        if event.button() == Qt.MouseButton.MiddleButton:
            if self._autoscroll_origin is None:
                self._autoscroll_origin = event.position().toPoint()
                self._autoscroll_pointer_y = self._autoscroll_origin.y()
                self._autoscroll_press.start()
                self._autoscroll_dragged = False
                self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
                QApplication.instance().installEventFilter(self)
                self._autoscroll_timer.start()
            else:
                self._stop_autoscroll()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            if self._autoscroll_press.isValid():
                distance = (event.position().toPoint() - self._autoscroll_origin).manhattanLength()
                if (self._autoscroll_dragged or distance >= QApplication.startDragDistance()
                        or self._autoscroll_press.elapsed() >= self._AUTOSCROLL_HOLD_MS):
                    self._stop_autoscroll()
                else:
                    self._autoscroll_press.invalidate()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.mousePressEvent(event)
            return
        if self._autoscroll_origin is not None:
            self._stop_autoscroll()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._autoscroll_origin is not None:
            point = event.position().toPoint()
            self._autoscroll_pointer_y = point.y()
            if self._autoscroll_press.isValid():
                distance = (point - self._autoscroll_origin).manhattanLength()
                self._autoscroll_dragged |= distance >= QApplication.startDragDistance()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def eventFilter(self, watched, event):
        if self._autoscroll_origin is not None and (
            event.type() == QEvent.Type.ApplicationDeactivate
            or (watched is not self.viewport() and event.type() in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
            ))
        ):
            self._stop_autoscroll()
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        self._wheel_animation.stop()
        if self._autoscroll_origin is not None:
            self._stop_autoscroll()
        super().hideEvent(event)

    def _autoscroll_tick(self):
        if self._autoscroll_origin is None:
            return
        distance = self._autoscroll_pointer_y - self._autoscroll_origin.y()
        offset = abs(distance) - self._AUTOSCROLL_DEAD_ZONE
        if offset <= 0:
            self._autoscroll_remainder = 0.0
            return

        bar = self.verticalScrollBar()
        direction = 1 if distance > 0 else -1
        if self._autoscroll_remainder * direction < 0:
            self._autoscroll_remainder = 0.0
        span = self._AUTOSCROLL_FULL_SPEED_DISTANCE - self._AUTOSCROLL_DEAD_ZONE
        progress = min(1.0, offset / span)
        speed = expm1(3 * progress) / expm1(3)
        units_per_second = speed * self._AUTOSCROLL_MAX_PAGES_PER_SECOND * bar.pageStep()
        self._autoscroll_remainder += direction * units_per_second * self._autoscroll_timer.interval() / 1000
        # Keep fractions so even speeds below one scrollbar unit per tick work.
        step = int(self._autoscroll_remainder)
        if step:
            self._autoscroll_remainder -= step
            bar.setValue(bar.value() + step)

    def _stop_autoscroll(self):
        self._autoscroll_origin = None
        self._autoscroll_remainder = 0.0
        self._autoscroll_press.invalidate()
        QApplication.instance().removeEventFilter(self)
        self._autoscroll_timer.stop()
        self.viewport().unsetCursor()

    def mimeData(self, items):
        mime = super().mimeData(items)
        paths, seen = [], set()
        for item in items:
            children = [item.child(i) for i in range(item.childCount())] if item.childCount() else [item]
            for child in children:
                if child.isHidden():
                    continue
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path and path_key(path) not in seen:
                    seen.add(path_key(path))
                    paths.append(QUrl.fromLocalFile(str(path)))
        mime.setUrls(paths)
        return mime

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction


class QueueTrackList(QListWidget):
    files_dropped = Signal(object)

    def _preview_drop(self, paths):
        if not hasattr(self, "_drop_indicator"):
            self._drop_indicator = DropIndicator(self.viewport())
        count = len(paths)
        self._drop_indicator.preview(
            f"Append {count} {'track' if count == 1 else 'tracks'} to the end of the queue\n\n"
            "Playback continues. Added tracks will be selected."
        )

    def _hide_drop_indicator(self):
        if hasattr(self, "_drop_indicator"):
            self._drop_indicator.hide()

    def dragEnterEvent(self, event):
        paths = audio_drop_paths(event.mimeData())
        if paths:
            self._preview_drop(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self._hide_drop_indicator()
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        paths = audio_drop_paths(event.mimeData())
        if paths:
            self._preview_drop(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self._hide_drop_indicator()
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._hide_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._hide_drop_indicator()
        paths = audio_drop_paths(event.mimeData())
        if paths:
            start = self.count()
            self.files_dropped.emit(paths)
            if self.count() > start:
                self.clearSelection()
                for index in range(start, self.count()):
                    self.item(index).setSelected(True)
                self.setCurrentRow(start, QItemSelectionModel.SelectionFlag.NoUpdate)
                self.scrollToItem(self.item(start))
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            super().dropEvent(event)
