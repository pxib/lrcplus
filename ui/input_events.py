from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication, QAbstractSlider, QAbstractSpinBox, QComboBox, QLineEdit,
    QPlainTextEdit, QScrollBar, QTextEdit, QWidget,
)


class ControlWheelFilter(QObject):
    """Keep wheel scrolling from adjusting value controls outside playback."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            if (isinstance(watched, QAbstractSpinBox)
                    # Scrollbars share the slider base class but must keep scrolling.
                    or (isinstance(watched, QAbstractSlider)
                        and not isinstance(watched, QScrollBar)
                        and watched.objectName() not in {"seekSlider", "volumeSlider"})
                    or (isinstance(watched, QComboBox)
                        and not watched.view().isVisible())):
                event.ignore()
                return True
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and isinstance(watched, QWidget)
            and watched.window().objectName() == "lrcPlusWindow"
            and QApplication.activeModalWidget() is None
        ):
            player_window = watched.window()
            focus = QApplication.focusWidget()
            if focus is None or focus.window() is not player_window:
                focus = watched
            while focus is not None and focus is not player_window:
                if isinstance(
                    focus, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)
                ):
                    return super().eventFilter(watched, event)
                focus = focus.parentWidget()

            volume_slider = getattr(player_window, "volume_slider", None)
            if isinstance(volume_slider, QAbstractSlider):
                step = volume_slider.singleStep()
                direction = 1 if event.key() == Qt.Key.Key_Up else -1
                volume_slider.setValue(volume_slider.value() + direction * step)
                return True
        return super().eventFilter(watched, event)
