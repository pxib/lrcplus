"""Application identity, support links, acknowledgements, and a little secret."""

import platform
from pathlib import Path

import PySide6
from PySide6.QtCore import QSize, Qt, QTimer, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL


APP_ROOT = Path(__file__).resolve().parents[1]


def debug_info(plugin_manager=None):
    """Only runtime details; no media names, user paths, or preferences."""
    plugins = getattr(plugin_manager, "plugins", {})
    lines = [
        f"{APP_NAME} {APP_VERSION}",
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {platform.python_version()}",
        f"PySide6: {PySide6.__version__}",
        f"Qt: {qVersion()}",
        "Loaded plugins:",
    ]
    for plugin_id, plugin in sorted(plugins.items()):
        metadata = getattr(plugin, "metadata", {})
        lines.append(f"  {plugin_id}: {metadata.get('version', 'unknown')}")
    if not plugins:
        lines.append("  None")
    return "\n".join(lines)


class AboutTab(QWidget):
    def __init__(self, plugin_manager=None, parent=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.game_window = None
        self._clicks = 0
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(2500)
        self._click_timer.timeout.connect(self._reset_clicks)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addStretch()
        self.logo = QToolButton()
        self.logo.setObjectName("aboutLogo")
        self.logo.setIcon(QIcon(str(APP_ROOT / "assets" / "ico.png")))
        self.logo.setIconSize(QSize(72, 72))
        self.logo.setFixedSize(88, 88)
        self.logo.setAutoRaise(True)
        self.logo.setAccessibleName("LRCPlus logo")
        self.logo.setToolTip("Every song deserves an encore.")
        self.logo.setCursor(Qt.CursorShape.ArrowCursor)
        self.logo.clicked.connect(self._logo_clicked)
        layout.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        title = QLabel(f"<span style='font-size: 25pt; font-weight: 600;'>{APP_NAME}</span>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        version = QLabel(f"Version {APP_VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(version)
        description = QLabel(
            "A music player and lyrics editor with synchronized lyrics,\n"
            "furigana, and karaoke."
        )
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(description)
        links = QHBoxLayout()
        links.addStretch()
        for label, suffix in (("Source code", ""), ("Report a bug", "/issues/new"),
                              ("Release notes", "/blob/main/CHANGELOG.md")):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, path=suffix: QDesktopServices.openUrl(QUrl(PROJECT_URL + path))
            )
            links.addWidget(button)
        links.addStretch()
        layout.addLayout(links)
        actions = QHBoxLayout()
        actions.addStretch()
        self.copy_button = QPushButton("Copy debug info")
        self.copy_button.clicked.connect(self._copy_debug_info)
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(lambda: self.copy_button.setText("Copy debug info"))
        actions.addWidget(self.copy_button)
        credits = QPushButton("Credits & licenses")
        credits.clicked.connect(self._show_credits)
        actions.addWidget(credits)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        copyright_label = QLabel("Made by pxib and contributors · © 2026 pxib · MIT License")
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copyright_label.setWordWrap(True)
        layout.addWidget(copyright_label)

    def _copy_debug_info(self):
        QApplication.clipboard().setText(debug_info(self.plugin_manager))
        self.copy_button.setText("Copied!")
        self._copy_timer.start(2000)

    def _reset_clicks(self):
        self._clicks = 0

    def _logo_clicked(self):
        self._clicks += 1
        self._click_timer.start()
        if self._clicks < 7:
            return
        self._click_timer.stop()
        self._reset_clicks()
        if self.game_window is None:
            from assets.scarlet.danmaku_game import DanmakuWindow

            # Parent to Settings so it stays interactive during Settings.exec().
            self.game_window = DanmakuWindow(self.window())
        self.game_window.show()
        self.game_window.raise_()
        self.game_window.activateWindow()
        self.game_window.setFocus()

    def _show_credits(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Credits & licenses")
        dialog.resize(600, 460)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        credits = QPlainTextEdit()
        credits.setReadOnly(True)
        credits.setPlainText(
            "LRCPlus\nCreated by pxib, with thanks to all contributors.\n\n"
            "Built with Python, PySide6 / Qt, NumPy, sounddevice, aubio-ledfx, "
            "Pedalboard, Pillow, and Fugashi / MeCab.\n\n"
            "Bundled artwork and fonts\n"
            "Noto Sans JP — Noto project contributors (SIL Open Font License 1.1).\n"
            "oneko cat — adryd (MIT).\n\n"
            "Scarlet Refrain\nA Touhou-inspired mini game.\n"
            "Reimu Hakurei sprite — GabrielDrawingsxD.\n"
            "https://www.deviantart.com/gabrieldrawingsxd/art/"
            "My-new-Reimu-Hakurei-sprite-516844635\n"
            "Flandre Scarlet boss sprite — Touhou Project / ZUN.\n"
            "https://en.touhouwiki.net/images/6/6d/Th06FlandreSprite.png\n"
            "Dialogue portraits — new fan illustrations generated with imagegen.\n"
            "Boss-theme MIDI — provided by the user.\n"
            "Game sound effects — WAV files provided by the user.\n"
            "Inspired by Touhou Project by ZUN / Team Shanghai Alice.\n"
            "No affiliation or endorsement.\n\n"
            "Third-party components retain their own licenses."
        )
        tabs.addTab(credits, "Credits")
        for title, path in (("LRCPlus · MIT", "LICENSE.txt"),
                            ("Noto · OFL", "assets/fonts/LICENSE"),
                            ("oneko · MIT", "assets/oneko-LICENSE.txt"),
                            ("Reimu · source", "assets/scarlet/char/reimu-README.md"),
                            ("Flandre · source", "assets/scarlet/char/flandre-README.md")):
            text = QPlainTextEdit()
            text.setReadOnly(True)
            try:
                contents = (APP_ROOT / path).read_text(encoding="utf-8")
            except OSError:
                contents = "License file unavailable in this build. See the project source for license notices."
            text.setPlainText(contents)
            tabs.addTab(text, title)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def hideEvent(self, event):
        self._reset_clicks()
        self._click_timer.stop()
        if self.game_window is not None:
            self.game_window.hide()
        super().hideEvent(event)
