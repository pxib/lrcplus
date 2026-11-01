from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollBar,
)


class ControlWheelFilter(QObject):
    """Keep wheel scrolling from adjusting value controls across the app."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            if (isinstance(watched, QAbstractSpinBox)
                    # Scrollbars share the slider base class but must keep scrolling.
                    or (isinstance(watched, QAbstractSlider)
                        and not isinstance(watched, QScrollBar))
                    or (isinstance(watched, QComboBox)
                        and not watched.view().isVisible())):
                event.ignore()
                return True
        return super().eventFilter(watched, event)
