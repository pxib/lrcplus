"""Small, session-only surprises for the player."""

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap, QShortcut, QAction
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QLabel, QLineEdit, QMenuBar,
    QMessageBox, QPlainTextEdit, QStatusBar, QStyle, QStyleOptionSlider,
    QTextEdit, QVBoxLayout, QWidget,
)


class BudgetBrandLabel(QLabel):
    budget_cut_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("LRCPlus", parent)
        self._clicks = 0
        self._reset = QTimer(self)
        self._reset.setSingleShot(True)
        self._reset.setInterval(2500)
        self._reset.timeout.connect(self._reset_clicks)

    def _reset_clicks(self):
        self._clicks = 0

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._clicks += 1
            self._reset.start()
            if self._clicks == 7:
                self._reset.stop()
                self._reset_clicks()
                self.budget_cut_requested.emit()
        super().mousePressEvent(event)


class PlaybackCat(QWidget):
    """Animate the bundled oneko sprite sheet without intercepting input."""

    SPRITE_SIZE = 32
    RUNNING_FRAMES = ((3, 0), (3, 1))
    SLEEPING_FRAMES = ((2, 0), (2, 1))

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(self.SPRITE_SIZE, self.SPRITE_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Playback cat")
        self.playing = False
        self.frame = 0
        self.spritesheet = QPixmap(str(Path(__file__).resolve().parents[1] / "assets" / "oneko.gif"))
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        self.frame += 1
        self.update()

    def set_playing(self, playing):
        if self.playing != playing:
            self.frame = 0
        self.playing = playing
        self._sync_timer()
        self.setToolTip("Running" if playing else "Napping")
        self.update()

    def _sync_timer(self):
        if self.isVisible():
            self.timer.start(1000 if self.playing else 400)
        else:
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        frames = self.RUNNING_FRAMES if self.playing else self.SLEEPING_FRAMES
        column, row = frames[self.frame % len(frames)]
        size = self.SPRITE_SIZE
        painter.drawPixmap(self.rect(), self.spritesheet, QRect(column * size, row * size, size, size))


class CatSeekTrack(QWidget):
    """Reserve space above the unchanged, fully interactive seek slider."""

    def __init__(self, slider, media_player):
        super().__init__()
        self.slider = slider
        self.cat_enabled = False
        self.perch = QWidget(self)
        self.perch.setFixedHeight(34)
        self.cat = PlaybackCat(self.perch)
        self.perch.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.perch)
        layout.addWidget(slider)
        slider.valueChanged.connect(self._move_cat)
        slider.rangeChanged.connect(self._move_cat)
        slider.installEventFilter(self)
        media_player.playbackStateChanged.connect(self._playback_changed)
        self._playback_changed(media_player.playbackState())

    def toggle_cat(self):
        self.cat_enabled = not self.cat_enabled
        self.perch.setVisible(self.cat_enabled)
        self._move_cat()

    def _playback_changed(self, state):
        self.cat.set_playing(state == QMediaPlayer.PlaybackState.PlayingState)

    def _move_cat(self, *_):
        option = QStyleOptionSlider()
        self.slider.initStyleOption(option)
        handle = self.slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderHandle, self.slider,
        )
        left = self.slider.x() - self.perch.x() + handle.center().x() - self.cat.width() // 2
        self.cat.move(
            max(0, min(left, self.perch.width() - self.cat.width())),
            self.perch.height() - self.cat.height(),
        )

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.StyleChange, QEvent.Type.Show):
            self._move_cat()
        return False


class EasterEggs(QObject):
    KONAMI = (
        Qt.Key.Key_Up, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Down,
        Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Left, Qt.Key.Key_Right,
        Qt.Key.Key_B, Qt.Key.Key_A,
    )

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.minus_player = None
        self._extra_windows = {}
        self._sequence = ()
        self._last_key = 0
        host.brand_label.budget_cut_requested.connect(self.show_budget_dialog)
        host.windowTitleChanged.connect(self._title_changed)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if (self.minus_player is not None and event.type() == QEvent.Type.Show
                and watched in self._extra_windows):
            watched.hide()
            return True
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        if not isinstance(watched, QWidget) or watched.window() is not self.host:
            return False
        if self.minus_player is not None or QApplication.activeModalWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            self._sequence = ()
            return False
        if monotonic() - self._last_key > 2.5:
            self._sequence = ()
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            self._sequence = ()
            return False
        if event.isAutoRepeat():
            return False
        key = event.key()
        if event.type() == QEvent.Type.ShortcutOverride:
            if key == self.KONAMI[0] or (self._sequence and key == self.KONAMI[len(self._sequence)]):
                event.accept()
                return True
            return False
        self._last_key = monotonic()
        candidate = self._sequence + (key,)
        # Keep the longest matching prefix, including overlapping Up presses.
        self._sequence = next(
            (candidate[-size:] for size in range(min(len(candidate), len(self.KONAMI)), 0, -1)
             if candidate[-size:] == self.KONAMI[:size]), ()
        )
        if self._sequence == self.KONAMI:
            self._sequence = ()
            self.host.seek_track.toggle_cat()
            return True
        return bool(self._sequence)

    def show_budget_dialog(self):
        if self.minus_player is not None:
            return
        box = QMessageBox(self.host)
        box.setWindowTitle("LRCMinus — Budget cuts")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Budget cuts. We're LRCMinus now.")
        box.setInformativeText(
            "All we can afford is Furigana Player.\n"
            "Your song keeps playing. You can restore the budget whenever you like."
        )
        alright = box.addButton("Alright?", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Keep LRCPlus", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(alright)
        box.setEscapeButton(cancel)
        self.host.brand_label.setText("LRCMinus")
        try:
            box.exec()
            if box.clickedButton() is alright:
                self.enter_minus()
        finally:
            self.host.brand_label.setText("LRCPlus")

    def enter_minus(self):
        if self.minus_player is not None:
            return
        from assets.apps.furiganaplayer import EmbeddedPlayer

        host = self.host
        controls = host.findChildren(QShortcut) + host.findChildren(QAction)
        player = EmbeddedPlayer(host, self.return_to_plus)
        self._geometry = host.normalGeometry()
        if not self._geometry.isValid():
            self._geometry = host.geometry()
        self._window_state = host.windowState()
        self._style = host.styleSheet()
        self._title = host.windowTitle()
        self._controls = [(control, control.isEnabled()) for control in controls]
        self._bars = [(bar, not bar.isHidden()) for bar in (
            host.findChildren(QMenuBar) + host.findChildren(QStatusBar)
        )]
        for control, _ in self._controls:
            control.setEnabled(False)
        for bar, _ in self._bars:
            bar.hide()
        self._surface = host.takeCentralWidget()
        self._surface.setParent(host)
        self._surface.hide()
        self.minus_player = player
        self._extra_windows = {
            window: window.isVisible() for window in QApplication.topLevelWidgets()
            if window is not host and window is not self._surface
        }
        for window in self._extra_windows:
            window.hide()
        host.setCentralWidget(player)
        host.setStyleSheet("")
        host.setWindowTitle("Furigana Player")
        host.showNormal()
        host.layout().activate()
        host.resize(700, 400)
        player.show()
        player.play_button.setFocus()

    def _title_changed(self, title):
        if self.minus_player is not None and title != "Furigana Player":
            self._title = title
            self.host.setWindowTitle("Furigana Player")

    def return_to_plus(self):
        if self.minus_player is None:
            return
        host = self.host
        player = host.takeCentralWidget()
        player.hide()
        player.setEnabled(False)
        self.minus_player = None
        host.setCentralWidget(self._surface)
        host.setStyleSheet(self._style)
        host.setWindowTitle(self._title)
        host.setGeometry(self._geometry)
        host.setWindowState(self._window_state)
        for control, enabled in self._controls:
            control.setEnabled(enabled)
        for bar, visible in self._bars:
            bar.setVisible(visible)
        self._surface.show()
        live_windows = QApplication.topLevelWidgets()
        for window, visible in self._extra_windows.items():
            if visible and window in live_windows:
                window.show()
        self._extra_windows = {}
        self._surface = None
        self._controls = []
        self._bars = []
        player.deleteLater()
        host._handle_workspace_changed(host.workspace_stack.currentIndex())
