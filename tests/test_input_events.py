from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QLineEdit, QSlider, QWidget

from ui.input_events import ControlWheelFilter
from ui.player_dialogs import ClickableSlider


def _wheel_event():
    return QWheelEvent(
        QPointF(1, 1),
        QPointF(1, 1),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_wheel_changes_playback_sliders_but_not_other_sliders(qapp):
    player = QWidget()
    player.setObjectName("lrcPlusWindow")
    seek_slider = ClickableSlider(Qt.Orientation.Horizontal, player)
    seek_slider.setObjectName("seekSlider")
    seek_slider.setRange(0, 60000)
    seek_slider.setSingleStep(5000)
    seek_slider.setPageStep(5000)
    volume_slider = QSlider(Qt.Orientation.Horizontal, player)
    volume_slider.setObjectName("volumeSlider")
    volume_slider.setRange(0, 300)
    volume_slider.setSingleStep(5)
    volume_slider.setPageStep(5)
    other_slider = QSlider(Qt.Orientation.Horizontal, player)
    other_slider.setRange(0, 100)
    seek_requests = []
    seek_slider.wheelSeekRequested.connect(seek_requests.append)

    event_filter = ControlWheelFilter(qapp)
    qapp.installEventFilter(event_filter)
    try:
        qapp.sendEvent(seek_slider, _wheel_event())
        qapp.sendEvent(volume_slider, _wheel_event())
        qapp.sendEvent(other_slider, _wheel_event())
    finally:
        qapp.removeEventFilter(event_filter)

    assert seek_slider.value() == 5000
    assert seek_requests == [5000]
    assert volume_slider.value() == 5
    assert other_slider.value() == 0


def test_up_and_down_keys_adjust_volume_but_not_text_entry(qapp):
    player = QWidget()
    player.setObjectName("lrcPlusWindow")
    player.volume_slider = QSlider(Qt.Orientation.Horizontal, player)
    player.volume_slider.setRange(0, 300)
    player.volume_slider.setSingleStep(5)
    player.volume_slider.setValue(50)
    event_filter = ControlWheelFilter(qapp)
    target = QWidget(player)
    target.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    text_input = QLineEdit(player)
    player.show()
    qapp.installEventFilter(event_filter)
    try:
        target.setFocus()
        qapp.processEvents()
        up = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier
        )
        qapp.sendEvent(target, up)
        assert player.volume_slider.value() == 55

        down = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier
        )
        qapp.sendEvent(target, down)
        assert player.volume_slider.value() == 50

        text_input.setFocus()
        qapp.processEvents()
        typing_up = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier
        )
        qapp.sendEvent(text_input, typing_up)
        assert player.volume_slider.value() == 50
    finally:
        qapp.removeEventFilter(event_filter)
        player.close()
