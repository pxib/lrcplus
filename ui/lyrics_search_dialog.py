"""Provider lyrics search dialog and metadata-based search queries."""

from pathlib import Path
from copy import deepcopy
import math
import os
from time import monotonic
from threading import Event
from unicodedata import normalize
from PySide6.QtCore import QEventLoop, Qt, QTimer, QSize
from PySide6.QtGui import QGuiApplication, QIcon, QMovie, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
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
from core.music_library import LibraryTrack, read_track
from core.search_history import (
    clean_lyrics_history, load_lyrics_search_history, save_lyrics_search_history,
    split_lyrics_query,
)
from ui.lyrics_preview import LyricsPreview
from ui.search_history import SearchHistoryLineEdit
from core.file_io import atomic_write_text
from core.fonts import content_font, library_font
from lyrics.writer import serialize_lrcx


def _lyrics_search_track(audio_path):
    audio_path = Path(audio_path)
    try:
        return read_track(audio_path)
    except (OSError, ValueError):
        return LibraryTrack(str(audio_path), audio_path.stem)


def _lyrics_search_fields(audio_path, *, track=None):
    track = track if track is not None else _lyrics_search_track(audio_path)
    title = (track.title or "").strip() or Path(audio_path).stem
    artist = (track.artist or "").strip()
    return title, artist


def _history_label(fields):
    title, artist = fields
    return f"{title} · {artist}" if artist else title


def _duration_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return seconds if math.isfinite(seconds) and seconds > 0 else 0


def _display_duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes}:{seconds:02d}"


def _match_duration_difference(item, title, artist, duration):
    def comparable(text):
        return " ".join(normalize("NFKC", str(text or "")).casefold().split())

    candidate_duration = _duration_seconds(item.get("duration"))
    if not (title and artist and duration and candidate_duration):
        return None
    if comparable(item.get("trackName") or item.get("name")) != comparable(title):
        return None
    if comparable(item.get("artistName")) != comparable(artist):
        return None
    difference = abs(candidate_duration - duration)
    return difference if difference <= 3 else None


class LyricsSearchDialogMixin:
    def search_lrclib_lyrics(self):
        """Search online providers, inspect results, and load selected lyrics."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Search Lyrics")
        dialog.resize(900, 650)
        audio_path = Path(self.current_audio_path) if self.current_audio_path else None
        track_title = track_artist = ""
        track_duration = 0
        if audio_path:
            track = _lyrics_search_track(audio_path)
            track_title, track_artist = _lyrics_search_fields(audio_path, track=track)
            track_duration = _duration_seconds(getattr(track, "duration", None))
        from core.onboarding_events import onboarding_events
        dialog.finished.connect(lambda _result: onboarding_events(self).occurred.emit("lyrics_search_closed"))

        layout = QVBoxLayout(dialog)
        if audio_path:
            duration_label = _display_duration(track_duration) if track_duration else ""
            track_details = " · ".join(filter(None, (track_title, track_artist, duration_label)))
            track_label = QLabel(f"Matching lyrics for {track_details}", dialog)
            track_label.setObjectName("lyricsSearchTrack")
            track_label.setTextFormat(Qt.TextFormat.PlainText)
            track_label.setFont(content_font(track_label.font(), track_details))
            track_label.setWordWrap(True)
            layout.addWidget(track_label)
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Lyrics source"))
        provider = QComboBox(dialog)
        provider.addItems(["LRCLIB", "SyncLRC", *CHINESE_LYRICS_PROVIDERS, "Musixmatch"])
        provider.setAccessibleName("Lyrics source")
        provider_row.addWidget(provider)
        provider_row.addStretch()
        layout.addLayout(provider_row)

        search_form = QGridLayout()
        title_input = SearchHistoryLineEdit(
            "lyrics", dialog, load=load_lyrics_search_history, save=save_lyrics_search_history,
            clean=clean_lyrics_history, label=_history_label, completion_text=lambda fields: fields[0],
        )
        artist_input = QLineEdit(dialog)
        title_input.setObjectName("lyricsSearchTitle")
        artist_input.setObjectName("lyricsSearchArtist")
        title_input.setPlaceholderText("Song title")
        artist_input.setPlaceholderText("Artist name")
        for column, label, field in (
            (0, "&Title", title_input), (1, "&Artist (optional)", artist_input),
        ):
            caption = QLabel(label, dialog)
            caption.setBuddy(field)
            search_form.addWidget(caption, 0, column)
            search_form.addWidget(field, 1, column)
            field.setFont(content_font(field.font(), ""))
            field.textChanged.connect(
                lambda _text, field=field: field.setFont(content_font(field.font(), field.text()))
            )

        def set_search_fields(fields):
            title, artist = fields
            title_input.setText(title)
            artist_input.setText(artist)

        def search_fields():
            return title_input.text().strip(), artist_input.text().strip()

        set_search_fields((track_title, track_artist))
        title_input.entry_recalled.connect(set_search_fields)
        previous_title = title_input.text()

        def split_pasted_title(text):
            title, artist = split_lyrics_query(text)
            if not artist:
                return
            # A single typed character can complete a title that happens to match the clipboard.
            cursor = title_input.cursorPosition()
            if cursor and text[:cursor - 1] + text[cursor:] == previous_title:
                return
            if text.strip() == QGuiApplication.clipboard().text().strip():
                set_search_fields((title, artist))

        def remember_title(_text):
            nonlocal previous_title
            previous_title = title_input.text()

        title_input.textEdited.connect(split_pasted_title)
        title_input.textChanged.connect(remember_title)

        search_button = QPushButton("Search", dialog)
        search_button.setAutoDefault(False)
        search_form.addWidget(search_button, 1, 2)
        search_form.setColumnStretch(0, 2)
        search_form.setColumnStretch(1, 1)
        layout.addLayout(search_form)

        recovery_row = QHBoxLayout()
        title_only_button = QPushButton("Search title only", dialog)
        other_source_button = QPushButton("Try another source", dialog)
        other_sources = QMenu(other_source_button)
        other_source_button.setMenu(other_sources)
        for button in (title_only_button, other_source_button):
            button.setAutoDefault(False)
            button.hide()
            recovery_row.addWidget(button)
        recovery_row.addStretch()

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

        results = QTableWidget(0, 6, splitter)
        results.setFont(library_font(results.font()))
        results.setHorizontalHeaderLabels(
            ["Title", "Album", "Artist", "Timing", "Duration", "Match"]
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
        results.setColumnHidden(5, True)
        results.verticalHeader().setVisible(False)

        header = results.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        preview_panel = QWidget(splitter)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(QLabel("Lyrics preview"))
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
        preview_layout.addWidget(preview_controls_widget)
        preview_controls_widget.setVisible(provider.currentText() in CHINESE_LYRICS_PROVIDERS)

        preview = LyricsPreview(preview_panel)
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
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setWordWrap(True)
        status_layout.addWidget(search_loading)
        status_layout.addWidget(search_result)
        status_layout.addWidget(status, 1)
        layout.insertWidget(layout.indexOf(splitter), status_widget)
        layout.insertLayout(layout.indexOf(splitter), recovery_row)
        search_loading.hide()
        search_result.hide()

        buttons = QHBoxLayout()
        destination_label = QLabel(dialog)
        destination_label.setObjectName("lyricsSearchDestination")
        destination_label.setTextFormat(Qt.TextFormat.PlainText)
        destination_label.setWordWrap(True)
        use_button = QPushButton("Use Selected Lyrics", dialog)
        use_button.setEnabled(False)
        use_translation_button = QPushButton("Apply Only Translation", dialog)
        use_translation_button.setEnabled(False)
        use_translation_button.setVisible(False)
        cancel_button = QPushButton("Cancel", dialog)
        for button in (use_button, use_translation_button, cancel_button):
            button.setAutoDefault(False)
        buttons.addWidget(destination_label, 1)
        buttons.addWidget(use_translation_button)
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
        search_state = None
        submitted_fields = None
        cooldown_timer = QTimer(dialog)
        cooldown_timer.setInterval(100)
        provider_errors = (LRCLibError, MusixmatchError, LyricsProviderError, SyncLRCError)

        def set_search_indicator(state):
            nonlocal search_state
            search_state = state
            placeholders = {
                "loading": "Results will appear here when the search finishes.",
                "empty": "No matching lyrics to preview.",
                "error": "Retry the search or try another source.",
                "success": "Select a result to preview its lyrics.",
            }
            preview.setPlaceholderText(placeholders.get(
                state, "Search for this track to see matching lyrics." if audio_path
                else "Enter a song title to find lyrics.",
            ))
            title_only_button.setVisible(state == "empty" and all(search_fields()))
            other_source_button.setVisible(state in {"empty", "error"})
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
                return "Word-synced"
            if item.get("syncedLyrics") or item.get("hasSyncedLyrics"):
                return "Line-synced"
            if item.get("plainLyrics"):
                return "Plain text"
            if item.get("source") in ("Musixmatch", "SyncLRC", *CHINESE_LYRICS_PROVIDERS):
                return "Checking"
            return "Unknown" if item.get("hasSyncedLyrics", False) is None else "No"

        def update_search_button():
            remaining = math.ceil(max(
                0, getattr(self, cooldown_attribute(), 0) - monotonic()
            ))
            enabled = not search_in_progress and remaining == 0
            search_button.setEnabled(enabled)
            title_only_button.setEnabled(enabled)
            title_only_button.setVisible(search_state == "empty" and all(search_fields()))
            action = "Retry" if search_state == "error" else "Search"
            search_button.setText(f"{action} ({remaining}s)" if remaining else action)
            if remaining:
                cooldown_timer.start()
            else:
                cooldown_timer.stop()

        cooldown_timer.timeout.connect(update_search_button)
        title_input.textChanged.connect(update_search_button)
        artist_input.textChanged.connect(update_search_button)
        update_search_button()

        def selected_record():
            row_index = results.currentRow()
            if 0 <= row_index < len(records):
                return records[row_index]
            return None

        def destination_for(item, only_translation=False):
            if not audio_path:
                return None
            if only_translation:
                source_path = getattr(self, "current_lyrics_path", None)
                return Path(source_path).with_suffix(".lrcx") if source_path else None
            if include_translations.isChecked() and item.get("translatedLyrics"):
                return audio_path.with_suffix(".lrcx")
            if item.get("syncedLyrics"):
                return audio_path.with_suffix(".lrcx" if item.get("lyricsFormat") == "lrcx" else ".lrc")
            return audio_path.with_suffix(".txt") if item.get("plainLyrics") else None

        def update_preview():
            use_button.setEnabled(False)
            use_translation_button.setEnabled(False)
            destination_label.clear()
            destination_label.setToolTip("")
            use_translation_button.setVisible(
                provider.currentText() in CHINESE_LYRICS_PROVIDERS
                and preview_tabs.currentIndex() == 1
            )
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
                preview.setPlainText("This result does not contain lyrics. Try another result.")
            destination = destination_for(item)
            translation_destination = (
                destination_for(item, only_translation=True)
                if has_translation and not use_translation_button.isHidden()
                and getattr(self, "lyric_lines", None) else None
            )
            use_button.setEnabled(destination is not None)
            use_translation_button.setEnabled(translation_destination is not None)
            if destination:
                message = f"Save to {destination.name}"
                if translation_destination and translation_destination != destination:
                    message += f"\nTranslation only: {translation_destination.name}"
                destination_label.setText(message)
                tooltip = str(destination)
                if translation_destination and translation_destination != destination:
                    tooltip += f"\nTranslation only: {translation_destination}"
                destination_label.setToolTip(tooltip)
            elif not audio_path and (synced or plain):
                destination_label.setText("Open an audio file to save lyrics.")
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
                item["_preview_error"] = f"{payload}\nTry another result."
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
            nonlocal search_in_progress, submitted_fields
            search_cancel.set()
            search_job.cancel()
            preview_queue.cancel()
            search_in_progress = False
            submitted_fields = None
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
            nonlocal active_provider, records, submitted_fields
            provider_states[active_provider] = {
                "records": list(records), "fields": (title_input.text(), artist_input.text()),
                "submitted_fields": submitted_fields,
                "row": results.currentRow(),
                "scroll": results.verticalScrollBar().value(),
                "preview_scroll": preview.verticalScrollBar().value(),
                "state": None if search_in_progress else search_state,
                "status": ("Search cancelled. Search again to refresh."
                           if search_in_progress else status.text()),
            }
            reset_provider()
            active_provider = provider.currentText()
            saved = provider_states.get(active_provider)
            if saved is not None:
                records = list(saved["records"])
                submitted_fields = saved["submitted_fields"]
                set_search_fields(saved["fields"])
                check_result_timing()
                populate_results(saved["row"])
                results.verticalScrollBar().setValue(saved["scroll"])
                preview.verticalScrollBar().setValue(saved["preview_scroll"])
                status.setText(saved["status"])
                set_search_indicator(saved["state"])
                update_search_button()

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
            nonlocal search_in_progress, search_cancel, submitted_fields
            update_search_button()
            if not search_button.isEnabled():
                return
            fields = search_fields()
            if not fields[0]:
                status.setText("Enter a song title.")
                title_input.setFocus()
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
            title_input.remember_search(fields)
            search_cancel.set()
            preview_queue.cancel()
            search_cancel = Event()
            submitted_fields = fields
            set_search_indicator("loading")
            status.setText(f"Searching {source}...")
            results.setRowCount(0)
            preview.clear()
            records.clear()
            update_preview()
            search_in_progress = True
            setattr(self, cooldown_attribute(), monotonic() + 5)
            update_search_button()
            from core.onboarding_events import onboarding_events
            onboarding_events(self).occurred.emit("lyrics_search_started")
            search_job.start(fetch_results, fields, source, key, search_cancel, auth_method.currentData())

        def search_title_only():
            if all(search_fields()):
                artist_input.clear()
                run_search()

        def submit_title():
            popup = title_input.completer().popup()
            if not (popup.isVisible() and popup.currentIndex().isValid()):
                run_search()

        def search_other_source(source):
            fields = search_fields()
            if not fields[0] or source == active_provider:
                return
            provider.setCurrentText(source)
            if submitted_fields != fields:
                reset_provider()
            set_search_fields(fields)
            if search_state != "success":
                run_search()

        for index in range(provider.count()):
            source = provider.itemText(index)
            action = other_sources.addAction(source)
            action.triggered.connect(lambda _checked=False, source=source: search_other_source(source))

        def update_other_sources():
            for action in other_sources.actions():
                action.setVisible(action.text() != active_provider)

        other_sources.aboutToShow.connect(update_other_sources)

        def fetch_results(fields, source, key, cancel, mode):
            title, artist = fields
            query = f"{artist} - {title}" if artist else title
            try:
                if source == "SyncLRC":
                    return search_synclrc(query, cancel)
                if source == "Musixmatch":
                    return search_musixmatch(
                        query, key, cancel, auth_mode=mode, title=title, artist=artist,
                    )
                if source in CHINESE_LYRICS_PROVIDERS:
                    return search_chinese_lyrics(source, query, cancel)
                return search_lrclib(query, cancel)
            except provider_errors as error:
                return error

        def show_results(payload):
            nonlocal search_in_progress
            search_in_progress = False
            if isinstance(payload, provider_errors):
                from core.onboarding_events import onboarding_events
                onboarding_events(self).occurred.emit("lyrics_search_failed")
                set_search_indicator("error")
                setattr(self, cooldown_attribute(), max(
                    getattr(self, cooldown_attribute(), 0), monotonic() + payload.retry_after,
                ))
                update_search_button()
                status.setText(str(payload))
                return
            update_search_button()
            if not isinstance(payload, list):
                payload = []

            records.extend(payload)
            from core.onboarding_events import onboarding_events
            onboarding_events(self).occurred.emit("lyrics_search_results" if records else "lyrics_search_empty")

            check_result_timing()
            populate_results()
            if records:
                set_search_indicator("success")
                status.setText("1 result" if len(records) == 1 else f"{len(records)} results")
            else:
                set_search_indicator("empty")
                status.setText("No lyrics found.")

        def populate_results(selected_row=None):
            matches = {}
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
                seconds = _duration_seconds(item.get("duration"))
                duration = _display_duration(seconds) if seconds else ""
                difference = _match_duration_difference(item, track_title, track_artist, track_duration)
                if difference is not None:
                    matches[row_index] = difference

                for column, value in enumerate(
                    [title, album, artist, timing_label(item), duration,
                     "Close match" if difference is not None else ""]
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
                    if column == 5 and difference is not None:
                        font = table_item.font()
                        font.setBold(True)
                        table_item.setFont(font)
                        table_item.setToolTip("Same title and artist, with a duration within 3 seconds.")
                    results.setItem(
                        row_index, column, table_item
                    )

            results.blockSignals(False)
            results.setColumnHidden(5, not matches)
            if selected_row is None:
                selected_row = min(matches, key=matches.get) if matches else 0
            if 0 <= selected_row < len(records):
                results.selectRow(selected_row)
                results.scrollToItem(results.item(selected_row, 0))
            else:
                update_preview()

        def search_failed(error):
            nonlocal search_in_progress
            from core.onboarding_events import onboarding_events
            onboarding_events(self).occurred.emit("lyrics_search_failed")
            search_in_progress = False
            set_search_indicator("error")
            update_search_button()
            status.setText(f"Search failed: {error}")

        def use_selected(only_translation=False):
            item = selected_record()
            if item is None:
                status.setText("Select a result first.")
                return
            if not audio_path:
                status.setText("Open an audio file before loading lyrics.")
                return
            if self.current_audio_path != audio_path:
                status.setText("The track changed. Reopen Lyrics Search to save lyrics for the current track.")
                use_button.setEnabled(False)
                use_translation_button.setEnabled(False)
                return
            destination = destination_for(item, only_translation)

            if only_translation:
                if (provider.currentText() not in CHINESE_LYRICS_PROVIDERS
                        or not item.get("translatedLyrics")):
                    status.setText("No provider translation is available for this result.")
                    return
                item = converted_translations(item, translation_script.currentData())
                translation_rows = item.get("translationRows") or {}
                source_path = getattr(self, "current_lyrics_path", None)
                current_lines = getattr(self, "lyric_lines", None) or []
                if not source_path or not current_lines:
                    status.setText("Open a timed lyrics file before applying translations.")
                    return
                merged_lines = deepcopy(current_lines)
                by_timestamp = {round(float(line.start) * 1000): line for line in merged_lines}
                matched = 0
                for timestamp, translated in translation_rows.items():
                    line = by_timestamp.get(int(timestamp))
                    if line is None or not str(translated).strip():
                        continue
                    rows = line.text.split("\n")
                    if translated in rows:
                        continue
                    line.translation_lines = list(line.translation_lines or [])
                    line.translation_lines.append(len(rows))
                    line.text = line.text + "\n" + translated
                    matched += 1
                if not matched:
                    status.setText("No translation timestamps matched the current lyrics.")
                    return
                item = {
                    **item,
                    "syncedLyrics": serialize_lrcx(merged_lines),
                    "plainLyrics": "",
                    "lyricsFormat": "lrcx",
                }

            if not only_translation and include_translations.isChecked() and item.get("translatedLyrics"):
                item = converted_translations(item, translation_script.currentData())
                item = {**item, "syncedLyrics": item["translatedLyrics"], "lyricsFormat": "lrcx"}

            synced = item.get("syncedLyrics")
            plain = item.get("plainLyrics")

            if not synced and not plain:
                status.setText("That result contains no lyrics.")
                return

            content = synced or plain
            try:
                current_lyrics = getattr(self, "current_lyrics_path", None)
                destination_exists = destination.exists()
                has_lyrics = (
                    bool(getattr(self, "lyric_lines", []))
                    or (current_lyrics and Path(current_lyrics).is_file())
                    or destination_exists
                    or any(audio_path.with_suffix(suffix).is_file()
                           for suffix in (".lrc", ".lrcx", ".txt"))
                )
                if has_lyrics:
                    file_action = "Overwrite the file at" if destination_exists else "Save a new file at"
                    warning = QMessageBox(
                        QMessageBox.Icon.Warning,
                        "Overwrite Lyrics",
                        (
                            f'"{audio_path.name}" already has lyrics.\n\n'
                            + ("Append the selected provider translations to the current lyrics and save as LRCX?\n"
                               if only_translation else
                               f"Replace them with the selected lyrics from {provider.currentText()}?\n")
                            + f"{file_action} {destination}."
                        ),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                        dialog,
                    )
                    save_as = warning.addButton("Save as...", QMessageBox.ButtonRole.ActionRole)
                    warning.setDefaultButton(QMessageBox.StandardButton.Cancel)
                    warning.setEscapeButton(QMessageBox.StandardButton.Cancel)
                    try:
                        warning.exec()
                    finally:
                        warning.deleteLater()
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
                if self.current_audio_path != audio_path:
                    status.setText("The track changed. Reopen Lyrics Search to save lyrics for the current track.")
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
        title_only_button.clicked.connect(search_title_only)
        search_job.finished.connect(show_results)
        search_job.failed.connect(search_failed)
        title_input.returnPressed.connect(submit_title)
        artist_input.returnPressed.connect(run_search)
        results.itemSelectionChanged.connect(update_preview)
        provider.currentTextChanged.connect(switch_provider)
        api_key.textEdited.connect(credential_edited)
        auth_method.currentIndexChanged.connect(change_auth_method)
        preview_queue.finished.connect(show_preview)
        results.itemDoubleClicked.connect(lambda _: preview.setFocus())
        use_button.clicked.connect(lambda: use_selected())
        use_translation_button.clicked.connect(lambda: use_selected(only_translation=True))
        cancel_button.clicked.connect(dialog.reject)
        change_auth_method()

        try:
            if getattr(self, "_active_onboarding", None) is not None:
                loop = QEventLoop(dialog)
                dialog.finished.connect(loop.quit)
                dialog.setWindowModality(Qt.WindowModality.NonModal)
                dialog.show()
                loop.exec()
            else:
                dialog.exec()
        finally:
            cooldown_timer.stop()
            search_cancel.set()
            search_job.close()
            preview_queue.close()
            dialog.deleteLater()
