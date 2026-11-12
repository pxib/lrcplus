"""Open the bundled offline manual in the user's browser."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox


APP_ROOT = Path(__file__).resolve().parents[1]


def open_user_manual(parent=None):
    path = APP_ROOT / "docs" / "manual" / "index.html"
    if not path.is_file():
        QMessageBox.warning(
            parent, "User manual unavailable",
            "The offline user manual is missing. Reinstall LRCPlus to restore it.\n\n"
            f"Expected location: {path}",
        )
        return
    answer = QMessageBox.question(
        parent, "Open user manual?",
        "Open the offline user manual in your default web browser?",
        QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Open:
        return
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        QMessageBox.warning(
            parent, "Could not open user manual",
            f"Open this file in your web browser:\n{path}",
        )
