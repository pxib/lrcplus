"""Player update checks, download progress and installer handoff."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import subprocess
import sys
import uuid
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from core.settings import get_update_settings, save_update_settings, get_app_data_dir
from core.updater import build_handoff_request, find_updater_executable, log_update_event
from ui.update_dialogs import (
    UpdateAvailableDialog,
    UpdateCheckWorker,
    UpdateDownloadDialog,
    UpdateDownloadWorker,
)
from core.file_io import atomic_write_text


class PlayerUpdatesMixin:
    def _automatic_update_check(self):
        self.update_settings = get_update_settings()
        if not self.update_settings.get("automatic_check", True):
            return
        previous = self.update_settings.get("last_attempt", "")
        if previous:
            try:
                last_attempt = datetime.fromisoformat(previous.replace("Z", "+00:00"))
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last_attempt < timedelta(hours=24):
                    return
            except ValueError:
                pass
        self.check_for_updates(manual=False)

    def check_for_updates(self, *, manual=True):
        worker = self._update_check_worker
        if worker is not None and worker.isRunning():
            if manual:
                QMessageBox.information(self, "Check for Updates", "An update check is already in progress.")
            return
        self.update_settings = get_update_settings()
        self.update_settings["last_attempt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_update_settings(self.update_settings)
        log_update_event(f"Checking for updates on the {self.update_settings['channel']} channel")
        worker = UpdateCheckWorker(self.update_settings["channel"], self)
        self._update_check_worker = worker
        worker.checked.connect(
            lambda release, job=worker, is_manual=manual:
                self._update_check_succeeded(job, release, is_manual)
        )
        worker.failed.connect(
            lambda message, job=worker, is_manual=manual:
                self._update_check_failed(job, message, is_manual)
        )
        worker.finished.connect(lambda job=worker: self._update_check_finished(job))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _update_check_finished(self, worker):
        if self._update_check_worker is worker:
            self._update_check_worker = None
        if self._close_after_update_cancel:
            self._close_after_update_cancel = False
            QTimer.singleShot(0, self.close)

    def _update_check_succeeded(self, _worker, release, manual):
        self.update_settings = get_update_settings()
        self.update_settings["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_update_settings(self.update_settings)
        if release is None:
            log_update_event("Update check completed: no newer compatible release")
            if manual:
                QMessageBox.information(self, "Check for Updates", "You are using the latest version of LRCPlus.")
            return
        log_update_event(f"Update available: {release.tag}; asset {release.asset.name}")
        if (not manual and self.update_settings.get("skipped_version") == release.tag):
            return
        current = self._update_available_dialog
        if current is not None and current.isVisible() and current.release.tag == release.tag:
            return
        dialog = UpdateAvailableDialog(release, self)
        self._update_available_dialog = dialog
        dialog.skip_requested.connect(
            lambda tag=release.tag: self._skip_update_version(tag)
        )
        dialog.update_requested.connect(lambda item=release: self._begin_update(item))

        def release_dialog():
            if self._update_available_dialog is dialog:
                self._update_available_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(release_dialog)
        dialog.show()

    def _update_check_failed(self, _worker, message, manual):
        log_update_event(f"Update check failed: {message}")
        if manual:
            QMessageBox.warning(self, "Unable to Check for Updates", message)

    def _skip_update_version(self, tag):
        self.update_settings = get_update_settings()
        self.update_settings["skipped_version"] = tag
        save_update_settings(self.update_settings)
        log_update_event(f"Skipped update version {tag}")

    def _begin_update(self, release):
        answer = QMessageBox.question(
            self,
            "Restart to Install Update",
            "Installing this update will close LRCPlus and stop playback. Save any open edits before continuing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        updates_root = get_app_data_dir() / "updates" / "transactions"
        try:
            updates_root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(updates_root).free < release.asset.size * 2 + 64 * 1024**2:
                raise OSError("There is not enough free disk space to download and prepare this update.")
            transaction = updates_root / uuid.uuid4().hex
            transaction.mkdir()
        except OSError as error:
            QMessageBox.warning(self, "Unable to Prepare Update", str(error))
            return

        worker = UpdateDownloadWorker(release, transaction, self)
        log_update_event(f"Preparing update {release.tag} from {release.asset.name}")
        self._update_download_worker = worker
        self._update_transaction = transaction
        progress = UpdateDownloadDialog(release.tag, self)
        self._update_progress_dialog = progress
        progress.cancel_requested.connect(self._cancel_update_download)
        worker.progress.connect(progress.set_progress)
        worker.stage_changed.connect(progress.set_stage)
        worker.prepared.connect(
            lambda staged, job=worker, item=release, folder=transaction:
                self._launch_update_helper(job, item, folder, Path(staged))
        )
        worker.failed.connect(
            lambda message, job=worker, folder=transaction:
                self._update_download_failed(job, folder, message)
        )
        worker.cancelled.connect(
            lambda job=worker, folder=transaction:
                self._update_download_cancelled(job, folder)
        )
        worker.finished.connect(lambda job=worker: self._update_download_finished(job))
        worker.finished.connect(worker.deleteLater)
        # Keep a hidden progress window usable while its worker is active,
        # then release it even if the user dismissed it during the download.
        def release_progress():
            if self._update_progress_dialog is progress:
                self._update_progress_dialog = None
            progress.deleteLater()

        worker.finished.connect(release_progress)
        progress.show()
        worker.start()

    def _cancel_update_download(self):
        worker = self._update_download_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            if self._update_progress_dialog is not None:
                self._update_progress_dialog.cancel_button.setEnabled(False)
                self._update_progress_dialog.set_stage("Cancelling download...")

    def _update_download_finished(self, worker):
        if self._update_download_worker is worker:
            self._update_download_worker = None
        if self._close_after_update_cancel:
            self._close_after_update_cancel = False
            QTimer.singleShot(0, self.close)

    def _update_download_failed(self, _worker, transaction, message):
        log_update_event(f"Update download/preparation failed: {message}")
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        shutil.rmtree(transaction, ignore_errors=True)
        if not self._close_after_update_cancel:
            QMessageBox.warning(self, "Update Download Failed", f"{message}\n\nYour current installation was not changed.")

    def _update_download_cancelled(self, _worker, transaction):
        log_update_event("Update download cancelled by the user")
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        shutil.rmtree(transaction, ignore_errors=True)

    def _launch_update_helper(self, worker, release, transaction, staged_root):
        if self._close_after_update_cancel:
            return
        try:
            install_root = (
                Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parents[1]
            )
            helper_source = find_updater_executable(staged_root, install_root)
            helper_path = transaction / "LRCPlusUpdater.exe"
            shutil.copy2(helper_source, helper_path)
            request = build_handoff_request(
                install_root, release, worker.architecture, os.getpid()
            )
            request_path = transaction / "request.json"
            atomic_write_text(request_path, json.dumps(request, indent=2))
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(
                [str(helper_path), "--request", str(request_path)],
                cwd=transaction,
                close_fds=True,
                creationflags=flags,
            )
            log_update_event(f"Started standalone updater for {release.tag}")
        except Exception as error:
            log_update_event(f"Could not start standalone updater: {error!r}")
            shutil.rmtree(transaction, ignore_errors=True)
            if self._update_progress_dialog is not None:
                self._update_progress_dialog.close()
                self._update_progress_dialog = None
            QMessageBox.warning(self, "Unable to Start Update", f"The update is verified, but the installer could not start.\n\n{error}")
            return
        self._update_handoff_started = True
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.set_finished()
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        self.close()
