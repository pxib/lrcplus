"""Provider lyrics search dialog and metadata-based search queries."""

from pathlib import Path
import math
import os
from time import monotonic
from threading import Event
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon, QMovie, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QTabBar,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSplitter,
)
from core.background_job import BackgroundJob
from core.lyrics_preview_queue import LyricsPreviewQueue
from core.lyrics_translations import converted_translations, translation_preview
from core.lrclib import LRCLibError, search as search_lrclib
from core.musixmatch import (
    MusixmatchError,
    search as search_musixmatch,
    fetch_lyrics as fetch_musixmatch_lyrics,
)
from core.chinese_lyrics import (
    PROVIDERS as CHINESE_LYRICS_PROVIDERS,
    LyricsProviderError,
    search as search_chinese_lyrics,
    fetch_lyrics as fetch_chinese_lyrics,
)
from core.synclrc import SyncLRCError, search as search_synclrc, fetch_lyrics as fetch_synclrc_lyrics
from core.music_library import read_track
from ui.lyrics_preview import LyricsPreview
from core.file_io import atomic_write_text
from core.fonts import content_font, library_font


def _lyrics_search_query(audio_path):
    audio_path = Path(audio_path)
    try:
        track = read_track(audio_path)
    except (OSError, ValueError):
        return audio_path.stem

    title = str(getattr(track, "title", "") or "").strip()
    artist = str(getattr(track, "artist", "") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    if title and title.casefold() != audio_path.stem.casefold():
        return title
    return audio_path.stem


class LyricsSearchDialogMixin:
    def search_lrclib_lyrics(self):
        """Search online providers, inspect results, and load selected lyrics."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Search Lyrics")
        dialog.resize(900, 650)

        layout = QVBoxLayout(dialog)
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Lyrics source"))
        provider = QComboBox(dialog)
        provider.addItems(["LRCLIB", "SyncLRC", *CHINESE_LYRICS_PROVIDERS, "Musixmatch"])
        provider.setAccessibleName("Lyrics source")
        provider_row.addWidget(provider)
        layout.addLayout(provider_row)

        search_row = QHBoxLayout()
        query_input = QLineEdit(dialog)
        query_input.setPlaceholderText("Song title, artist, or keywords")
        query_input.setFont(content_font(query_input.font(), ""))
        query_input.textChanged.connect(
            lambda text: query_input.setFont(content_font(query_input.font(), text))
        )

        # Prefer embedded tags, falling back to the filename when unavailable.
        if self.current_audio_path:
            query_input.setText(
                _lyrics_search_query(self.current_audio_path)
            )

        search_button = QPushButton("Search", dialog)
        search_row.addWidget(query_input)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)

        key_panel = QWidget(dialog)
        key_layout = QVBoxLayout(key_panel)
        key_layout.setContentsMargins(0, 0, 0, 0)
        auth_method = QComboBox(key_panel)
        auth_method.setAccessibleName("Musixmatch authentication method")
        auth_method.addItem("Authentication token (OpenLyrics)", "token")
        auth_method.addItem("Developer API key", "api_key")
        key_layout.addWidget(auth_method)
        credential_row = QHBoxLayout()
        api_key = QLineEdit(key_panel)
        api_key.setEchoMode(QLineEdit.EchoMode.Password)
        credential_row.addWidget(api_key)
        show_key_button = QPushButton("Show", key_panel)
        show_key_button.setAutoDefault(False)
        show_key_button.setCheckable(True)
        show_key_button.toggled.connect(lambda show: api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password
        ))
        credential_row.addWidget(show_key_button)
        key_layout.addLayout(credential_row)
        key_note = QLabel(key_panel)
        key_note.setOpenExternalLinks(True)
        key_note.setWordWrap(True)
        key_layout.addWidget(key_note)
        layout.addWidget(key_panel)
        key_panel.hide()

        splitter = QSplitter(Qt.Orientation.Vertical, dialog)

        results = QTableWidget(0, 5, splitter)
        results.setFont(library_font(results.font()))
        results.setHorizontalHeaderLabels(
            ["Title", "Album", "Artist", "Timing", "Duration"]
        )
        results.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        results.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        results.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        results.setAlternatingRowColors(True)
        results.verticalHeader().setVisible(False)

        header = results.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        preview_panel = QWidget(splitter)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(QLabel("Preview / Inspect Lyrics"))
        include_translations = QCheckBox("Include translations", preview_panel)
        include_translations.setAccessibleName("Include translations")
        include_translations.setEnabled(False)
        include_translations.setToolTip(
            "Show and save provider translations below the original lyrics as LRCX. Available in the Original tab."
        )
        preview_tabs = QTabBar(preview_panel)
        preview_tabs.setAccessibleName("Lyric preview version")
        preview_tabs.setExpanding(False)
        preview_tabs.setDrawBase(False)
        preview_tabs.addTab("Original")
        preview_tabs.addTab("Translated")
        preview_tabs.setTabEnabled(1, False)
        translation_script = QComboBox(preview_panel)
        translation_script.setAccessibleName("Chinese translation script")
        translation_script.addItem("As provided", "provided")
        translation_script.addItem("Simplified", "simplified")
        translation_script.addItem("Traditional", "traditional")
        translation_script.setEnabled(False)
        translation_script.setToolTip(
            "Convert Chinese translations in the preview and saved lyrics. Original lyrics stay as provided."
        )
        preview_controls_widget = QWidget(preview_panel)
        preview_controls = QHBoxLayout(preview_controls_widget)
        preview_controls.setContentsMargins(0, 0, 0, 0)
        preview_controls.addWidget(include_translations)
        preview_controls.addWidget(preview_tabs)
        preview_controls.addStretch(1)
        preview_controls.addWidget(QLabel("Chinese:", preview_panel))
        preview_controls.addWidget(translation_script)
        preview_layout.addLayout(preview_controls)
        preview_layout.addWidget(preview_controls_widget)
        preview_controls_widget.setVisible(provider.currentText() in CHINESE_LYRICS_PROVIDERS)

        preview = LyricsPreview(preview_panel)
        preview.setPlaceholderText(
            "Select a result to preview its lyrics here."
        )
        preview_layout.addWidget(preview)
        attribution = QLabel(preview_panel)
        attribution.setTextFormat(Qt.TextFormat.PlainText)
        attribution.setWordWrap(True)
        preview_layout.addWidget(attribution)

        splitter.addWidget(results)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        status_widget = QWidget(dialog)
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(6)
        search_loading = QLabel(status_widget)
        search_loading.setObjectName("lyricsSearchLoading")
        search_loading.setAccessibleName("Searching lyrics")
        search_loading.setFixedSize(24, 24)
        search_movie = QMovie(
            str(Path(__file__).resolve().parents[1] / "assets" / "media" / "loading.gif"),
            parent=search_loading,
        )
        search_movie.setScaledSize(QSize(24, 24))
        search_loading.setMovie(search_movie)
        search_result = QLabel(status_widget)
        search_result.setObjectName("lyricsSearchResult")
        search_result.setFixedSize(24, 24)
        status = QLabel("", status_widget)
        status.setWordWrap(True)
        status_layout.addWidget(search_loading)
        status_layout.addWidget(search_result)
        status_layout.addWidget(status, 1)
        layout.addWidget(status_widget)
        search_loading.hide()
        search_result.hide()

        buttons = QHBoxLayout()
        use_button = QPushButton("Use Selected Lyrics", dialog)
        use_button.setEnabled(False)
        cancel_button = QPushButton("Cancel", dialog)
        buttons.addStretch(1)
        buttons.addWidget(use_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        records = []
        provider_states = {}
        preview_retry_at = {}
        active_provider = provider.currentText()
        search_job = BackgroundJob(dialog, "lyrics-search")
        preview_queue = LyricsPreviewQueue(dialog)
        search_cancel = Event()
        search_in_progress = False
        cooldown_timer = QTimer(dialog)
        cooldown_timer.setInterval(100)
        provider_errors = (LRCLibError, MusixmatchError, LyricsProviderError, SyncLRCError)

        def set_search_indicator(state):
            search_movie.stop()
            search_loading.hide()
            search_result.hide()
            if state == "loading":
                search_loading.show()
                search_movie.start()
            elif state in {"success", "empty", "error"}:
                icon, accessible_name = {
                    "success": ("success.png", "Lyrics found"),
                    "empty": ("fail.png", "No lyrics found"),
                    "error": ("error.png", "Lyrics search error"),
                }[state]
                search_result.setPixmap(QPixmap(str(
                    Path(__file__).resolve().parents[1] / "assets" / "media" / icon
                )).scaled(
                    QSize(24, 24), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                search_result.setAccessibleName(accessible_name)
                search_result.show()

        def lookup_failure(message):
            return f"{message}\nTry removing the artist name and searching with just the song title."

        def cooldown_attribute():
            return {
                "LRCLIB": "_lrclib_search_ready_at",
                "SyncLRC": "_synclrc_search_ready_at",
                "Musixmatch": "_musixmatch_search_ready_at",
                "NetEase Cloud Music": "_netease_search_ready_at",
                "QQ Music": "_qqmusic_search_ready_at",
                "KuGou": "_kugou_search_ready_at",
                "Kuwo": "_kuwo_search_ready_at",
            }[provider.currentText()]

        def timing_label(item):
            if item.get("_preview_error"):
                return "Unavailable" if item.get("_preview_unavailable") else "Error"
            if item.get("lyricsFormat") == "lrcx":
                return "Words"
            if item.get("syncedLyrics") or item.get("hasSyncedLyrics"):
                return "Lines"
            if item.get("plainLyrics"):
                return "Plain"
            if item.get("source") in ("Musixmatch", "SyncLRC", *CHINESE_LYRICS_PROVIDERS):
                return "Checking"
            return "Unknown" if item.get("hasSyncedLyrics", False) is None else "No"

        def update_search_button():
            remaining = math.ceil(max(
                0, getattr(self, cooldown_attribute(), 0) - monotonic()
            ))
            search_button.setEnabled(not search_in_progress and remaining == 0)
            search_button.setText(
                f"Search ({remaining}s)" if remaining else "Search"
            )
            if remaining:
                cooldown_timer.start()
            else:
                cooldown_timer.stop()

        cooldown_timer.timeout.connect(update_search_button)
        update_search_button()

        def selected_record():
            row_index = results.currentRow()
            if 0 <= row_index < len(records):
                return records[row_index]
            return None

        def update_preview():
            use_button.setEnabled(False)
            attribution.clear()
            item = selected_record()
            has_translation = bool(item and item.get("translatedLyrics"))
            translation_script.setEnabled(has_translation)
            preview_tabs.setTabEnabled(1, has_translation)
            include_translations.setEnabled(has_translation and preview_tabs.currentIndex() == 0)
            if item is None:
                preview.clear()
                return

            if item.get("_preview_error"):
                preview.setPlainText(item["_preview_error"])
                return
            if item.get("source") in ("Musixmatch", "SyncLRC", *CHINESE_LYRICS_PROVIDERS) and not item.get("syncedLyrics") and not item.get("plainLyrics"):
                preview.setPlainText(f"Loading lyrics from {item['source']}...")
                preview_queue.prioritize(item)
                return

            synced = item.get("syncedLyrics") or ""
            plain = item.get("plainLyrics") or ""

            if preview_tabs.currentIndex() == 1 and has_translation:
                variant = converted_translations(item, translation_script.currentData())
                preview.setPlainText(translation_preview(variant))
            elif synced:
                if has_translation and include_translations.isChecked():
                    variant = converted_translations(item, translation_script.currentData())
                    preview.setPlainText(variant["translatedLyrics"])
                else:
                    preview.setPlainText(synced)
            elif plain:
                preview.setPlainText(plain)
            else:
                preview.setPlainText(
                    lookup_failure("This result does not contain lyrics.")
                )
            use_button.setEnabled(bool(synced or plain))
            if item.get("source"):
                notes = [f"Lyrics provided by {item['source']}", item.get("copyright"), item.get("sourceUrl")]
                if item["source"] == "Kuwo":
                    notes.append("Kuwo may include original and translated lines together.")
                if item.get("translationCount"):
                    notes.append(f"Provider translations available for {item['translationCount']} line(s).")
                elif item["source"] in ("NetEase Cloud Music", "QQ Music", "KuGou"):
                    notes.append("No aligned provider translation is available for this result.")
                credit = "\n".join(filter(None, notes))
                attribution.setFont(content_font(attribution.font(), credit))
                attribution.setText(credit)

        include_translations.toggled.connect(update_preview)
        preview_tabs.currentChanged.connect(update_preview)
        translation_script.currentIndexChanged.connect(update_preview)

        def fetch_preview(item, key, mode, cancel):
            try:
                if item.get("source") == "SyncLRC":
                    return fetch_synclrc_lyrics(item, cancel)
                if item.get("source") in CHINESE_LYRICS_PROVIDERS:
                    return fetch_chinese_lyrics(item, cancel)
                return fetch_musixmatch_lyrics(item, key, cancel, auth_mode=mode)
            except provider_errors as error:
                return error

        def check_result_timing():
            pending = [item for item in records
                       if item.get("source") in ("Musixmatch", "SyncLRC", *CHINESE_LYRICS_PROVIDERS)
                       and not item.get("syncedLyrics") and not item.get("plainLyrics")
                       and not item.get("_preview_error")]
            preview_queue.start(fetch_preview, pending, api_key.text().strip(), auth_method.currentData())
            remaining = preview_retry_at.get(provider.currentText(), 0) - monotonic()
            if remaining > 0:
                preview_queue.pause(remaining)

        def show_preview(item, payload):
            row = next((index for index, candidate in enumerate(records) if candidate is item), None)
            if row is None:
                return
            if isinstance(payload, Exception):
                item["_preview_error"] = lookup_failure(str(payload))
                item["_preview_unavailable"] = (getattr(payload, "status_code", None) in {301, 404}
                                                or "no lyrics available" in str(payload).lower())
                retry_after = getattr(payload, "retry_after", 0)
                if retry_after:
                    preview_queue.pause(retry_after)
                    preview_retry_at[provider.currentText()] = monotonic() + retry_after
                    setattr(self, cooldown_attribute(), max(
                        getattr(self, cooldown_attribute(), 0), monotonic() + retry_after,
                    ))
                    update_search_button()
            else:
                item.update(payload)
            cell = results.item(row, 3)
            cell.setText(timing_label(item))
            cell.setToolTip(item.get("_preview_error", ""))
            if selected_record() is item:
                update_preview()

        def reset_provider():
            nonlocal search_in_progress
            search_cancel.set()
            search_job.cancel()
            preview_queue.cancel()
            search_in_progress = False
            set_search_indicator(None)
            results.setRowCount(0)
            records.clear()
            update_preview()
            status.clear()
            key_panel.setVisible(provider.currentText() == "Musixmatch")
            preview_controls_widget.setVisible(
                provider.currentText() in CHINESE_LYRICS_PROVIDERS
            )
            update_search_button()

        def switch_provider():
            nonlocal active_provider, records
            provider_states[active_provider] = {
                "records": list(records), "query": query_input.text(),
                "row": results.currentRow(),
                "scroll": results.verticalScrollBar().value(),
                "preview_scroll": preview.verticalScrollBar().value(),
                "status": ("Search cancelled. Search again to refresh."
                           if search_in_progress else status.text()),
            }
            reset_provider()
            active_provider = provider.currentText()
            saved = provider_states.get(active_provider)
            if saved is not None:
                records = list(saved["records"])
                query_input.setText(saved["query"])
                check_result_timing()
                populate_results(saved["row"])
                results.verticalScrollBar().setValue(saved["scroll"])
                preview.verticalScrollBar().setValue(saved["preview_scroll"])
                status.setText(saved["status"])

        def credential_attribute():
            return "_musixmatch_token" if auth_method.currentData() == "token" else "_musixmatch_api_key"

        def change_auth_method():
            reset_provider()
            token_mode = auth_method.currentData() == "token"
            label = "Musixmatch authentication token" if token_mode else "Musixmatch API key"
            api_key.setAccessibleName(label)
            api_key.setPlaceholderText(label)
            env_key = "MUSIXMATCH_TOKEN" if token_mode else "MUSIXMATCH_API_KEY"
            api_key.setText(getattr(self, credential_attribute(), os.environ.get(env_key, "")))
            show_key_button.setChecked(False)
            key_note.setText(
                "Paste an existing authentication token. "
                "The token is remembered until the app exits."
                if token_mode else
                'Use your <a href="https://developer.musixmatch.com/">Musixmatch developer API key</a>. '
                "Kept in memory until the app exits. Synced lyrics require API access."
            )

        def credential_edited():
            setattr(self, credential_attribute(), api_key.text().strip())
            reset_provider()

        def run_search():
            nonlocal search_in_progress, search_cancel
            update_search_button()
            if not search_button.isEnabled():
                return
            query = query_input.text().strip()
            if not query:
                status.setText(
                    "Enter a title, artist, or other keywords."
                )
                return

            source = provider.currentText()
            key = api_key.text().strip()
            if source == "Musixmatch" and not key:
                status.setText(
                    "Paste an authentication token before searching."
                    if auth_method.currentData() == "token" else
                    "Enter a Musixmatch API key before searching."
                )
                api_key.setFocus()
                return
            if source == "Musixmatch":
                setattr(self, credential_attribute(), key)
            search_cancel.set()
            preview_queue.cancel()
            search_cancel = Event()
            set_search_indicator("loading")
            status.setText(f"Searching {source}...")
            results.setRowCount(0)
            preview.clear()
            records.clear()
            update_preview()
            search_in_progress = True
            setattr(self, cooldown_attribute(), monotonic() + 5)
            update_search_button()
            search_job.start(fetch_results, query, source, key, search_cancel, auth_method.currentData())

        def fetch_results(query, source, key, cancel, mode):
            try:
                if source == "SyncLRC":
                    return search_synclrc(query, cancel)
                if source == "Musixmatch":
                    return search_musixmatch(query, key, cancel, auth_mode=mode)
                if source in CHINESE_LYRICS_PROVIDERS:
                    return search_chinese_lyrics(source, query, cancel)
                return search_lrclib(query, cancel)
            except provider_errors as error:
                return error

        def show_results(payload):
            nonlocal search_in_progress
            search_in_progress = False
            if isinstance(payload, provider_errors):
                set_search_indicator("error")
                setattr(self, cooldown_attribute(), max(
                    getattr(self, cooldown_attribute(), 0), monotonic() + payload.retry_after,
                ))
                update_search_button()
                status.setText(lookup_failure(str(payload)))
                return
            update_search_button()
            if not isinstance(payload, list):
                payload = []

            records.extend(payload)

            check_result_timing()
            populate_results()
            if records:
                set_search_indicator("success")
                status.setText(
                    f"{len(records)} result(s). "
                    "Select one to inspect the lyrics."
                )
            else:
                set_search_indicator("empty")
                status.setText(lookup_failure("No lyrics found."))

        def populate_results(selected_row=0):
            # Rebuilding a cached table must not fetch intermediate rows.
            results.blockSignals(True)
            results.setRowCount(0)
            for row_index, item in enumerate(records):
                results.insertRow(row_index)

                title = (
                    item.get("trackName")
                    or item.get("name")
                    or "Unknown"
                )
                album = item.get("albumName") or ""
                artist = item.get("artistName") or "Unknown artist"
                try:
                    seconds = max(0, int(float(item.get("duration") or 0)))
                except (TypeError, ValueError, OverflowError):
                    seconds = 0
                duration = f"{seconds // 60}:{seconds % 60:02d}" if seconds else ""

                for column, value in enumerate(
                    [title, album, artist, timing_label(item), duration]
                ):
                    table_item = QTableWidgetItem(str(value))
                    if column < 3:
                        table_item.setFont(content_font(results.font(), str(value)))
                    if column == 3:
                        table_item.setToolTip(item.get("_preview_error", ""))
                        if value == "Unknown":
                            table_item.setIcon(QIcon(
                                str(Path(__file__).resolve().parents[1]
                                    / "assets" / "media" / "uncertain.png")
                            ))
                            table_item.setToolTip(
                                "Timing information is unknown."
                            )
                    results.setItem(
                        row_index, column, table_item
                    )

            results.blockSignals(False)
            if 0 <= selected_row < len(records):
                results.selectRow(selected_row)
            else:
                update_preview()

        def search_failed(error):
            nonlocal search_in_progress
            search_in_progress = False
            set_search_indicator("error")
            update_search_button()
            status.setText(lookup_failure(f"Search failed: {error}"))

        def use_selected():
            item = selected_record()
            if item is None:
                status.setText("Select a result first.")
                return

            if include_translations.isChecked() and item.get("translatedLyrics"):
                item = converted_translations(item, translation_script.currentData())
                item = {**item, "syncedLyrics": item["translatedLyrics"], "lyricsFormat": "lrcx"}

            synced = item.get("syncedLyrics")
            plain = item.get("plainLyrics")

            if not synced and not plain:
                status.setText(
                    lookup_failure("That result contains no lyrics.")
                )
                return

            if not self.current_audio_path:
                status.setText(
                    "Open an audio file before loading lyrics."
                )
                return

            if synced:
                destination = (
                    self.current_audio_path.with_suffix(".lrcx" if item.get("lyricsFormat") == "lrcx" else ".lrc")
                )
                content = synced
            else:
                destination = (
                    self.current_audio_path.with_suffix(".txt")
                )
                content = plain

            try:
                current_lyrics = getattr(self, "current_lyrics_path", None)
                has_lyrics = (
                    bool(getattr(self, "lyric_lines", []))
                    or (current_lyrics and Path(current_lyrics).is_file())
                    or destination.exists()
                    or any(self.current_audio_path.with_suffix(suffix).is_file()
                           for suffix in (".lrc", ".lrcx", ".txt"))
                )
                if has_lyrics:
                    warning = QMessageBox(
                        QMessageBox.Icon.Warning,
                        "Overwrite Lyrics",
                        f'"{self.current_audio_path.name}" already has lyrics.\n\n'
                        f"Replace them with the selected lyrics from {provider.currentText()}?\n"
                        f"An existing file at {destination} will be overwritten.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                        dialog,
                    )
                    save_as = warning.addButton("Save as...", QMessageBox.ButtonRole.ActionRole)
                    warning.setDefaultButton(QMessageBox.StandardButton.Cancel)
                    warning.setEscapeButton(QMessageBox.StandardButton.Cancel)
                    warning.exec()
                    if warning.clickedButton() is save_as:
                        suffix = destination.suffix
                        filename, _ = QFileDialog.getSaveFileName(
                            dialog, "Save Lyrics As", str(destination),
                            f"Lyrics (*{suffix})",
                        )
                        if not filename:
                            return
                        destination = Path(filename)
                        if not destination.suffix:
                            destination = destination.with_suffix(suffix)
                    elif warning.standardButton(warning.clickedButton()) != QMessageBox.StandardButton.Yes:
                        return
                if item.get("source") == "Musixmatch":
                    credit = "\n".join(filter(None, [f"Lyrics provided by {item['source']}", item.get("copyright"), item.get("sourceUrl")]))
                    if synced:
                        credit = credit.replace("\n", " ").replace("[", "(").replace("]", ")")
                        content = f"[re:{credit}]\n{content}"
                    else:
                        content = f"{content.rstrip()}\n\n{credit}"
                atomic_write_text(destination, content.rstrip() + "\n", backup=True)
                self.clear_lyrics_search_index()
                self.load_lrc_file(str(destination))
                self.current_lyrics_path = destination
            except Exception as error:
                status.setText(
                    f"Could not save lyrics: {error}"
                )
                return

            dialog.accept()

        search_button.clicked.connect(run_search)
        search_job.finished.connect(show_results)
        search_job.failed.connect(search_failed)
        query_input.returnPressed.connect(run_search)
        results.itemSelectionChanged.connect(update_preview)
        provider.currentTextChanged.connect(switch_provider)
        api_key.textEdited.connect(credential_edited)
        auth_method.currentIndexChanged.connect(change_auth_method)
        preview_queue.finished.connect(show_preview)
        results.itemDoubleClicked.connect(
            lambda _: use_selected()
        )
        use_button.clicked.connect(use_selected)
        cancel_button.clicked.connect(dialog.reject)
        change_auth_method()

        try:
            dialog.exec()
        finally:
            cooldown_timer.stop()
            search_cancel.set()
            search_job.close()
            preview_queue.close()
            dialog.deleteLater()
