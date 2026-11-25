"""Dependency management shared by Settings and the startup notice."""
from __future__ import annotations

import hashlib
from collections import deque
from datetime import datetime
import platform
import sys
import threading
import time
import traceback

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QHeaderView, QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from core.dependencies import DependencyError, YTDLP_CHANNELS
from core.dependency_tasks import DependencyCancelled, cancellation_scope, diagnostic, diagnostic_scope
from core.external_tools import ExternalToolManager, TOOLS
from core.settings import load_all_settings, save_all_settings


# Describe the feature first; keep exact requirements visible for troubleshooting.
_PACKAGE_FEATURES = {
    "fugashi": "Japanese readings engine",
    "unidic-lite": "Japanese reading dictionary",
    "yt-dlp": "Online audio downloads",
    "mutagen": "Audio tags and cover art",
    "pypresence": "Discord listening status",
    "requests": "Online connections",
    "argostranslate": "Offline lyric translation",
    "alkana": "English words in katakana",
    "koroman": "Korean romanization",
    "pycantonese": "Cantonese readings (Jyutping)",
}
_TOOL_FEATURES = {
    "ffmpeg": "Audio import and waveforms",
    "deno": "YouTube download support",
}


class _DependencyJob(QThread):
    message = Signal(str)
    succeeded = Signal()
    failed = Signal(str)
    cancelled = Signal()
    diagnostic_message = Signal(str)

    def __init__(self, manager, action, name, parent=None, kind="library", *, channel=None):
        super().__init__(parent)
        self.manager, self.action, self.name = manager, action, name
        self.kind = kind
        self.channel = channel
        self._diagnostic_lines = deque(maxlen=5000)
        self._diagnostic_lock = threading.Lock()
        self._last_diagnostic_emit = 0.0

    def _buffer_diagnostic(self, message):
        with self._diagnostic_lock:
            self._diagnostic_lines.extend(message.splitlines())
            ready = time.monotonic() - self._last_diagnostic_emit >= 0.1
        if ready:
            self._flush_diagnostics()

    def _flush_diagnostics(self):
        with self._diagnostic_lock:
            if self._diagnostic_lines:
                self.diagnostic_message.emit("\n".join(self._diagnostic_lines))
                self._diagnostic_lines.clear()
                self._last_diagnostic_emit = time.monotonic()

    def run(self):
        started = time.monotonic()
        with diagnostic_scope(self._buffer_diagnostic):
            try:
                diagnostic(f"Starting {self.action}: {self.kind} {self.name}\n"
                           f"Python: {sys.version}\nExecutable: {sys.executable}\nPlatform: {platform.platform()}")
                with cancellation_scope(self.isInterruptionRequested):
                    self._run_action()
            except DependencyCancelled:
                diagnostic(f"Operation cancelled after {time.monotonic() - started:.2f}s")
                self.cancelled.emit()
            except Exception as error:
                diagnostic(traceback.format_exc())
                diagnostic(f"Operation failed after {time.monotonic() - started:.2f}s")
                self.failed.emit(str(error))
            else:
                diagnostic(f"Operation succeeded after {time.monotonic() - started:.2f}s")
                self.succeeded.emit()
            finally:
                self._flush_diagnostics()

    def _run_action(self):
        if self.action == "check":
            result = self.manager.check(self.name)
            self.message.emit(result if self.kind == "tool" else f"{self.name}: import check passed.")
        elif self.channel is not None:
            self.manager.install(self.name, self.message.emit, channel=self.channel)
        else:
            getattr(self.manager, self.action)(self.name, self.message.emit)


class DependencyManagerWidget(QWidget):
    def __init__(self, plugin_manager, parent=None, *, defer_refresh=False):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.manager = plugin_manager.dependency_manager if plugin_manager else None
        self.tools = ExternalToolManager()
        self._job = None
        self._pending = []
        self._batch_total = 0
        self._batch_failures = []
        self._cancel_requested = False
        self._checks = {}
        self._buttons = {}
        self._installed_yt_dlp_channel = None
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Choose the features you want, then click Install / Repair. "
            "Supporting packages are installed automatically. "
            "Package names appear below each feature for troubleshooting. Some changes need a restart."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Feature / package", "Used by", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(self._update_buttons)
        self.tree.itemChanged.connect(self._update_buttons)
        layout.addWidget(self.tree, 1)
        selection = QHBoxLayout()
        self.select_all = QPushButton("Select all")
        self.clear_selection = QPushButton("Clear selection")
        self.select_all.clicked.connect(lambda: self._set_checked(True))
        self.clear_selection.clicked.connect(lambda: self._set_checked(False))
        selection.addWidget(self.select_all)
        selection.addWidget(self.clear_selection)
        selection.addWidget(QLabel("Check items to act on several at once."))
        selection.addStretch()
        layout.addLayout(selection)
        actions = QHBoxLayout()
        self.yt_dlp_channel = QComboBox()
        for channel in YTDLP_CHANNELS:
            self.yt_dlp_channel.addItem(channel.title(), channel)
        self.yt_dlp_channel.setToolTip("Nightly includes newer site fixes and may introduce regressions.")
        self.yt_dlp_channel_label = QLabel("yt-dlp build")
        self.yt_dlp_channel_label.setBuddy(self.yt_dlp_channel)
        for widget in (self.yt_dlp_channel_label, self.yt_dlp_channel):
            widget.hide()
            actions.addWidget(widget)
        for label, action in (("Install / Repair", "install"), ("Check", "check"), ("Remove", "remove")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, a=action: self._start_selected(a))
            self._buttons[action] = button
            actions.addWidget(button)
        actions.addStretch()
        self.cancel_button = QPushButton("Cancel download")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_button)
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
        self.details.setUndoRedoEnabled(False)
        self.details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.details.setMaximumBlockCount(5000)
        self.details.setMaximumHeight(150)
        self.details.hide()
        layout.addWidget(self.details)
        self._pending_logs = deque(maxlen=5000)
        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.setInterval(100)
        self._log_timer.timeout.connect(self._flush_logs)
        self.details_button = QPushButton("Show verbose logs")
        self.details_button.setCheckable(True)
        self.details_button.setToolTip("Show live installer output, commands, download verification, and error traces. Keeps the latest 5,000 lines.")
        self.details_button.toggled.connect(self._toggle_logs)
        layout.addWidget(self.details_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.hide_notices = QCheckBox("Don't show dependency notices again")
        self.hide_notices.setChecked(load_all_settings().get("dependency_notice_disabled") is True)
        self.hide_notices.toggled.connect(_save_notice_preference)
        layout.addWidget(self.hide_notices)
        if not defer_refresh:
            self.refresh()

    @property
    def busy(self):
        return self._job is not None

    def select_yt_dlp_build(self, channel):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if (item.data(0, Qt.ItemDataRole.UserRole), item.data(0, Qt.ItemDataRole.UserRole + 1)) == ("yt-dlp", "library"):
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                self.yt_dlp_channel.setCurrentIndex(self.yt_dlp_channel.findData(channel))
                return

    def refresh(self):
        # Rebuilding checked rows must not rescan every package on each item change.
        signals = QSignalBlocker(self.tree)
        checked = {(kind, name) for name, kind in self._checked_items()}
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
                if name == "yt-dlp":
                    channel = status.get("channel") or "stable"
                    if channel != self._installed_yt_dlp_channel:
                        self._installed_yt_dlp_channel = channel
                        self.yt_dlp_channel.setCurrentIndex(self.yt_dlp_channel.findData(channel))
                check = self._checks.get(("library", name))
                text = status["text"]
                if check:
                    text += "; " + ("Import check passed" if check == "ok" else "Import failed — see verbose logs")
                requirements = ", ".join(sorted({item.requirement for item in entry["dependencies"]}))
                plugins = ", ".join(sorted(set(entry["plugins"])))
                feature = _PACKAGE_FEATURES.get(name, f"Support for {plugins}")
                item = QTreeWidgetItem([f"{feature}\n{requirements}", plugins, text])
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "library")
                item.setCheckState(0, Qt.CheckState.Checked if ("library", name) in checked
                                   else Qt.CheckState.Unchecked)
                support = sorted({value for dep in entry["dependencies"] for value in dep.requires})
                item.setToolTip(0, f"{feature}\nPackage: {requirements}"
                                   + ("\nAlso installs: " + ", ".join(support) if support else ""))
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
                text += "; " + ("Check passed" if check == "ok" else "Check failed — see verbose logs")
            feature = _TOOL_FEATURES.get(name, tool["used_by"])
            item = QTreeWidgetItem([f"{feature}\n{tool['label']}", tool["used_by"], text])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, "tool")
            item.setCheckState(0, Qt.CheckState.Checked if ("tool", name) in checked
                               else Qt.CheckState.Unchecked)
            item.setToolTip(0, f"{feature}\n{tool['label']}\nDownload source: https://github.com/{tool['repo']}")
            item.setToolTip(2, check if check and check != "ok" else status["text"])
            self.tree.addTopLevelItem(item)
            if name == selected and selected_kind == "tool":
                self.tree.setCurrentItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        signals.unblock()
        self._update_buttons()

    def _update_buttons(self, *_args):
        self.action_hint.clear()
        for button in self._buttons.values():
            button.setEnabled(False)
        item = self.tree.currentItem()
        if self.busy or item is None:
            if not self.busy:
                self._show_yt_dlp_channel(False)
            return
        checked = self._checked_items()
        current = (item.data(0, Qt.ItemDataRole.UserRole), item.data(0, Qt.ItemDataRole.UserRole + 1))
        self._show_yt_dlp_channel(
            ("yt-dlp", "library") in (checked or [current]) and self._can_act("install", "yt-dlp", "library")
        )
        if checked:
            for action, button in self._buttons.items():
                count = sum(self._can_act(action, name, kind) for name, kind in checked)
                button.setEnabled(count > 0)
                button.setToolTip(f"Apply to {count} of {len(checked)} checked items that support this action.")
            self.action_hint.setText(
                f"{len(checked)} items selected. Actions apply to eligible checked items; "
                "app-provided libraries and tools installed elsewhere are kept. "
                "Restart after changing Python libraries."
            )
            return
        name, kind = current
        if kind == "tool":
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

    def _show_yt_dlp_channel(self, visible):
        self.yt_dlp_channel_label.setVisible(visible)
        self.yt_dlp_channel.setVisible(visible)
        self.yt_dlp_channel.setEnabled(visible)

    def _checked_items(self):
        return [
            (item.data(0, Qt.ItemDataRole.UserRole), item.data(0, Qt.ItemDataRole.UserRole + 1))
            for i in range(self.tree.topLevelItemCount())
            if (item := self.tree.topLevelItem(i)).checkState(0) == Qt.CheckState.Checked
        ]

    def _set_checked(self, checked):
        if self.busy:
            return
        self.tree.blockSignals(True)
        try:
            for i in range(self.tree.topLevelItemCount()):
                self.tree.topLevelItem(i).setCheckState(
                    0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self.tree.blockSignals(False)
        self._update_buttons()

    def _can_act(self, action, name, kind):
        try:
            if kind == "tool":
                return (self.tools.supported(name) if action == "install" else
                        self.tools.status(name)["managed"] if action == "remove" else True)
            if self.manager is None:
                return False
            return (self.manager.status(name)["source"] != "app" if action == "install" else
                    name in self.manager.state()["requirements"] if action == "remove" else True)
        except DependencyError:
            return False

    def _start_selected(self, action):
        if self.busy:
            return
        checked = self._checked_items()
        if checked:
            eligible = [(name, kind) for name, kind in checked if self._can_act(action, name, kind)]
            if not eligible:
                return
            self._batch_total = len(eligible)
            self._batch_failures = []
            self._pending = [(action, name, kind) for name, kind in eligible[1:]]
            self._clear_logs()
            self._start(action, *eligible[0])
            return
        item = self.tree.currentItem()
        if item:
            self._start(action, item.data(0, Qt.ItemDataRole.UserRole),
                        item.data(0, Qt.ItemDataRole.UserRole + 1))

    def _start(self, action, name, kind="library"):
        if self.busy:
            return
        if not self._batch_total:
            self._clear_logs()
        self.status.setText(f"{'Checking' if action == 'check' else 'Removing' if action == 'remove' else 'Installing'} {name}…")
        self._log(self.status.text())
        self._checks.pop((kind, name), None)
        manager = self.tools if kind == "tool" else self.manager
        channel = self.yt_dlp_channel.currentData() if (action, name, kind) == ("install", "yt-dlp", "library") else None
        self._job = _DependencyJob(manager, action, name, self, kind=kind, channel=channel)
        self._job.message.connect(self._message)
        self._job.diagnostic_message.connect(self._log)
        self._job.failed.connect(lambda error: self._failed(action, name, error, kind))
        self._job.succeeded.connect(lambda: self._succeeded(action, name, kind))
        self._job.cancelled.connect(self._cancelled)
        self._job.finished.connect(self._finished)
        self.progress.show()
        self.cancel_button.setEnabled(action == "install")
        self.tree.setEnabled(False)
        self.select_all.setEnabled(False)
        self.clear_selection.setEnabled(False)
        self.yt_dlp_channel.setEnabled(False)
        for button in self._buttons.values():
            button.setEnabled(False)
        self._job.start()

    def _cancel(self):
        if not self.busy:
            return
        self._cancel_requested = True
        self._pending.clear()
        self.cancel_button.setEnabled(False)
        self._job.requestInterruption()
        self.status.setText("Cancelling download… Waiting for the current step to stop safely.")

    def _cancelled(self):
        self._cancel_requested = True
        self._pending.clear()
        self._log("Download cancelled.")

    def _message(self, message):
        self._log(message)
        if "\n" not in message and not self._cancel_requested:
            self.status.setText(message)

    def _log(self, message):
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._pending_logs.extend(f"[{timestamp}] {line}" for line in message.splitlines())
        if self.details_button.isChecked() and not self._log_timer.isActive():
            self._log_timer.start()

    def _clear_logs(self):
        self._log_timer.stop()
        self._pending_logs.clear()
        self.details.clear()

    def _toggle_logs(self, visible):
        self.details.setVisible(visible)
        self.details_button.setText("Hide verbose logs" if visible else "Show verbose logs")
        if visible:
            self._flush_logs(all_pending=True)
        else:
            self._log_timer.stop()

    def _flush_logs(self, *, all_pending=False):
        self._log_timer.stop()
        if not self.details_button.isChecked() or not self._pending_logs:
            return
        count = len(self._pending_logs) if all_pending else min(500, len(self._pending_logs))
        lines = [self._pending_logs.popleft() for _ in range(count)]
        self.details.appendPlainText("\n".join(lines))
        if self._pending_logs:
            self._log_timer.start()

    def _failed(self, action, name, error, kind="library"):
        if self._batch_total:
            self._batch_failures.append(name)
        if action == "check":
            self._checks[(kind, name)] = error
        self.status.setText(f"{name}: operation failed. See verbose logs for the cause.")
        self._log(f"{name}: {error}")
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
        self._flush_logs(all_pending=True)
        job = self._job
        self._job = None
        job.deleteLater()
        if self._pending:
            self._start(*self._pending.pop(0))
            return
        if self._cancel_requested:
            self.status.setText("Cancelled. Completed installations were kept; remaining items were skipped.")
            self._cancel_requested = False
            self._batch_total = 0
        elif self._batch_total:
            self.status.setText(
                f"Finished: {self._batch_total - len(self._batch_failures)} succeeded, "
                f"{len(self._batch_failures)} failed. "
                "Restart after changing Python libraries. Tools apply to new imports immediately."
            )
            self._batch_total = 0
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        self.tree.setEnabled(True)
        self.select_all.setEnabled(True)
        self.clear_selection.setEnabled(True)
        self.refresh()


class DependencyManagerDialog(QDialog):
    def __init__(self, plugin_manager, parent=None):
        # A QWidget parent would also cascade the player's custom stylesheet.
        # Use a native transient owner to keep window behavior without its theme.
        super().__init__()
        if parent is not None:
            self.winId()
            parent.window().winId()
            self.windowHandle().setTransientParent(parent.window().windowHandle())
            parent.destroyed.connect(self.deleteLater)
        self.setWindowTitle("Dependency Manager")
        self.resize(900, 520)
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


def _save_notice_preference(disabled):
    settings = load_all_settings()
    settings["dependency_notice_disabled"] = disabled
    save_all_settings(settings)


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
    if (settings.get("dependency_notice_disabled") is True
            or settings.get("dependency_notice_dismissed") == signature):
        return
    notice = QMessageBox(parent)
    notice.setWindowTitle("Plugin dependencies")
    notice.setText("Some enabled plugins need additional libraries.")
    notice.setInformativeText(
        "Open the Dependency Manager to install them or inspect the problem. "
        "You can also open it later in Settings."
    )
    notice.setDetailedText(details)
    hide_notices = QCheckBox("Don't show again")
    notice.setCheckBox(hide_notices)
    open_button = notice.addButton("Open Dependency Manager", QMessageBox.ButtonRole.AcceptRole)
    notice.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    try:
        notice.exec()
        open_manager = notice.clickedButton() is open_button
        disabled = hide_notices.isChecked()
    finally:
        notice.deleteLater()
    settings = load_all_settings()
    settings["dependency_notice_dismissed"] = signature
    settings["dependency_notice_disabled"] = disabled
    save_all_settings(settings)
    if open_manager:
        dialog = DependencyManagerDialog(plugin_manager, parent)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
