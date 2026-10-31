"""Dependency management shared by Settings and the startup notice."""
from __future__ import annotations

import hashlib

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QHeaderView, QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from core.dependencies import DependencyError
from core.external_tools import ExternalToolManager, TOOLS
from core.settings import load_all_settings, save_all_settings
from ui.settings_controls import _apply_settings_dialog_style


class _DependencyJob(QThread):
    message = Signal(str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, manager, action, name, parent=None, kind="library"):
        super().__init__(parent)
        self.manager, self.action, self.name = manager, action, name
        self.kind = kind

    def run(self):
        try:
            if self.action == "check":
                result = self.manager.check(self.name)
                self.message.emit(result if self.kind == "tool" else f"{self.name}: import check passed.")
            else:
                getattr(self.manager, self.action)(self.name, self.message.emit)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit()


class DependencyManagerWidget(QWidget):
    def __init__(self, plugin_manager, parent=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.manager = plugin_manager.dependency_manager if plugin_manager else None
        self.tools = ExternalToolManager()
        self._job = None
        self._checks = {}
        self._buttons = {}
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Manage optional libraries and tools. FFmpeg includes ffprobe; Deno provides JavaScript for YouTube. "
            "Tool installs are private to LRCPlus and work for new imports immediately. "
            "Restart after changing Python libraries. Their first installation also downloads a private Python runtime."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Library or tool", "Used by", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(self._update_buttons)
        layout.addWidget(self.tree, 1)
        actions = QHBoxLayout()
        for label, action in (("Install / Repair", "install"), ("Check", "check"), ("Remove", "remove")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, a=action: self._start_selected(a))
            self._buttons[action] = button
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.action_hint = QLabel()
        self.action_hint.setWordWrap(True)
        layout.addWidget(self.action_hint)
        self.storage = QLabel()
        self.storage.setWordWrap(True)
        self.storage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.storage)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(500)
        self.details.setMaximumHeight(150)
        self.details.hide()
        layout.addWidget(self.details)
        self.details_button = QPushButton("Show details")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.refresh()

    @property
    def busy(self):
        return self._job is not None

    def refresh(self):
        current = self.tree.currentItem()
        selected = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        selected_kind = current.data(0, Qt.ItemDataRole.UserRole + 1) if current else None
        self.tree.clear()
        self.storage.setText(
            (f"Libraries: {self.manager.root}\n" if self.manager else "") + f"Tools: {self.tools.root}"
        )
        try:
            state = self.manager.state() if self.manager else None
            for name, entry in sorted(self.manager.catalog.items() if self.manager else []):
                status = self.manager.status(name, state=state)
                check = self._checks.get(("library", name))
                text = status["text"]
                if check:
                    text += "; " + ("Import check passed" if check == "ok" else "Import failed — see details")
                requirements = ", ".join(sorted({item.requirement for item in entry["dependencies"]}))
                item = QTreeWidgetItem([requirements, ", ".join(sorted(set(entry["plugins"]))), text])
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "library")
                support = sorted({value for dep in entry["dependencies"] for value in dep.requires})
                item.setToolTip(0, requirements + ("\nAlso installs: " + ", ".join(support) if support else ""))
                self.tree.addTopLevelItem(item)
                item.setToolTip(2, check if check and check != "ok" else status["text"])
                if name == selected and selected_kind == "library":
                    self.tree.setCurrentItem(item)
        except DependencyError as error:
            self.status.setText(str(error))
        for name, tool in TOOLS.items():
            status = self.tools.status(name)
            check = self._checks.get(("tool", name))
            text = status["text"]
            if check:
                text += "; " + ("Check passed" if check == "ok" else "Check failed — see details")
            item = QTreeWidgetItem([tool["label"], tool["used_by"], text])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, "tool")
            item.setToolTip(0, f"Download source: https://github.com/{tool['repo']}")
            item.setToolTip(2, check if check and check != "ok" else status["text"])
            self.tree.addTopLevelItem(item)
            if name == selected and selected_kind == "tool":
                self.tree.setCurrentItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._update_buttons()

    def _update_buttons(self, *_args):
        self.action_hint.clear()
        for button in self._buttons.values():
            button.setEnabled(False)
        item = self.tree.currentItem()
        if self.busy or item is None:
            return
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if item.data(0, Qt.ItemDataRole.UserRole + 1) == "tool":
            status = self.tools.status(name)
            self._buttons["install"].setEnabled(self.tools.supported(name))
            self._buttons["install"].setToolTip("Download the latest verified release for LRCPlus.")
            self._buttons["check"].setEnabled(True)
            self._buttons["remove"].setEnabled(status["managed"])
            self._buttons["remove"].setToolTip("Remove only the copy installed by LRCPlus.")
            self.action_hint.setText(
                "Install / Repair downloads the latest version for LRCPlus. No administrator access is needed. "
                "Remove keeps tools installed elsewhere. New imports pick up changes immediately."
                if self.tools.supported(name) else
                "Automatic installation is unavailable on this platform. Install the tool on PATH; Check can verify it."
            )
            return
        if self.manager is None:
            return
        try:
            status = self.manager.status(name)
            self._buttons["install"].setEnabled(status["source"] != "app")
            self._buttons["install"].setToolTip(
                "This library is supplied by the app. Update the app or its Python environment to repair it."
                if status["source"] == "app" else "Install or repair this library and its supporting packages."
            )
            # An import probe is useful even when metadata is missing or the
            # installed version does not meet the plugin's requirement.
            self._buttons["check"].setEnabled(True)
            explicit = name in self.manager.state()["requirements"]
            self._buttons["remove"].setEnabled(explicit)
            self._buttons["remove"].setToolTip("Removes this explicit installation. Other libraries may still require it.")
            if status["source"] == "app":
                self.action_hint.setText(
                    "Bundled with LRCPlus or its Python environment. Check is available; "
                    "installing or removing a downloaded copy cannot replace this library. "
                    "Update LRCPlus to repair a bundled version or missing version information."
                )
            elif not explicit and status["state"] != "missing":
                self.action_hint.setText(
                    "Installed as a supporting library. Install / Repair adds an explicit installation; "
                    "Remove applies to explicit installations only."
                )
        except DependencyError as error:
            self.action_hint.setText(str(error))

    def _start_selected(self, action):
        item = self.tree.currentItem()
        if item:
            self._start(action, item.data(0, Qt.ItemDataRole.UserRole),
                        item.data(0, Qt.ItemDataRole.UserRole + 1))

    def _start(self, action, name, kind="library"):
        if self.busy:
            return
        self.details.clear()
        self.status.setText(f"{'Checking' if action == 'check' else 'Removing' if action == 'remove' else 'Installing'} {name}…")
        self._checks.pop((kind, name), None)
        manager = self.tools if kind == "tool" else self.manager
        self._job = _DependencyJob(manager, action, name, self, kind=kind)
        self._job.message.connect(self._message)
        self._job.failed.connect(lambda error: self._failed(action, name, error, kind))
        self._job.succeeded.connect(lambda: self._succeeded(action, name, kind))
        self._job.finished.connect(self._finished)
        self.progress.show()
        for button in self._buttons.values():
            button.setEnabled(False)
        self._job.start()

    def _message(self, message):
        self.details.appendPlainText(message)
        if "\n" not in message:
            self.status.setText(message)

    def _failed(self, action, name, error, kind="library"):
        if action == "check":
            self._checks[(kind, name)] = error
        self.status.setText(f"{name}: operation failed. See details for the cause.")
        self.details.appendPlainText(error)
        self.details_button.setChecked(True)

    def _succeeded(self, action, name, kind="library"):
        if action == "check":
            self._checks[(kind, name)] = "ok"
            self.status.setText(f"{name}: {'tool' if kind == 'tool' else 'import'} check passed.")
        else:
            self._checks.clear()
            self.status.setText("Tools updated. New imports use the current tools immediately."
                                if kind == "tool" else
                                "Dependencies updated. Restart LRCPlus to apply the changes.")

    def _finished(self):
        job = self._job
        self._job = None
        self.progress.hide()
        self.refresh()
        job.deleteLater()


class DependencyManagerDialog(QDialog):
    def __init__(self, plugin_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dependency Manager")
        self.resize(900, 520)
        _apply_settings_dialog_style(
            self, getattr(parent, "theme_mode", "light"),
            getattr(parent, "theme_color", "#3a9879"),
        )
        layout = QVBoxLayout(self)
        self.dependencies = DependencyManagerWidget(plugin_manager, self)
        layout.addWidget(self.dependencies)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reject(self):
        if not self.dependencies.busy:
            super().reject()
        else:
            self.dependencies.status.setText("Please wait for the dependency operation to finish before closing.")


def show_dependency_notice(plugin_manager, parent):
    """Offer setup once for each changed set of missing enabled dependencies."""
    if not plugin_manager.dependency_errors:
        return
    details = "\n".join(
        f"{plugin_id}: {error}"
        for plugin_id, error in sorted(plugin_manager.dependency_errors.items())
    )
    signature = hashlib.sha256(details.encode("utf-8")).hexdigest()
    settings = load_all_settings()
    if settings.get("dependency_notice_dismissed") == signature:
        return
    notice = QMessageBox(parent)
    notice.setWindowTitle("Plugin dependencies")
    notice.setText("Some enabled plugins need additional libraries.")
    notice.setInformativeText("Open the Dependency Manager to install them or inspect the problem.")
    notice.setDetailedText(details)
    open_button = notice.addButton("Open Dependency Manager", QMessageBox.ButtonRole.AcceptRole)
    notice.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    notice.exec()
    settings = load_all_settings()
    settings["dependency_notice_dismissed"] = signature
    save_all_settings(settings)
    if notice.clickedButton() is open_button:
        DependencyManagerDialog(plugin_manager, parent).exec()
