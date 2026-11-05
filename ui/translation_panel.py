from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QDialogButtonBox, QAbstractItemView, QMessageBox,
)

from core.fonts import library_font


class TranslationUiMixin:
    def __init__(self, api, parent=None, *, document=None, apply_translations=None,
                 document_loader=None, cloud_provider=None):
        super().__init__(parent)
        self.setFont(library_font(self.font()))
        self.api = api
        self.cloud_provider = cloud_provider
        self._editor_document = document
        self._apply_translations = apply_translations
        self._document_loader = document_loader
        self._embedded = not isinstance(self, QDialog)
        self.document = None
        self.catalog = {"languages": [], "routes": []}
        self._busy = False
        self._stopped = False
        self._translated_pair = None
        self._translation_pairs = {}
        self._saved_drafts = {}
        self.setWindowTitle(
            "Lyrics Translator (poor quality)"
            if cloud_provider is None
            else "Cloud Translation"
        )
        self.resize(900, 680)
        self.quota_dialog = QDialog(self)
        self.quota_dialog.setWindowTitle("Translation quota")
        self.quota_dialog.resize(520, 240)
        quota_layout = QVBoxLayout(self.quota_dialog)
        quota_form = QFormLayout()
        quota_layout.addLayout(quota_form)
        self.quota_scope = QComboBox()
        self.quota_scope.addItem("Missing rows", "missing")
        self.quota_scope.addItem("Selected rows", "selected")
        quota_form.addRow("Estimate for:", self.quota_scope)
        layout = QVBoxLayout(self)
        note = QLabel("Local machine translation (poor quality). Review and edit the results before saving.")
        note.setWordWrap(True)
        self.note = note
        layout.addWidget(note)
        self.track = QLabel("Load a song with timed lyrics.")
        self.track.setWordWrap(True)
        layout.addWidget(self.track)
        form = QFormLayout()
        root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.folder = QLineEdit(api.get_setting("models_folder", str(root / "argos-models")))
        self.folder.setAccessibleName("Argos models folder")
        form.addRow("Models folder:", self.folder)
        self.credentials = {}
        self._dirty_credentials = set()
        credential_warnings = []
        if cloud_provider:
            self.folder.hide()
            form.labelForField(self.folder).hide()
            fields = [] if cloud_provider in {"mymemory", "datpmt"} else [("api_key", "API key", True)]
            if cloud_provider == "azure":
                fields.append(("region", "Azure region (if required)", False))
            import os
            for key, label, secret in fields:
                saved = ""
                if secret:
                    from core.secret_storage import decrypt_secret, SecretStorageError
                    try:
                        saved = decrypt_secret(api.get_setting(f"encrypted_{cloud_provider}_{key}"),
                                               f"translation/{cloud_provider}/{key}")
                    except (SecretStorageError, OSError, ValueError):
                        credential_warnings.append("Could not restore the saved key. Enter it again to replace it.")
                field = QLineEdit(saved or os.environ.get(f"LRCPLUS_{cloud_provider.upper()}_{key.upper()}", ""))
                if secret:
                    field.setEchoMode(QLineEdit.EchoMode.Password)
                    field.textEdited.connect(lambda _text, name=key: self._dirty_credentials.add(name))
                    field.editingFinished.connect(self._save_credentials)
                field.textChanged.connect(lambda: self._update_controls())
                field.setPlaceholderText("Saved encrypted for your Windows account" if secret else "This session only")
                self.credentials[key] = field
                form.addRow(label + ":", field)
            if cloud_provider == "deepl":
                self.plan = QComboBox()
                self.plan.addItem("Automatic (from API key)", "auto")
                self.plan.addItem("Developer / Growth / Pro", "pro")
                self.plan.addItem("Legacy API Free", "free")
                form.addRow("Account:", self.plan)
                self.quota_plan = QComboBox()
                self.quota_plan.setAccessibleName("DeepL quota reference")
                for label, code in (("API Developer: 1M total", "developer"),
                                    ("Legacy API Free: 500K/month", "free"),
                                    ("API Growth monthly: 1M/month", "growth_monthly"),
                                    ("API Growth annual: 12M/year", "growth_annual"),
                                    ("Legacy API Pro: usage-based", "pro")):
                    self.quota_plan.addItem(label, code)
                quota_form.addRow("Quota reference:", self.quota_plan)
        self.credential_status = QLabel(" ".join(credential_warnings))
        self.credential_status.setTextFormat(Qt.TextFormat.PlainText)
        self.credential_status.setWordWrap(True)
        self.credential_status.setVisible(bool(credential_warnings))
        form.addRow(self.credential_status)
        languages = QHBoxLayout()
        self.source, self.target = QComboBox(), QComboBox()
        self.source.setAccessibleName("Source language")
        self.target.setAccessibleName("Target language")
        languages.addWidget(self.source)
        self.swap_button = QPushButton("⇄")
        self.swap_button.setAccessibleName("Swap source and target languages")
        self.swap_button.setToolTip("Swap source and target languages")
        self.swap_button.setAutoDefault(False)
        self.swap_button.setFixedWidth(36)
        self.swap_button.clicked.connect(self.swap_languages)
        languages.addWidget(self.swap_button)
        languages.addWidget(self.target)
        form.addRow("Translate:", languages)
        layout.addLayout(form)
        self.route = QLabel()
        layout.addWidget(self.route)
        self.usage_estimate = QLabel()
        self.usage_estimate.setAccessibleName("Estimated translation quota usage")
        self.usage_estimate.setTextFormat(Qt.TextFormat.PlainText)
        self.usage_estimate.setWordWrap(True)
        self.usage_estimate.setToolTip(
            "Rough source character count for one target language. Repeated rows are sent once per run; "
            "spaces and newlines count. Quota references depend on your account plan. "
            "Sent counts include failed or cancelled requests which may have reached the service. "
            "Other tabs, previous sessions and other apps are not included. UTF-8 request limits are separate."
        )
        quota_layout.addWidget(self.usage_estimate)
        quota_hint = QLabel(self.usage_estimate.toolTip())
        quota_hint.setWordWrap(True)
        quota_layout.addWidget(quota_hint)
        quota_close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        quota_close.rejected.connect(self.quota_dialog.close)
        quota_layout.addWidget(quota_close)
        # Keep the dialog at its intended width, but let the layout choose its
        # height so an empty estimate does not leave a large blank band.
        quota_layout.activate()
        self.quota_dialog.resize(520, self.quota_dialog.sizeHint().height())
        buttons = QHBoxLayout()
        self.load_button = QPushButton("Load current lyrics")
        self.refresh_button = QPushButton("Refresh models")
        self.install_button = QPushButton("Install model file…")
        self.translate_button = QPushButton("Translate missing rows")
        self.translate_selected_button = QPushButton("Translate selected rows")
        self.translate_selected_button.setToolTip("Select lyric rows with Ctrl/Shift. Replace only those draft translations.")
        for button in (self.load_button, self.refresh_button, self.install_button, self.translate_button,
                       self.translate_selected_button):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        models_link = QLabel('<a href="https://www.argosopentech.com/argospm/index/">Download Argos language models</a> · Install downloaded .argosmodel files here.')
        models_link.setOpenExternalLinks(True)
        models_link.setWordWrap(True)
        layout.addWidget(models_link)
        if cloud_provider:
            models_link.hide()
            self.refresh_button.hide()
            self.install_button.hide()
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 2)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setHorizontalHeaderLabels(["Original", "Translation (editable)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 410)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("LRCX preview appears after translation.")
        splitter.addWidget(self.table)
        splitter.addWidget(self.preview)
        layout.addWidget(splitter, 1)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel operation")
        self.save_button = QPushButton("Save translated copy…")
        close = QPushButton("Close")
        if apply_translations is not None:
            self.save_button.setText("Apply to lyrics editor")
            self.load_button.hide()
            note.setText("Local machine translation. Review and edit the results before applying to the lyrics editor.")
        if self._embedded:
            close.hide()
            self.load_button.show()
            self.load_button.setText("Reload editor lyrics")
        if cloud_provider:
            from plugins.community.lyrics_translator.cloud_providers import PROVIDERS
            provider_name = PROVIDERS[cloud_provider]
            self.setWindowTitle(provider_name)
            note.setText(f"{provider_name}: lyrics are sent to this service only when you choose Translate. "
                         "Review the output before applying. Entered API keys are saved encrypted for your Windows account.")
        footer.addWidget(self.cancel_button)
        if not self._embedded:
            quota_button = QPushButton("Quota")
            quota_button.setAutoDefault(False)
            quota_button.clicked.connect(self.show_quota)
            footer.addWidget(quota_button)
        footer.addStretch(1)
        footer.addWidget(self.save_button)
        footer.addWidget(close)
        layout.addLayout(footer)
        if cloud_provider:
            from plugins.community.lyrics_translator.cloud_job import CloudTranslationJob
            self.job = CloudTranslationJob(cloud_provider, self)
        else:
            from plugins.community.lyrics_translator.job import TranslationJob
            self.job = TranslationJob(self)
        self.job.succeeded.connect(self._succeeded)
        self.job.failed.connect(self.status.setText)
        self.job.progress.connect(self._progress)
        self.job.finished.connect(self._finished)
        if cloud_provider:
            self.job.usage_changed.connect(self._update_usage_estimate)
        if hasattr(self, "quota_plan"):
            self.quota_plan.currentIndexChanged.connect(self._update_usage_estimate)
            self.plan.currentIndexChanged.connect(self._account_changed)
            self.credentials["api_key"].textChanged.connect(self._account_changed)
            self._account_changed()
        self.load_button.clicked.connect(self.load_current)
        self.refresh_button.clicked.connect(lambda: self._start("catalog"))
        self.install_button.clicked.connect(self.install_model)
        self.translate_button.clicked.connect(self.translate)
        self.translate_selected_button.clicked.connect(lambda: self.translate(selected=True))
        self.quota_scope.currentIndexChanged.connect(self._update_usage_estimate)
        self.cancel_button.clicked.connect(self.cancel)
        self.save_button.clicked.connect(self.save)
        close.clicked.connect(self.close)
        self.source.currentIndexChanged.connect(self._options_changed)
        self.target.currentIndexChanged.connect(self._options_changed)
        self.table.itemChanged.connect(self._translation_edited)
        self.table.itemSelectionChanged.connect(self._update_controls)
        self.load_current()
        self._update_controls()
        QTimer.singleShot(0, lambda: self._start("catalog"))

    def pair(self):
        return self.source.currentData(), self.target.currentData()

    def swap_languages(self):
        if self._busy:
            return
        source, target = self.pair()
        source_index, target_index = self.source.findData(target), self.target.findData(source)
        if source_index < 0 or target_index < 0:
            return
        source_blocker, target_blocker = QSignalBlocker(self.source), QSignalBlocker(self.target)
        self.source.setCurrentIndex(source_index)
        self.target.setCurrentIndex(target_index)
        del source_blocker, target_blocker
        self._options_changed()

    def _save_credentials(self):
        from core.secret_storage import encrypt_secret, SecretStorageError
        for key in list(self._dirty_credentials):
            value = self.credentials[key].text().strip()
            try:
                record = encrypt_secret(value, f"translation/{self.cloud_provider}/{key}") if value else None
                self.api.set_setting(f"encrypted_{self.cloud_provider}_{key}", record)
            except (SecretStorageError, OSError, ValueError):
                self.credential_status.setText("Could not save the key encrypted. It remains available for this session.")
                self.credential_status.show()
                continue
            self._dirty_credentials.discard(key)
            self.credential_status.clear()
            self.credential_status.hide()

    def show_quota(self):
        self._update_usage_estimate()
        self.quota_dialog.setWindowTitle(f"{self.windowTitle()} — Quota")
        self.quota_dialog.show()
        self.quota_dialog.raise_()
        self.quota_dialog.activateWindow()

    def load_current(self, *, discard=False):
        if self._busy:
            return
        try:
            if self._document_loader is not None:
                document = self._document_loader()
            else:
                document = self._editor_document if self._editor_document is not None else self.api.get_current_lyrics_document()
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        if not discard and not self.confirm_discard("Reloading lyrics will discard the current translation drafts. Continue?"):
            return
        self.document = document
        self._translated_pair = None
        self._translation_pairs = {}
        self._saved_drafts = {}
        self.track.setText(Path(document["lyrics_path"] if self._apply_translations is not None else document["audio_path"]).name)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        preserved = 0
        for line in document["lines"]:
            if line["has_translation"]:
                preserved += 1
                continue
            if not line["text"].strip():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            original = QTableWidgetItem(line["text"])
            original.setData(Qt.ItemDataRole.UserRole, line["index"])
            original.setFlags(original.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, original)
            self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.blockSignals(False)
        self.preview.clear()
        self.status.setText(f"{self.table.rowCount()} row(s) to translate; {preserved} existing translation(s) preserved.")
        self._update_controls()

    def _route(self):
        source, target = self.pair()
        if self.cloud_provider:
            return {"via": []} if source and target and source != target else None
        return next((route for route in self.catalog["routes"]
                     if route["source"] == source and route["target"] == target), None)

    def _options_changed(self):
        route = self._route()
        if self.cloud_provider:
            self.route.setText("Cloud translation · Review your provider's current quota and billing settings.")
        elif route:
            names = {language["code"]: language["name"] for language in self.catalog["languages"]}
            self.route.setText("Via " + " → ".join(names[code] for code in route["via"])
                               if route["via"] else "Direct translation model")
        else:
            self.route.setText("Install models for the selected language pair.")
        self._update_preview()
        self._update_controls()

    def _update_controls(self):
        for widget in (self.folder, self.source, self.target, self.load_button,
                       self.refresh_button, self.install_button, self.table):
            widget.setEnabled(not self._busy)
        for field in self.credentials.values():
            field.setEnabled(not self._busy)
        if hasattr(self, "plan"):
            self.plan.setEnabled(not self._busy)
        source, target = self.pair()
        self.swap_button.setEnabled(not self._busy and source is not None and target is not None
                                    and self.source.findData(target) >= 0 and self.target.findData(source) >= 0)
        self.cancel_button.setEnabled(self._busy)
        configured = not self.cloud_provider or self.cloud_provider in {"mymemory", "datpmt"} or bool(self.credentials["api_key"].text().strip())
        ready = not self._busy and configured and self._route() is not None
        self.translate_button.setEnabled(ready and bool(self._rows_to_translate()))
        self.translate_selected_button.setEnabled(ready and bool(self._rows_to_translate(selected=True)))
        self.save_button.setEnabled(not self._busy and bool(self.preview.toPlainText()))
        self._update_usage_estimate()

    def _account_changed(self):
        legacy_free = self.plan.currentData() == "free" or (
            self.plan.currentData() == "auto" and self.credentials["api_key"].text().strip().endswith(":fx")
        )
        if legacy_free:
            self.quota_plan.setCurrentIndex(self.quota_plan.findData("free"))
        elif self.quota_plan.currentData() == "free":
            self.quota_plan.setCurrentIndex(self.quota_plan.findData("developer"))
        self._update_usage_estimate()

    def _update_usage_estimate(self):
        from core.translation_usage import format_estimate
        rows = self._rows_to_translate(selected=self.quota_scope.currentData() == "selected")
        texts = [self.table.item(row, 0).text() for row in rows]
        profile = self.quota_plan.currentData() if hasattr(self, "quota_plan") else "developer"
        self.usage_estimate.setText(format_estimate(
            self.cloud_provider, texts, deepl_plan=profile,
            sent=getattr(self.job, "estimated_characters_sent", 0),
        ))

    def _start(self, action, **kwargs):
        if self._busy or self._stopped:
            return
        folder = self.folder.text().strip()
        if not folder and not self.cloud_provider:
            self.status.setText("Choose a models folder first.")
            return
        if not self.cloud_provider:
            self.api.set_setting("models_folder", folder)
        self._action = action
        self._busy = True
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText({"catalog": "Reading installed models…", "install": "Installing local model file…",
                             "translate": "Loading local translation model…"}[action])
        if self.cloud_provider:
            self.status.setText("Preparing languages…" if action == "catalog" else "Translating with selected service…")
        self._update_controls()
        settings = {key: field.text().strip() for key, field in self.credentials.items()}
        if hasattr(self, "plan"):
            settings["plan"] = self.plan.currentData()
        self.job.start({"action": action, "folder": folder, "settings": settings, **kwargs})

    def install_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Install Argos model", "", "Argos models (*.argosmodel)")
        if path:
            self._start("install", model_file=path)

    def _rows_to_translate(self, *, selected=False):
        if selected:
            return sorted({index.row() for index in self.table.selectedIndexes()})
        return [row for row in range(self.table.rowCount())
                if self.table.item(row, 1) is not None and (
                    not self.table.item(row, 1).text().strip()
                    or self._translation_pairs.get(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) != self.pair())]

    def translate(self, *, selected=False):
        if self._busy:
            return
        selected_rows = self._rows_to_translate(selected=selected)
        if not selected_rows:
            return
        self.quota_scope.setCurrentIndex(1 if selected else 0)
        self._save_credentials()
        rows = [{"index": self.table.item(row, 0).data(Qt.ItemDataRole.UserRole),
                 "text": self.table.item(row, 0).text()} for row in selected_rows]
        self._request_rows = {str(row["index"]) for row in rows}
        source, target = self.pair()
        self._request_pair = source, target
        self.api.set_setting("source", source)
        self.api.set_setting("target", target)
        self._start("translate", source=source, target=target, rows=rows)

    def _succeeded(self, result):
        if self._action in {"catalog", "install"}:
            self.catalog = result
            previous_pair = self.pair()
            for combo, key, default in ((self.source, "source", "ja"), (self.target, "target", "en")):
                previous = previous_pair[0 if key == "source" else 1]
                combo.blockSignals(True)
                combo.clear()
                for language in result["languages"]:
                    combo.addItem(language["name"], language["code"])
                selected = combo.findData(previous)
                if selected < 0:
                    selected = combo.findData(self.api.get_setting(key, default))
                combo.setCurrentIndex(selected if selected >= 0 else 0)
                combo.blockSignals(False)
            self._options_changed()
            self.status.setText("Languages ready." if self.cloud_provider else
                                "Models ready." if result["routes"] else
                                "No translation models installed. Install an .argosmodel file to begin.")
            return
        translations = result.get("translations", {})
        expected = self._request_rows
        if set(translations) != expected or not all(isinstance(text, str) and text.strip() for text in translations.values()):
            self.status.setText("The worker returned missing or invalid translation rows. Nothing was saved.")
            return
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            index = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if str(index) in translations:
                self.table.item(row, 1).setText(translations[str(index)])
                self._translation_pairs[index] = self._request_pair
                self.table.item(row, 1).setToolTip("Translation language pair: " + " → ".join(self._request_pair))
        self.table.blockSignals(False)
        self._translated_pair = self._request_pair
        self._update_preview()
        self.status.setText("Review the translations. Edit cells before applying to the lyrics editor."
                            if self._apply_translations is not None else
                            "Review the translations. Edit cells before saving a separate copy.")

    def translations(self):
        return {self.table.item(row, 0).data(Qt.ItemDataRole.UserRole): self.table.item(row, 1).text().strip()
                for row in range(self.table.rowCount()) if self.table.item(row, 1).text().strip()}

    def has_unapplied_translations(self):
        drafts = self._drafts()
        return (bool(drafts) and drafts != self._saved_drafts) or (
            self._busy and getattr(self, "_action", None) == "translate")

    def _drafts(self):
        return {index: (text, self._translation_pairs.get(index)) for index, text in self.translations().items()}

    def confirm_discard(self, message):
        if self._stopped or not self.has_unapplied_translations():
            return True
        return QMessageBox.warning(
            self, "Unapplied Translations", message,
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Discard

    def _clear_drafts(self):
        blocker = QSignalBlocker(self.table)
        for row in range(self.table.rowCount()):
            self.table.item(row, 1).setText("")
        del blocker
        self._translation_pairs = {}
        self._saved_drafts = {}
        self.preview.clear()

    def resume(self):
        self._stopped = False
        self._update_controls()
        if not self.catalog["languages"]:
            QTimer.singleShot(0, lambda: self._start("catalog"))

    def _translation_edited(self, item):
        if item.column() == 1:
            index = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
            if item.text().strip():
                # Editing an existing draft must not silently change its language.
                self._translation_pairs.setdefault(index, self.pair())
            else:
                self._translation_pairs.pop(index, None)
        self._update_preview()

    def _current_translations(self):
        return {index: text for index, text in self.translations().items()
                if self._translation_pairs.get(index) == self.pair()}

    def _update_preview(self):
        translations = self._current_translations()
        if not self.document or not translations:
            self.preview.clear()
        else:
            try:
                self.preview.setPlainText(self.api.render_lyrics_translations(self.document, translations))
            except (ValueError, OSError) as error:
                self.preview.clear()
                self.status.setText(str(error))
        self._update_controls()

    def _progress(self, done, total):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status.setText(f"Translating {done}/{total}…")

    def _finished(self):
        self._busy = False
        self.progress.hide()
        self._update_controls()

    def cancel(self):
        self.job.cancel()
        self.status.setText("Operation cancelled. No lyrics were saved.")

    def save(self):
        translations = self._current_translations()
        if not self.document or not translations:
            return
        if self._apply_translations is not None:
            try:
                self._apply_translations(self.document, translations)
            except (OSError, ValueError, RuntimeError) as error:
                self.status.setText(str(error))
                return
            if self._embedded:
                remaining = {index: (text, self._translation_pairs.get(index))
                             for index, text in self.translations().items() if index not in translations}
                self.load_current(discard=True)
                self.table.blockSignals(True)
                for row in range(self.table.rowCount()):
                    index = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                    if index in remaining:
                        text, pair = remaining[index]
                        self.table.item(row, 1).setText(text)
                        self._translation_pairs[index] = pair
                self.table.blockSignals(False)
                self._update_preview()
                self.status.setText("Translations applied. Use Undo to revert, or Save in the lyrics editor.")
            else:
                self.accept()
            return
        source = Path(self.document["lyrics_path"])
        suggested = source.with_name(source.stem + f".{self.pair()[1]}.machine.lrcx")
        path, _ = QFileDialog.getSaveFileName(self, "Save translated copy", str(suggested), "LRCX lyrics (*.lrcx)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".lrcx"
        try:
            saved = self.api.save_lyrics_translations(self.document, translations, path)
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        for index, text in translations.items():
            self._saved_drafts[index] = (text, self._translation_pairs.get(index))
        self.status.setText(f"Saved and attached to {Path(self.document['audio_path']).name}: {saved}")

    def shutdown(self):
        self._save_credentials()
        self.quota_dialog.close()
        self._stopped = True
        self.job.shutdown()

    def closeEvent(self, event):
        if not self.confirm_discard("Closing will discard unapplied translations and cancel any running translation. Continue?"):
            event.ignore()
            return
        self._clear_drafts()
        self.shutdown()
        super().closeEvent(event)

    def reject(self):
        if not self.confirm_discard("Closing will discard unapplied translations and cancel any running translation. Continue?"):
            return
        self._clear_drafts()
        self.shutdown()
        super().reject()


class TranslationDialog(TranslationUiMixin, QDialog):
    """Standalone translation window."""


class TranslationPanel(TranslationUiMixin, QWidget):
    """Translation workspace embedded in the lyrics editor."""
