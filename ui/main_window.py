"""Main player window and workspace orchestration."""

from pathlib import Path
import math
import random
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMainWindow,
    QStackedWidget,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from core.pcm_playback import PcmPlayback
from core.gapless import GaplessMediaPlayer
from core.audio_controller import AudioController, snap_volume_percentage
from core.queue_controller import QueueControllerMixin
from core.lyrics_controller import LyricsController, _FuriganaProgressBridge
from core.settings import (
    load_all_settings,
    save_all_settings,
    get_startup_settings,
    save_startup_settings,
    get_update_settings,
    save_update_settings,
    get_appearance_settings,
    save_appearance_settings,
    load_lrc_search_folders,
    get_global_lyrics_settings,
    get_playback_settings,
    save_playback_settings,
    get_karaoke_settings,
    save_karaoke_settings,
    apply_karaoke_settings,
    get_local_lyrics_overrides,
    save_global_lyrics_settings,
    save_local_lyrics_overrides,
    save_disabled_plugins,
    load_music_library,
)
from core.recent import get_recent_songs
from core.music_library import LibraryTrack
from lyrics.widgets import FuriganaWidget, ScrollingLyricsWidget
from lyrics.waveform import WaveformWidget, find_ffmpeg
from lyrics.writer import convert_lyrics
from ui.settings_dialog import SettingsDialog
from ui.lrc_editor import LrcEditorDialog
from ui.queue_actions import QueueActionsMixin
from ui.library_lyrics_search import LibraryLyricsSearchMixin
from ui.audio_devices import AudioDeviceControllerMixin
from ui.artwork_background import ArtworkBackgroundWidget, extract_embedded_cover
from ui.player_dialogs import ClickableSlider, ClickableTimeLabel, PitchChangerDialog
from ui.settings_controls import ThemedComboBox
from core.booru_artwork import fetch_booru_artwork
from core.theme import build_theme_palette
from ui.easter_eggs import BudgetBrandLabel, CatSeekTrack, EasterEggs
from ui.player_library import PlayerLibraryMixin
from ui.player_updates import PlayerUpdatesMixin
from ui.lyrics_search_dialog import LyricsSearchDialogMixin, _lyrics_search_query


class Player(
    PlayerLibraryMixin,
    PlayerUpdatesMixin,
    LyricsSearchDialogMixin,
    LibraryLyricsSearchMixin,
    QueueActionsMixin,
    QueueControllerMixin,
    AudioDeviceControllerMixin,
    AudioController,
    LyricsController,
    QMainWindow,
):
        queue_changed = Signal()
        lyrics_renderers_created = Signal()

        def __init__(self, plugin_manager=None):
            super().__init__()

            self.plugin_manager = plugin_manager
            self._booru_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="booru-artwork",
            )
            self._booru_future = None
            self._booru_request_id = 0

            self.setWindowTitle("LRCPlus")
            self.resize(1180, 760)
            self.setObjectName("lrcPlusWindow")

            self.startup_settings = get_startup_settings()
            self.update_settings = get_update_settings()
            self._update_check_worker = None
            self._update_download_worker = None
            self._update_progress_dialog = None
            self._update_available_dialog = None
            self._close_after_update_cancel = False
            self._update_handoff_started = False
            self.appearance_settings = get_appearance_settings()

            # --------------------------------------------------
            # AUDIO
            # --------------------------------------------------

            self.media_player = GaplessMediaPlayer(self)
            self.media_player.next_path = self._gapless_next_path
            self.media_player.gaplessFallback.connect(
                self._handle_gapless_fallback
            )

            self.audio_output = QAudioOutput(self)
            self._pcm_buffer_output = None
            self._pcm_sink = None
            self._pcm_sink_io = None
            self._pcm_pending = bytearray()
            self._pcm_worker = PcmPlayback(self)
            self._pcm_worker.route_started.connect(self._activate_pcm_route)
            self._pcm_worker.failed.connect(self._pcm_output_failed)
            self._playback_save_timer = QTimer(self)
            self._playback_save_timer.setSingleShot(True)
            self._playback_save_timer.setInterval(300)
            self._playback_save_timer.timeout.connect(
                lambda: save_playback_settings(self.playback_settings)
            )
            self.pitch_settings = {"mode": "Semitones", "value": 0}
            self.pitch_semitones = 0.0
            self._pcm_drain_timer = QTimer(self)
            self._pcm_drain_timer.setInterval(10)
            self._pcm_drain_timer.timeout.connect(self._drain_pcm_pending)

            self.media_player.setAudioOutput(
                self.audio_output
            )

            self.current_audio_path = None
            self.playlist = []
            self.current_index = -1
            self._priority_queue_indices = []
            self._shuffle_history = []
            self._shuffle_forward_history = []
            self.lrc_search_paths = load_lrc_search_folders()
            # Persistent music library. Playback controls remain separate from
            # the library; the library is the primary workspace.
            music_library = load_music_library()
            self.library_folders = music_library["folders"]
            self.library_tracks = []
            self.library_column_state = music_library.get("columns", {}) or {}
            # Older library builds stored column positions without the new
            # embedded Album cover column. Shift that saved layout once so a
            # user's existing visibility/order/width preferences stay intact.
            old_order = self.library_column_state.get("order") if isinstance(self.library_column_state, dict) else None
            if isinstance(old_order, list) and len(old_order) == 11:
                try:
                    migrated = dict(self.library_column_state)
                    migrated["hidden"] = [int(i) + 1 for i in migrated.get("hidden", [])]
                    migrated["widths"] = {str(int(i) + 1): value for i, value in migrated.get("widths", {}).items()}
                    migrated["order"] = [0] + [int(i) + 1 for i in old_order]
                    self.library_column_state = migrated
                except (TypeError, ValueError, AttributeError):
                    pass
            for raw in music_library["tracks"]:
                if not isinstance(raw, dict):
                    continue
                try:
                    track = LibraryTrack(**{k: raw.get(k, "") for k in LibraryTrack.__dataclass_fields__})
                    if Path(track.path).is_file():
                        self.library_tracks.append(track)
                except (TypeError, ValueError):
                    continue
            self._library_scan_worker = None
            # Artwork stays in memory only. Covers are always read from the song
            # files themselves and are not written into the library settings.
            self._library_cover_cache = OrderedDict()
            self._library_cover_cache_limit = 150
            self._library_cover_load_pending = False

            # Global defaults. Local overrides are resolved when a file loads.
            self.global_lyrics_settings = get_global_lyrics_settings()
            self.playback_settings = get_playback_settings()
            self.playback_speed = self.playback_settings.get("speed_percent", 100) / 100.0
            self.pitch_linked_to_speed = self.playback_settings.get(
                "pitch_linked_to_speed", False
            )
            self.media_player.setPlaybackRate(self.playback_speed)
            self._set_native_pitch_compensation()
            self.pitch_settings = {
                "mode": self.playback_settings.get("pitch_mode", "Semitones"),
                "value": self.playback_settings.get("pitch_value", 0),
            }
            _pmode = self.pitch_settings["mode"]
            _pvalue = float(self.pitch_settings["value"])
            self.pitch_semitones = (
                _pvalue * 12.0 if _pmode == "Octaves"
                else 12.0 * math.log2(max(0.01, _pvalue) / 100.0)
                if _pmode == "Percent" else _pvalue
            )
            self._apply_output_device(self.playback_settings)
            self.karaoke_settings = get_karaoke_settings()
            self.local_lyrics_overrides = {}

            self.scrolling_lyrics_enabled = (
                self.global_lyrics_settings["scrolling_lyrics"]
            )
            self.lyrics_direction = (
                self.global_lyrics_settings["lyrics_direction"]
            )
            self.lyrics_padding = (
                self.global_lyrics_settings["lyrics_padding"]
            )
            self.ruby_padding = (
                self.global_lyrics_settings["ruby_padding"]
            )
            self.show_ruby = (
                self.global_lyrics_settings["show_ruby"]
            )
            self.show_romaji = self.global_lyrics_settings.get("show_romaji", False)
            self.all_romaji = self.global_lyrics_settings.get("all_romaji", False)
            self.ruby_position = self.global_lyrics_settings.get("ruby_position", "above")
            self.limited_reading_rendering = self.global_lyrics_settings["limited_reading_rendering"]
            self.limited_reading_range = self.global_lyrics_settings["limited_reading_range"]
            self.fade_in_readings = self.global_lyrics_settings["fade_in_readings"]

            self.is_seeking = False

            # --------------------------------------------------
            # LYRICS
            # --------------------------------------------------

            self.lyric_lines = []
            self.lyrics = []
            self.lyric_times = []
            self.current_lyrics_path = None

            self.current_line = -1
            self.last_lyric_text = None

            # --------------------------------------------------
            # LYRIC DISPLAY
            # --------------------------------------------------

            self._lyric_widget = None
            self._scrolling_lyrics_widget = None
            self._lyrics_renderers_ready = False

            # --------------------------------------------------
            # LYRICS LIST
            # --------------------------------------------------

            self.lyrics_list_collapsed = bool(
                load_all_settings().get("lyrics_list_collapsed", False)
            )

            self.lyrics_list_toggle = QPushButton(
                "Lyrics [+]" if self.lyrics_list_collapsed else "Lyrics [-]"
            )
            self.lyrics_list_toggle.setFlat(True)
            self.lyrics_list_toggle.setCursor(
                Qt.CursorShape.PointingHandCursor
            )
            self.lyrics_list_toggle.clicked.connect(
                self.toggle_lyrics_list
            )

            # Compatibility alias for existing controller code.
            self.lyrics_list_label = self.lyrics_list_toggle

            self.lyrics_list_widget = QListWidget()
            self.lyrics_list_widget.setMaximumHeight(150)
            self.lyrics_list_widget.setVisible(
                not self.lyrics_list_collapsed
            )
            self.lyrics_list_widget.itemClicked.connect(
                self.jump_to_lyric_item
            )

            self.furigana_progress_bar = QProgressBar()
            self.furigana_progress_bar.setTextVisible(True)
            self.furigana_progress_bar.setMinimumHeight(18)
            self.furigana_progress_bar.setVisible(False)

            # --------------------------------------------------
            # TIME LABELS
            # --------------------------------------------------

            self.current_time_label = ClickableTimeLabel("00:00")
            self.current_time_label.clicked.connect(self.toggle_time_display)
            self.current_time_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self.current_time_label.setToolTip("Click to show remaining time")
            self.show_remaining_time = False
            self.duration_label = QLabel("00:00")

            self.is_seeking = False
            self.last_volume = snap_volume_percentage(
                self.playback_settings.get("volume", 80)
            )
            self.playback_settings["volume"] = self.last_volume
            self.is_muted = False

            # --------------------------------------------------
            # PLAYBACK CONTROL STATE
            # --------------------------------------------------

            # --------------------------------------------------
            # SEEK BAR
            # --------------------------------------------------

            self.seek_slider = ClickableSlider(
                Qt.Orientation.Horizontal
            )
            self.seek_slider.setRange(0, 0)
            self.seek_slider.setSingleStep(5000)
            self.seek_slider.setPageStep(5000)
            self.seek_slider.setFixedHeight(20)
            self.seek_slider.sliderPressed.connect(
                self.start_seeking
            )
            self.seek_slider.sliderReleased.connect(
                self.finish_seeking
            )
            self.seek_slider.wheelSeekRequested.connect(
                self._seek_from_waveform
            )
            
            # Interactive waveform overview. It mirrors the seek bar but also
            # shows lyric boundaries and supports direct seeking.
            self.waveform_widget = WaveformWidget(defer_hidden_load=True)
            self.waveform_widget.setVisible(
                self.playback_settings.get("show_waveform", True)
                and find_ffmpeg() is not None
            )
            self.waveform_widget.seekRequested.connect(
                self._seek_from_waveform
            )

            # --------------------------------------------------
            # VOLUME
            # --------------------------------------------------

            self.volume_slider = QSlider(
                Qt.Orientation.Horizontal
            )
            self.volume_slider.setObjectName("volumeSlider")
            self.volume_slider.setRange(0, 300)
            self.volume_slider.setSingleStep(5)
            self.volume_slider.setPageStep(5)
            self.volume_slider.setValue(self.last_volume)
            self.volume_slider.setFixedWidth(120)
            self.volume_percentage_label = QLabel(f"{self.last_volume}%")
            self.volume_percentage_label.setFixedWidth(42)
            self.volume_percentage_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.volume_slider.valueChanged.connect(
                self.handle_volume_change
            )
            QTimer.singleShot(
                0,
                lambda: self._warn_if_high_volume(
                    self.volume_slider.value()
                ),
            )

            self.shuffle_enabled = self.playback_settings.get("shuffle_enabled", False)
            self.shuffle_button = QPushButton("Shuffle: Off")
            self.shuffle_button.setObjectName("dockAction")
            self.shuffle_button.clicked.connect(self.cycle_shuffle_mode)
            self._update_shuffle_button_text()

            # --------------------------------------------------
            # OPEN BUTTON
            # --------------------------------------------------

            self.open_button = QToolButton()
            self.open_button.setText("Open")
            self.open_button.setPopupMode(
                QToolButton.ToolButtonPopupMode.MenuButtonPopup
            )
            self.open_button.clicked.connect(self.open_audio)

            self.open_menu = QMenu(self.open_button)

            open_audio_action = QAction(
                "Open Audio...",
                self,
            )
            open_audio_action.setShortcut(
                QKeySequence("Ctrl+O")
            )
            open_audio_action.triggered.connect(
                self.open_audio
            )
            self.open_menu.addAction(open_audio_action)

            add_queue_action = QAction("Add to Queue…", self)
            add_queue_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
            add_queue_action.triggered.connect(self.add_files_to_queue)
            self.open_menu.addAction(add_queue_action)

            open_lrc_action = QAction(
                "Open Lyrics...",
                self,
            )
            open_lrc_action.setShortcut(
                QKeySequence("Ctrl+L")
            )
            open_lrc_action.triggered.connect(
                self.open_lrc
            )
            self.open_menu.addAction(open_lrc_action)

            self.open_button.setMenu(self.open_menu)

            self.settings_button = QPushButton(
                "Settings"
            )
            self.settings_button.clicked.connect(
                self.open_settings
            )

            # --------------------------------------------------
            # LYRICS MENU
            # --------------------------------------------------

            self.lyrics_button = QToolButton()
            self.lyrics_button.setText("LRC")
            self.lyrics_button.setPopupMode(
                QToolButton.ToolButtonPopupMode.MenuButtonPopup
            )
            self.lyrics_button.clicked.connect(
                self.edit_current_lyrics
            )

            self.lyrics_menu = QMenu(self.lyrics_button)

            edit_lyrics_action = QAction(
                "Edit Lyrics",
                self,
            )
            edit_lyrics_action.triggered.connect(
                self.edit_current_lyrics
            )

            save_as_action = QAction(
                "Save As...",
                self,
            )
            save_as_action.triggered.connect(
                self.save_lyrics_as
            )

            self.lyrics_menu.addAction(edit_lyrics_action)
            self.lyrics_menu.addAction(save_as_action)
            reload_renderer_action = QAction("Reload Renderer", self)
            reload_renderer_action.setToolTip("Reload lyrics without restarting playback")
            reload_renderer_action.triggered.connect(self.reload_renderer)
            self.lyrics_menu.addAction(reload_renderer_action)
            self.lyrics_menu.addSeparator()
            search_lyrics_action = QAction("Search Lyrics", self)
            search_lyrics_action.setObjectName("search_lyrics")
            search_lyrics_action.triggered.connect(self.search_lrclib_lyrics)
            self.lyrics_menu.addAction(search_lyrics_action)
            self.lyrics_button.setMenu(self.lyrics_menu)

            self.recent_button = QPushButton(
                "Recent"
            )
            self.recent_button.clicked.connect(
                self.show_recent_songs
            )

            self.playlist_button = QPushButton(
                "Queue / Playlists"
            )
            self.playlist_button.clicked.connect(
                self.show_playlist
            )

            self.library_button = QPushButton("Library")
            self.library_button.setObjectName("workspaceButton")
            self.library_button.setCheckable(True)
            self.library_button.clicked.connect(lambda: self.workspace_stack.setCurrentWidget(self.library_page))
            self.lyrics_view_button = QPushButton("Lyrics")
            self.lyrics_view_button.setObjectName("workspaceButton")
            self.lyrics_view_button.setCheckable(True)
            self.lyrics_view_button.clicked.connect(lambda: self.workspace_stack.setCurrentWidget(self.lyrics_page))
            self.workspace_button_group = QButtonGroup(self)
            self.workspace_button_group.setExclusive(True)
            self.workspace_button_group.addButton(self.library_button)
            self.workspace_button_group.addButton(self.lyrics_view_button)

            # --------------------------------------------------
            # TRANSPORT CONTROLS
            # --------------------------------------------------

            self.previous_button = QPushButton("<<")
            self.rewind_button = QPushButton("< 5s")
            self.play_button = QPushButton("Play")
            self.stop_button = QPushButton("Stop")
            self.forward_button = QPushButton("5s >")
            self.next_button = QPushButton(">>")

            for button in (self.previous_button, self.rewind_button, self.play_button, self.stop_button, self.forward_button, self.next_button):
                button.setEnabled(False)
                button.setObjectName("transportButton")
                button.setMinimumHeight(36)

            self.previous_button.setToolTip("Previous track")
            self.rewind_button.setToolTip("Seek backward 5 seconds")
            self.play_button.setObjectName("primaryTransportButton")
            self.play_button.setToolTip("Play or pause (Space)")
            self.stop_button.setToolTip("Stop playback")
            self.forward_button.setToolTip("Seek forward 5 seconds")
            self.next_button.setToolTip("Next track")
            for button, width in (
                (self.previous_button, 58),
                (self.rewind_button, 62),
                (self.play_button, 76),
                (self.stop_button, 58),
                (self.forward_button, 62),
                (self.next_button, 58),
            ):
                button.setFixedWidth(width)

            self.previous_button.clicked.connect(self.previous_track)
            self.rewind_button.clicked.connect(lambda: self.skip_seconds(-5))
            self.play_button.clicked.connect(self.toggle_playback)
            self.stop_button.clicked.connect(self.stop_playback)
            self.forward_button.clicked.connect(lambda: self.skip_seconds(5))
            self.next_button.clicked.connect(self.next_track)

            self.mute_button = QPushButton(
                "Mute"
            )
            self.mute_button.setObjectName("dockAction")
            self.mute_button.clicked.connect(
                self.toggle_mute
            )

            self.pitch_options_button = QPushButton()
            self.pitch_options_button.setText("Pitch")
            self.pitch_options_button.setObjectName("dockAction")
            self.pitch_options_button.setToolTip("Adjust playback speed and pitch")
            self.pitch_options_button.clicked.connect(self.open_pitch_options)
            self.pitch_status_label = QLabel(
                f"{self._displayed_pitch_semitones():+.1f} st"
            )
            self.pitch_status_label.setMinimumWidth(48)

            # --------------------------------------------------
            # LAYOUT
            # --------------------------------------------------

            self.seek_slider.setObjectName("seekSlider")
            self.current_time_label.setObjectName("timeLabel")
            self.duration_label.setObjectName("timeLabel")

            seek_layout = QHBoxLayout()
            seek_layout.setContentsMargins(0, 0, 0, 0)
            seek_layout.setSpacing(10)
            seek_layout.addWidget(self.current_time_label)
            self.seek_track = CatSeekTrack(self.seek_slider, self.media_player)
            seek_layout.addWidget(self.seek_track, 1)
            seek_layout.addWidget(self.duration_label)

            volume_layout = QHBoxLayout()
            volume_layout.setContentsMargins(0, 0, 0, 0)
            volume_layout.setSpacing(8)
            self.playback_source_combo = ThemedComboBox()
            self.playback_source_combo.setObjectName("playbackSourceCombo")
            self.playback_source_combo.setAccessibleName("Playback source")
            self.playback_source_combo.addItem("Library", "library")
            self.playback_source_combo.addItem("Queue / Playlist", "playlist")
            self.playback_source_combo.setToolTip(
                "Choose the tracks used by Play, Previous, Next, and Shuffle. "
                "Library plays your full music library. Queue / Playlist follows your queue."
            )
            self._update_playback_source_control()
            self.playback_source_combo.currentIndexChanged.connect(
                lambda _index: self.set_playback_source(self.playback_source_combo.currentData())
            )
            volume_layout.addWidget(QLabel("Play from"))
            volume_layout.addWidget(self.playback_source_combo)
            volume_layout.addWidget(self.shuffle_button)
            volume_layout.addSpacing(8)
            volume_layout.addWidget(self.pitch_options_button)
            volume_layout.addWidget(self.pitch_status_label)
            volume_layout.addStretch(1)
            volume_layout.addWidget(self.mute_button)
            volume_layout.addWidget(QLabel("Volume"))
            volume_layout.addWidget(self.volume_slider)
            volume_layout.addWidget(self.volume_percentage_label)

            player_controls_layout = QHBoxLayout()
            player_controls_layout.setContentsMargins(0, 0, 0, 0)
            player_controls_layout.setSpacing(6)
            player_controls_layout.addWidget(self.previous_button)
            player_controls_layout.addWidget(self.rewind_button)
            player_controls_layout.addWidget(self.play_button)
            player_controls_layout.addWidget(self.stop_button)
            player_controls_layout.addWidget(self.forward_button)
            player_controls_layout.addWidget(self.next_button)
            player_controls_layout.addSpacing(14)
            player_controls_layout.addLayout(seek_layout, 1)

            player_dock = QWidget()
            player_dock.setObjectName("playerDock")
            player_dock_layout = QVBoxLayout(player_dock)
            player_dock_layout.setContentsMargins(14, 10, 14, 10)
            player_dock_layout.setSpacing(8)
            player_dock_layout.addLayout(player_controls_layout)
            player_dock_layout.addLayout(volume_layout)

            header = QWidget()
            header.setObjectName("mainHeader")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 10)
            header_layout.setSpacing(8)
            self.brand_label = brand_label = BudgetBrandLabel()
            brand_label.setObjectName("brandLabel")
            header_layout.addWidget(brand_label)
            header_layout.addSpacing(12)
            header_layout.addWidget(self.library_button)
            header_layout.addWidget(self.lyrics_view_button)
            header_layout.addStretch(1)

            self.plugins_menu = QMenu(self)
            self.plugins_button = QToolButton()
            self.plugins_button.setText("Plugins")
            self.plugins_button.setPopupMode(
                QToolButton.ToolButtonPopupMode.InstantPopup
            )
            self.plugins_button.setMenu(self.plugins_menu)
            self.plugins_button.setVisible(False)
            for button in (
                self.plugins_button,
                self.open_button,
                self.lyrics_button,
                self.recent_button,
                self.playlist_button,
                self.settings_button,
            ):
                button.setObjectName("headerAction")
                button.setMinimumHeight(34)
            header_layout.addWidget(self.plugins_button)
            header_layout.addWidget(self.open_button)
            header_layout.addWidget(self.lyrics_button)
            header_layout.addWidget(self.recent_button)
            header_layout.addWidget(self.playlist_button)
            self.plugin_buttons_layout = QHBoxLayout()
            self.plugin_buttons_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.addLayout(self.plugin_buttons_layout)
            header_layout.addWidget(self.settings_button)

            layout = QVBoxLayout()
            layout.setContentsMargins(18, 14, 18, 14)
            layout.setSpacing(10)

            self.lyric_display_stack = QStackedWidget()
            self.lyric_display_stack.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground,
                True,
            )
            self.lyric_display_stack.setAutoFillBackground(False)
            self.lyric_display_stack.setStyleSheet(
                "QStackedWidget { background: transparent; }"
            )

            # The lyrics now sit over a dedicated artwork background instead
            # of the old empty grey area.
            self.lyrics_background = ArtworkBackgroundWidget(defer_hidden_rendering=True)
            self.lyrics_background.setMinimumHeight(250)
            self.lyrics_background.set_content(self.lyric_display_stack)
            self._apply_appearance_settings()

            # Apply all configured lyric defaults now that the display stack exists.
            self.refresh_lyrics_settings_for_current_file()

            # The application has two main workspaces. Library is the default;
            # lyrics remain a dedicated toggleable view.
            self.workspace_stack = QStackedWidget()

            self.library_page = self._build_library_page()
            self.lyrics_page = QWidget()
            lyrics_page_layout = QVBoxLayout(self.lyrics_page)
            lyrics_page_layout.setContentsMargins(0, 0, 0, 0)
            lyrics_page_layout.setSpacing(8)
            lyrics_page_layout.addWidget(self.lyrics_background, 1)
            lyrics_page_layout.addWidget(self.furigana_progress_bar, 0)
            lyrics_page_layout.addWidget(self.lyrics_list_toggle, 0)
            lyrics_page_layout.addWidget(self.lyrics_list_widget, 0)

            self.workspace_stack.addWidget(self.library_page)
            self.workspace_stack.addWidget(self.lyrics_page)
            self.workspace_stack.setCurrentWidget(self.library_page)
            self.workspace_stack.currentChanged.connect(self._handle_workspace_changed)
            self._handle_workspace_changed(self.workspace_stack.currentIndex())
            self._populate_library_view()

            layout.addWidget(header, 0)
            layout.addWidget(self.workspace_stack, 1)
            layout.addWidget(self.waveform_widget, 0)
            layout.addWidget(player_dock, 0)

            container = QWidget()
            container.setObjectName("mainSurface")
            container.setLayout(layout)
            self.setCentralWidget(container)
            self._apply_main_window_style()

            self._furigana_prefetch_id = 0
            self._furigana_progress_bridge = _FuriganaProgressBridge(self)
            self._furigana_progress_bridge.progress.connect(
                self._set_furigana_progress
            )
            self._furigana_progress_bridge.finished.connect(
                self._furigana_prefetch_finished
            )

            # --------------------------------------------------
            # AUDIO SIGNALS
            # --------------------------------------------------

            self.media_player.positionChanged.connect(
                self.position_changed
            )

            self.media_player.durationChanged.connect(
                self.duration_changed
            )

            self.media_player.playbackStateChanged.connect(
                self.update_play_button
            )
            self.media_player.playbackStateChanged.connect(
                self._emit_plugin_playback_state
            )

            self.media_player.mediaStatusChanged.connect(
                self.handle_media_status
            )

            self._configure_pcm_volume(self.last_volume)
            self._watch_audio_devices()

            self.shortcut_play_pause = QShortcut(
                QKeySequence("Space"),
                self,
            )
            self.shortcut_play_pause.activated.connect(
                self.toggle_playback
            )

            self.shortcut_rewind = QShortcut(
                QKeySequence("Left"),
                self,
            )
            self.shortcut_rewind.activated.connect(
                lambda: self.skip_seconds(-5)
            )

            self.shortcut_forward = QShortcut(
                QKeySequence("Right"),
                self,
            )
            self.shortcut_forward.activated.connect(
                lambda: self.skip_seconds(5)
            )

            self.shortcut_mute = QShortcut(
                QKeySequence("Ctrl+M"),
                self,
            )
            self.shortcut_mute.activated.connect(
                self.toggle_mute
            )

            self.shortcut_library = QShortcut(
                QKeySequence("Ctrl+1"),
                self,
            )
            self.shortcut_library.activated.connect(
                lambda: self.workspace_stack.setCurrentWidget(self.library_page)
            )

            self.shortcut_lyrics = QShortcut(
                QKeySequence("Ctrl+2"),
                self,
            )
            self.shortcut_lyrics.activated.connect(
                lambda: self.workspace_stack.setCurrentWidget(self.lyrics_page)
            )

            self.easter_eggs = EasterEggs(self)
            self._restore_startup_state()
            self.update_transport_buttons()


        @property
        def lyric_widget(self):
            self._ensure_lyrics_renderers()
            return self._lyric_widget

        @property
        def scrolling_lyrics_widget(self):
            self._ensure_lyrics_renderers()
            return self._scrolling_lyrics_widget

        def _ensure_lyrics_renderers(self):
            """Create renderers when lyrics, a plugin, or the workspace needs them."""
            if self._lyrics_renderers_ready:
                return
            self._lyric_widget = FuriganaWidget()
            self._lyric_widget.setMinimumHeight(250)
            self._scrolling_lyrics_widget = ScrollingLyricsWidget(
                self._lyric_widget.manual_overrides,
                self.lyrics_direction, self.lyrics_padding, self,
            )
            for widget in (self._lyric_widget, self._scrolling_lyrics_widget):
                widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                widget.setAutoFillBackground(False)
                self.lyric_display_stack.addWidget(widget)
                apply_karaoke_settings(widget, self.karaoke_settings)
            self._lyric_widget.setStyleSheet("background: transparent; border: none;")
            self._scrolling_lyrics_widget.setStyleSheet(
                "background: transparent; border: none;"
                "QScrollArea { background: transparent; border: none; }"
                "QScrollArea::viewport { background: transparent; }"
                "QScrollArea > QWidget > QWidget { background: transparent; }"
            )
            self._lyrics_renderers_ready = True
            self.apply_lyric_display_mode()
            self.lyrics_renderers_created.emit()


        def _seek_from_waveform(self, position_ms):
            if self.media_player.duration() <= 0:
                return
            position_ms = max(0, min(int(position_ms), self.media_player.duration()))
            self._flush_pcm_output()
            self.media_player.setPosition(position_ms)
            self.seek_slider.setValue(position_ms)

        # --------------------------------------------------
        # WINDOW APPEARANCE
        # --------------------------------------------------
        def _apply_main_window_style(self):
            palette = build_theme_palette(
                self.appearance_settings.get("theme_mode", "light"),
                self.appearance_settings.get("theme_color", "#3a9879"),
                self.appearance_settings.get("theme_surface", "classic"),
                self.appearance_settings.get("theme_overrides", {}),
            )
            style = """
                QMainWindow#lrcPlusWindow,
                QWidget#mainSurface {
                    background: @WINDOW@;
                    color: @TEXT@;
                }
                QWidget#mainHeader {
                    background: @HEADER@;
                    border-bottom: 1px solid @BORDER@;
                }
                QLabel#brandLabel {
                    color: @BRAND@;
                    font-size: 18px;
                    font-weight: 700;
                }
                QPushButton#workspaceButton {
                    color: @MUTED@;
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 6px;
                    padding: 7px 13px;
                    font-weight: 600;
                }
                QPushButton#workspaceButton:hover {
                    background: @RAISED@;
                }
                QPushButton#workspaceButton:checked {
                    color: @TEXT@;
                    background: @ACCENT_SOFT@;
                    border-color: @ACCENT_BORDER@;
                }
                QPushButton#headerAction,
                QToolButton#headerAction {
                    color: @HEADER_BUTTON_TEXT@;
                    background: @SURFACE@;
                    border: 1px solid @FIELD_BORDER@;
                    border-radius: 6px;
                    padding: 6px 10px;
                }
                QPushButton#headerAction:hover,
                QToolButton#headerAction:hover {
                    background: @RAISED_HOVER@;
                    border-color: @ACCENT_BORDER@;
                }
                QPushButton#headerAction:pressed,
                QToolButton#headerAction:pressed {
                    background: @PRESSED@;
                }
                QLineEdit#librarySearch {
                    color: @TEXT@;
                    background: @SURFACE@;
                    border: 1px solid @FIELD_BORDER@;
                    border-radius: 6px;
                    padding: 8px 10px;
                    selection-background-color: @ACCENT@;
                }
                QComboBox#librarySearchFilter {
                    color: @TEXT@;
                    background: @SURFACE@;
                    border: 1px solid @FIELD_BORDER@;
                    border-radius: 6px;
                    padding: 7px 10px;
                    min-height: 18px;
                }
                QComboBox#librarySearchFilter:hover {
                    border-color: @ACCENT_BORDER@;
                }
                QComboBox#librarySearchFilter::drop-down {
                    border: none;
                    width: 22px;
                }
                QComboBox#librarySearchFilter::down-arrow {
                    image: none;
                    width: 0;
                    height: 0;
                }
                QComboBox#librarySearchFilter QAbstractItemView {
                    color: @TEXT@;
                    background: @SURFACE@;
                    border: 1px solid @FIELD_BORDER@;
                    selection-color: @TEXT@;
                    selection-background-color: @ACCENT_SOFT@;
                    outline: none;
                }
                QLabel#libraryFolderLabel {
                    color: @TEXT@;
                }
                QTreeWidget#libraryTable {
                    color: @TEXT@;
                    background: @SURFACE@;
                    alternate-background-color: @SURFACE_ALT@;
                    selection-color: @SELECTION_TEXT@;
                    selection-background-color: @SELECTION@;
                    border: 1px solid @BORDER@;
                    border-radius: 6px;
                    outline: none;
                }
                QTreeWidget#libraryTable::item {
                    padding: 4px 3px;
                }
                QTreeWidget#libraryTable::item:hover {
                    background-color: @ITEM_HOVER@;
                }
                QTreeWidget#libraryTable::item:selected {
                    color: @SELECTION_TEXT@;
                    background-color: @SELECTION@;
                }
                QTreeWidget#libraryTable QHeaderView::section {
                    color: @MUTED@;
                    background: @RAISED@;
                    border: none;
                    border-right: 1px solid @BORDER@;
                    border-bottom: 1px solid @BORDER@;
                    padding: 7px 6px;
                    font-weight: 600;
                }
                QWidget#playerDock {
                    background: @DOCK@;
                    border: 1px solid @DOCK_BORDER@;
                    border-radius: 8px;
                }
                QWidget#playerDock QLabel {
                    color: @DOCK_TEXT@;
                    background: transparent;
                }
                QPushButton#dockAction {
                    color: @DOCK_TEXT@;
                    background: @DOCK_CONTROL@;
                    border: 1px solid @DOCK_CONTROL_BORDER@;
                    border-radius: 6px;
                    padding: 5px 9px;
                    min-height: 28px;
                }
                QComboBox#playbackSourceCombo {
                    color: @DOCK_TEXT@;
                    background: @DOCK_CONTROL@;
                    border: 1px solid @DOCK_CONTROL_BORDER@;
                    border-radius: 6px;
                    padding: 4px 30px 4px 8px;
                    min-height: 28px;
                }
                QComboBox#playbackSourceCombo:hover {
                    background: @DOCK_CONTROL_HOVER@;
                }
                QComboBox#playbackSourceCombo::drop-down {
                    border: none;
                    width: 24px;
                }
                QComboBox#playbackSourceCombo::down-arrow {
                    image: none;
                    width: 0;
                    height: 0;
                }
                QComboBox#playbackSourceCombo QToolButton#comboDropIndicator {
                    color: @DOCK_TEXT@;
                    background: @DOCK_CONTROL@;
                    border: none;
                    border-left: 1px solid @DOCK_CONTROL_BORDER@;
                    border-top-right-radius: 5px;
                    border-bottom-right-radius: 5px;
                    padding: 0;
                }
                QComboBox#playbackSourceCombo QToolButton#comboDropIndicator:hover {
                    background: @DOCK_CONTROL_HOVER@;
                }
                QComboBox#librarySearchFilter QToolButton#comboDropIndicator {
                    color: @TEXT@;
                    background: @SURFACE@;
                    border: none;
                    padding: 0;
                }
                QComboBox#librarySearchFilter QToolButton#comboDropIndicator:hover {
                    background: @RAISED_HOVER@;
                }
                QPushButton#dockAction:hover {
                    background: @DOCK_CONTROL_HOVER@;
                }
                QLabel#timeLabel {
                    color: @DOCK_TEXT@;
                    font-weight: 600;
                }
                QPushButton#transportButton {
                    color: @DOCK_TEXT@;
                    background: @DOCK_CONTROL@;
                    border: 1px solid @DOCK_CONTROL_BORDER@;
                    border-radius: 6px;
                    padding: 5px 8px;
                    font-weight: 600;
                }
                QPushButton#transportButton:hover:enabled {
                    background: @DOCK_CONTROL_HOVER@;
                }
                QPushButton#transportButton:disabled {
                    color: @DOCK_MUTED@;
                    background: @DOCK_DISABLED@;
                    border-color: @DOCK_BORDER@;
                }
                QPushButton#primaryTransportButton {
                    color: @ACCENT_TEXT@;
                    background: @ACCENT@;
                    border: 1px solid @ACCENT_BORDER@;
                    border-radius: 6px;
                    padding: 5px 8px;
                    font-weight: 700;
                }
                QPushButton#primaryTransportButton:hover:enabled {
                    background: @ACCENT_HOVER@;
                }
                QSlider#seekSlider::groove:horizontal {
                    height: 4px;
                    background: @SEEK_TRACK@;
                    border-radius: 2px;
                }
                QSlider#seekSlider::sub-page:horizontal {
                    background: @ACCENT@;
                    border-radius: 2px;
                }
                QSlider#seekSlider::handle:horizontal {
                    width: 12px;
                    margin: -5px 0;
                    background: @SLIDER_HANDLE@;
                    border: 2px solid @ACCENT@;
                    border-radius: 7px;
                }
                QSlider#volumeSlider::groove:horizontal {
                    height: 4px;
                    background: @SEEK_TRACK@;
                    border-radius: 2px;
                }
                QSlider#volumeSlider::sub-page:horizontal {
                    background: @ACCENT@;
                    border-radius: 2px;
                }
                QSlider#volumeSlider::add-page:horizontal {
                    background: @SEEK_TRACK@;
                    border-radius: 2px;
                }
                QSlider#volumeSlider::handle:horizontal {
                    width: 12px;
                    margin: -5px 0;
                    background: @SLIDER_HANDLE@;
                    border: 2px solid @ACCENT@;
                    border-radius: 7px;
                }
            """
            replacements = {
                "@ACCENT@": "accent",
                "@ACCENT_HOVER@": "accent_hover",
                "@ACCENT_BORDER@": "accent_border",
                "@ACCENT_SOFT@": "accent_soft",
                "@SELECTION@": "selection",
                "@SELECTION_TEXT@": "selection_text",
                "@ITEM_HOVER@": "item_hover",
                "@ACCENT_TEXT@": "accent_text",
                "@WINDOW@": "window",
                "@HEADER@": "header",
                "@TEXT@": "text",
                "@BRAND@": "brand",
                "@MUTED@": "muted",
                "@BORDER@": "border",
                "@FIELD_BORDER@": "field_border",
                "@HEADER_BUTTON_TEXT@": "header_button_text",
                "@SURFACE@": "surface",
                "@SURFACE_ALT@": "surface_alt",
                "@RAISED@": "raised",
                "@RAISED_HOVER@": "raised_hover",
                "@PRESSED@": "pressed",
                "@DOCK@": "dock",
                "@DOCK_BORDER@": "dock_border",
                "@DOCK_CONTROL@": "dock_control",
                "@DOCK_CONTROL_HOVER@": "dock_control_hover",
                "@DOCK_CONTROL_BORDER@": "dock_control_border",
                "@DOCK_DISABLED@": "dock_disabled",
                "@DOCK_TEXT@": "dock_text",
                "@DOCK_MUTED@": "dock_muted",
                "@SEEK_TRACK@": "seek_track",
                "@SLIDER_HANDLE@": "slider_handle",
            }
            for marker, key in replacements.items():
                style = style.replace(marker, palette[key])
            self.setStyleSheet(style)


        def _restore_startup_state(self):
            payload = load_all_settings()

            if self.startup_settings.get("remember_window", True):
                window_state = payload.get("window_state", {})
                if isinstance(window_state, dict):
                    geometry = window_state.get("geometry", {})
                    if isinstance(geometry, dict):
                        try:
                            width = int(geometry.get("width", self.width()))
                            height = int(geometry.get("height", self.height()))
                            x = int(geometry.get("x", self.x()))
                            y = int(geometry.get("y", self.y()))
                            self.resize(max(300, width), max(200, height))
                            self.move(x, y)
                        except (TypeError, ValueError):
                            pass

                    if bool(window_state.get("maximized", False)):
                        self.showMaximized()

            if self.startup_settings.get("restore_playlist", False):
                playlist_state = payload.get("playlist_state", {})
                if not isinstance(playlist_state, dict):
                    return

                paths = playlist_state.get("paths", [])
                if not isinstance(paths, list):
                    return

                try:
                    index = int(playlist_state.get("current_index", 0))
                except (TypeError, ValueError):
                    index = 0

                restored = []
                original_indices = []
                for original, item in enumerate(paths):
                    if not isinstance(item, str):
                        continue
                    try:
                        path = Path(item)
                        if path.is_file():
                            restored.append(path)
                            original_indices.append(original)
                    except (OSError, ValueError):
                        continue
                if not restored:
                    return

                self.playlist = restored
                # Missing entries before the active occurrence must not shift
                # restoration to a different song (or a different repeat).
                self.current_index = next((i for i, original in enumerate(original_indices)
                                           if original >= index), len(restored) - 1)
                self.load_audio(str(self.playlist[self.current_index]))


        def _save_startup_state(self):
            payload = load_all_settings()

            if self.startup_settings.get("remember_window", True):
                normal_geometry = self.normalGeometry()
                if not normal_geometry.isValid():
                    normal_geometry = self.geometry()

                payload["window_state"] = {
                    "geometry": {
                        "x": normal_geometry.x(),
                        "y": normal_geometry.y(),
                        "width": normal_geometry.width(),
                        "height": normal_geometry.height(),
                    },
                    "maximized": self.isMaximized(),
                }
            else:
                payload.pop("window_state", None)

            if self.startup_settings.get("restore_playlist", False):
                payload["playlist_state"] = {
                    "paths": [str(path) for path in self.playlist],
                    "current_index": self.current_index,
                }
            else:
                payload.pop("playlist_state", None)

            save_all_settings(payload)


        def _handle_workspace_changed(self, index):
            """Resume visible-page work when switching workspaces."""
            if not hasattr(self, "workspace_stack"):
                return
            current_page = self.workspace_stack.widget(index)
            self.library_button.setChecked(current_page is self.library_page)
            self.lyrics_view_button.setChecked(current_page is self.lyrics_page)
            if current_page is self.library_page:
                self._queue_visible_library_covers()
            if current_page is not self.lyrics_page:
                return

            self._ensure_lyrics_renderers()
            QTimer.singleShot(0, self._refresh_lyrics_after_page_show)
            QTimer.singleShot(1, self._refresh_lyrics_after_page_show)

        def _refresh_lyrics_after_page_show(self):
            if not hasattr(self, "lyrics_page") or not self.lyrics_page.isVisible():
                return

            self.lyrics_page.updateGeometry()

            display_stack = getattr(self, "lyric_display_stack", None)
            if display_stack is not None:
                display_stack.updateGeometry()

            renderer = getattr(self, "scrolling_lyrics_widget", None)
            if renderer is not None:
                source_lines = getattr(self, "lyric_lines", None) or []

                if source_lines and (
                    not getattr(renderer, "rows", None)
                    or renderer.lyric_count != len(source_lines)
                ):
                    renderer.set_lyrics(source_lines)

                if hasattr(renderer, "refresh_layout_now"):
                    renderer.refresh_layout_now()

                renderer.updateGeometry()
                renderer.update()

            lyric_widget = getattr(self, "lyric_widget", None)
            if lyric_widget is not None:
                lyric_widget.updateGeometry()
                lyric_widget.update()

            if display_stack is not None:
                display_stack.update()

            self.lyrics_page.update()

        def toggle_lyrics_list(self):
            self.lyrics_list_collapsed = (
                not self.lyrics_list_collapsed
            )

            self.lyrics_list_widget.setVisible(
                not self.lyrics_list_collapsed
            )

            settings = load_all_settings()
            settings["lyrics_list_collapsed"] = self.lyrics_list_collapsed
            save_all_settings(settings)

            if self.lyrics_list_collapsed:
                self.lyrics_list_toggle.setText(
                    "Lyrics [+]"
                )
            else:
                self.lyrics_list_toggle.setText(
                    "Lyrics [-]"
                )


        def _apply_appearance_settings(self):
            if not hasattr(self, "lyrics_background"):
                return

            settings = self.appearance_settings
            self.lyrics_background.set_background_enabled(
                settings.get("background_enabled", True)
            )
            self.lyrics_background.set_background_layout(
                settings.get("background_layout", "fit")
            )
            self.lyrics_background.set_blur_mode(
                settings.get("background_blur_mode", "quality")
            )
            self.lyrics_background.set_blur_bleed(
                settings.get("background_blur_bleed", False)
            )
            self.lyrics_background.set_blur_radius(
                settings.get("background_blur", 12)
            )
            self.lyrics_background.set_darkness(
                settings.get("background_darkness", 155)
            )


        def _invalidate_booru_request(self):
            self._booru_request_id += 1
            if self._booru_future is not None:
                self._booru_future.cancel()
                self._booru_future = None

        def _start_booru_fetch(self, audio_path, fallback_data=None):
            """Fetch Booru artwork without blocking song loading."""
            self._invalidate_booru_request()
            request_id = self._booru_request_id
            tags = self.appearance_settings.get("booru_tags", "scenery")

            print(f"[Background] Fetching Booru artwork: tags={tags!r}")
            future = self._booru_executor.submit(
                fetch_booru_artwork,
                tags,
            )
            self._booru_future = future

            def check_result():
                # Stop polling and release fallback images as soon as a newer
                # song supersedes this request, even if its download is active.
                if request_id != self._booru_request_id:
                    return
                if not future.done():
                    QTimer.singleShot(50, check_result)
                    return

                self._booru_future = None

                try:
                    data, info = future.result()
                except Exception as error:
                    print(f"[Background] Booru fetch failed: {error!r}")
                    data, info = None, None

                if data and self.lyrics_background.set_artwork_bytes(data):
                    post_id = (info or {}).get("post_id", "?")
                    print(
                        f"[Background] Using Booru artwork "
                        f"(post {post_id}) for: {Path(audio_path).name}"
                    )
                    return

                if fallback_data and self.lyrics_background.set_artwork_bytes(
                    fallback_data
                ):
                    print(
                        f"[Background] Booru failed; using embedded artwork "
                        f"for: {Path(audio_path).name}"
                    )
                else:
                    print(
                        f"[Background] No Booru or embedded artwork for: "
                        f"{Path(audio_path).name}"
                    )
                    self.lyrics_background.clear_artwork()

            QTimer.singleShot(0, check_result)

        def update_background_for_audio(self, audio_path):
            """Choose embedded or Booru artwork according to Appearance."""
            self._invalidate_booru_request()
            source = self.appearance_settings.get(
                "artwork_source",
                "album_first",
            )
            if source == "custom":
                folders = tuple(
                    self.appearance_settings.get("custom_background_folders", [])
                )

                # Rebuild only when the configured folders change.
                if getattr(self, "_custom_artwork_folders", None) != folders:
                    supported_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
                    image_paths = []

                    for folder in folders:
                        try:
                            image_paths.extend(
                                path
                                for path in Path(folder).expanduser().rglob("*")
                                if path.is_file()
                                and path.suffix.lower() in supported_extensions
                            )
                        except (OSError, ValueError) as error:
                            print(f"[Background] Scan failed: {error!r}")

                    self._custom_background_cache = image_paths
                    self._custom_artwork_folders = folders

                image_paths = self._custom_background_cache

                if not image_paths:
                    self.lyrics_background.clear_artwork()
                    return

                custom_path = random.choice(image_paths)

                try:
                    image_data = custom_path.read_bytes()
                    if not self.lyrics_background.set_artwork_bytes(image_data):
                        self.lyrics_background.clear_artwork()
                except (OSError, ValueError) as error:
                    print(f"[Background] Image read failed: {error!r}")
                    if custom_path in image_paths:
                        image_paths.remove(custom_path)
                    self.lyrics_background.clear_artwork()

                return

            embedded = None
            if source != "booru_only":
                embedded = extract_embedded_cover(audio_path)

            if source == "album_only":
                if embedded and self.lyrics_background.set_artwork_bytes(embedded):
                    print(
                        f"[Background] Using embedded artwork for: "
                        f"{Path(audio_path).name}"
                    )
                else:
                    print(
                        f"[Background] No embedded artwork for: "
                        f"{Path(audio_path).name}"
                    )
                    self.lyrics_background.clear_artwork()
                return

            if source == "album_first" and embedded:
                if self.lyrics_background.set_artwork_bytes(embedded):
                    print(
                        f"[Background] Using embedded artwork for: "
                        f"{Path(audio_path).name}"
                    )
                    return

            fallback = embedded if source in {"booru_first", "album_first"} else None
            self._start_booru_fetch(audio_path, fallback)


        def _emit_plugin_playback_state(self, state):
            if self.plugin_manager is not None:
                self.plugin_manager.emit(
                    "playback_state_changed",
                    state=state.name if hasattr(state, "name") else str(state),
                )


        def open_audio(self):
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                "Open Audio",
                "",
                "Audio Files (*.mp3 *.wav *.flac *.ogg *.m4a *.aac);;"
                "All Files (*)",
            )

            if not paths:
                return

            self._clear_shuffle_history()
            self.playlist = [Path(path) for path in paths]
            self.current_index = 0
            self._priority_queue_indices = []
            self._register_queue_paths(self.playlist)
            self.load_audio(str(self.playlist[self.current_index]))


        def open_lrc(self):
            if not self.current_audio_path:
                directory = ""
            else:
                directory = str(self.current_audio_path.parent)

            path, _ = QFileDialog.getOpenFileName(
                self,
                "Open Lyrics",
                directory,
                "Lyrics Files (*.lrc *.lrcx *.txt);;LRC Files (*.lrc);;LRCX Files (*.lrcx);;Text Files (*.txt);;All Files (*)",
            )

            if not path:
                return

            self.load_lrc_file(path)
            self.current_lyrics_path = Path(path)


        def open_settings(self):
            local_overrides = get_local_lyrics_overrides(
                self.current_audio_path
            )

            dialog = SettingsDialog(
                self.lrc_search_paths,
                self.global_lyrics_settings,
                self.startup_settings,
                self.appearance_settings,
                self.playback_settings,
                self.karaoke_settings,
                local_overrides,
                str(self.current_audio_path)
                if self.current_audio_path
                else None,
                self.plugin_manager,
                self,
                update_settings=self.update_settings,
            )

            def check_updates_after_settings_close():
                dialog.accept()
                QTimer.singleShot(0, lambda: self.check_for_updates(manual=True))

            dialog.check_updates_requested.connect(check_updates_after_settings_close)

            def relaunch_wizard():
                dialog.accept()
                def open_wizard():
                    from ui.onboarding import OnboardingDialog
                    OnboardingDialog(self).run()
                QTimer.singleShot(0, open_wizard)

            dialog.relaunch_wizard_requested.connect(relaunch_wizard)

            try:
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    previous_artwork_source = self.appearance_settings.get(
                        "artwork_source", "album_first"
                    )
                    previous_custom_folders = self.appearance_settings.get(
                        "custom_background_folders", []
                    )
                    self.startup_settings = dialog.get_startup_settings()
                    save_startup_settings(self.startup_settings)

                    self.update_settings = dialog.get_update_settings()
                    save_update_settings(self.update_settings)

                    self.appearance_settings = dialog.get_appearance_settings()
                    save_appearance_settings(self.appearance_settings)
                    self._apply_main_window_style()
                    self._apply_appearance_settings()
                    if self.current_audio_path and (
                        previous_artwork_source
                        != self.appearance_settings.get(
                            "artwork_source", "album_first"
                        )
                        or previous_custom_folders
                        != self.appearance_settings.get(
                            "custom_background_folders", []
                        )
                    ):
                        self.update_background_for_audio(
                            self.current_audio_path
                        )

                    self.playback_settings = dialog.get_playback_settings()
                    self.shuffle_enabled = self.playback_settings.get("shuffle_enabled", False)
                    self._clear_shuffle_history()
                    save_playback_settings(self.playback_settings)
                    self.waveform_widget.setVisible(
                        self.playback_settings.get("show_waveform", True)
                        and find_ffmpeg() is not None
                    )
                    self._apply_output_device(self.playback_settings)
                    self._load_replaygain()
                    self._invalidate_gapless_preload()
                    self._update_shuffle_button_text()
                    self._update_playback_source_control()
                    self.update_transport_buttons()

                    self.karaoke_settings = dialog.get_karaoke_settings()
                    save_karaoke_settings(self.karaoke_settings)
                    if self._lyrics_renderers_ready:
                        for widget in (self.lyric_widget, self.scrolling_lyrics_widget):
                            apply_karaoke_settings(widget, self.karaoke_settings)
                    if self.plugin_manager is not None:
                        self.plugin_manager.emit("karaoke_settings_changed")

                    self.lrc_search_paths = dialog.get_folders()

                    self.global_lyrics_settings = (
                        dialog.get_global_lyrics_settings()
                    )
                    save_global_lyrics_settings(
                        self.global_lyrics_settings
                    )

                    if self.current_audio_path:
                        save_local_lyrics_overrides(
                            self.current_audio_path,
                            dialog.get_local_lyrics_overrides(),
                        )

                    # Folder settings remain outside the lyric scope system.
                    payload = load_all_settings()
                    payload["lrc_folders"] = list(
                        self.lrc_search_paths
                    )
                    save_all_settings(payload)

                    dialog.apply_plugin_settings()

                    if self.plugin_manager is not None:
                        disabled_plugins = dialog.get_disabled_plugins()
                        self.plugin_manager.disabled_plugins = set(disabled_plugins)
                        save_disabled_plugins(disabled_plugins)

                    self.refresh_lyrics_settings_for_current_file()
            finally:
                # Modal dialogs are hidden on close; their parent otherwise
                # retains the complete settings widget tree on every opening.
                dialog.deleteLater()


        def get_current_lyrics_path(self):
            """Return the active lyrics file, or a default .lrc path."""
            if self.current_lyrics_path:
                return Path(self.current_lyrics_path)

            if self.current_audio_path:
                return Path(self.current_audio_path).with_suffix(".lrc")

            return None


        def edit_current_lyrics(self):
            lyrics_path = self.get_current_lyrics_path()

            # Opening the editor without an existing lyrics file creates an
            # in-memory draft target only. Do not create the directory/file
            # or mark anything dirty merely because the editor was opened.
            if lyrics_path is None and self.current_audio_path:
                lyrics_path = Path(self.current_audio_path).with_suffix(".lrc")

            if lyrics_path is None:
                QMessageBox.information(
                    self,
                    "Edit Lyrics",
                    "Open an audio or lyrics file first.",
                )
                return

            dialog = LrcEditorDialog(lyrics_path, self)
            try:
                # Association edits refresh through save_document; loading the
                # editor's old file here would undo a newly selected target.
                if (dialog.exec() == QDialog.DialogCode.Accepted
                        and getattr(dialog, "_association_changed_song", None) is None
                        and dialog._is_editing_current_song()):
                    saved_path = getattr(dialog, "result_path", lyrics_path)
                    self.load_lrc_file(str(saved_path))
                    self.current_lyrics_path = Path(saved_path)
            finally:
                dialog.deleteLater()


        def save_lyrics_as(self):
            source_path = self.get_current_lyrics_path()

            if source_path is None or not source_path.exists():
                QMessageBox.information(
                    self,
                    "Save Lyrics As",
                    "There is no lyrics file to save.",
                )
                return

            default_path = str(source_path)

            destination, selected_filter = QFileDialog.getSaveFileName(
                self,
                "Save Lyrics As",
                default_path,
                "LRC Files (*.lrc);;LRCX Files (*.lrcx);;All Files (*)",
            )

            if not destination:
                return

            destination_path = Path(destination)

            if not destination_path.suffix:
                if "LRCX" in selected_filter:
                    destination_path = destination_path.with_suffix(
                        ".lrcx"
                    )
                else:
                    destination_path = destination_path.with_suffix(
                        ".lrc"
                    )

            try:
                convert_lyrics(
                    source_path,
                    destination_path,
                    manual_overrides=self.lyric_widget.manual_overrides,
                )
            except (OSError, UnicodeError, ValueError) as error:
                QMessageBox.critical(
                    self,
                    "Save Lyrics As",
                    f"Could not save lyrics:\n{error}",
                )
                return

            self.current_lyrics_path = destination_path
            self.load_lrc_file(str(destination_path))


        def show_recent_songs(self):
            """Show dialog with recently played songs."""
            recent = get_recent_songs()
            if not recent:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self, "Recent Songs", "No recent songs yet.")
                return

            dialog = QDialog(self)
            dialog.setWindowTitle("Recent Songs")
            dialog.resize(500, 300)

            list_widget = QListWidget()
            from ui.content_list import configure_content_list
            configure_content_list(list_widget)
            # Filenames share a row height. Avoid measuring hidden titles,
            # which can eagerly load large fallback fonts on Windows.
            list_widget.setUniformItemSizes(True)
            for song in recent:
                audio_path = Path(song.get("audio", ""))
                if audio_path.exists():
                    display_text = audio_path.name
                    timestamp = song.get("timestamp", "")
                    if timestamp:
                        from datetime import datetime as dt
                        try:
                            dt_obj = dt.fromisoformat(timestamp)
                            time_str = dt_obj.strftime("%Y-%m-%d %H:%M")
                            display_text = f"{audio_path.name} ({time_str})"
                        except:
                            pass
                    item = QListWidgetItem(display_text)
                    item.setData(Qt.ItemDataRole.UserRole, song.get("audio"))
                    list_widget.addItem(item)

            def load_selected():
                if not list_widget.currentItem():
                    return
                audio_path = list_widget.currentItem().data(Qt.ItemDataRole.UserRole)
                if audio_path and Path(audio_path).exists():
                    self._clear_shuffle_history()
                    self.load_audio(audio_path)
                    dialog.accept()

            button_layout = QHBoxLayout()
            load_button = QPushButton("Load")
            cancel_button = QPushButton("Cancel")
            load_button.clicked.connect(load_selected)
            cancel_button.clicked.connect(dialog.reject)
            button_layout.addWidget(load_button)
            button_layout.addWidget(cancel_button)

            layout = QVBoxLayout()
            layout.addWidget(QLabel("Recently played:"))
            layout.addWidget(list_widget)
            layout.addLayout(button_layout)
            dialog.setLayout(layout)
            try:
                dialog.exec()
            finally:
                # Closing a parented dialog only hides it; release its widgets
                # and signal callbacks before the next Recent opening.
                dialog.deleteLater()


        def showEvent(self, event):
            super().showEvent(event)
            # Initial construction happens before the library is visible.
            self._queue_visible_library_covers()

        def closeEvent(self, event):
            check_worker = self._update_check_worker
            download_worker = self._update_download_worker
            if ((check_worker is not None and check_worker.isRunning())
                    or (download_worker is not None and download_worker.isRunning())):
                event.ignore()
                if not self._close_after_update_cancel:
                    self._close_after_update_cancel = True
                    if download_worker is not None and download_worker.isRunning() and not getattr(
                        self, "_update_handoff_started", False
                    ):
                        download_worker.cancel()
                return
            self._library_search_timer.stop()
            self._library_lyrics_cancel.set()
            self._library_lyrics_job.close()
            # Save the full player's geometry even when closing the budget mode.
            self.easter_eggs.return_to_plus()
            if getattr(self, "_lyrics_search_job", None) is not None:
                self._lyrics_search_job.close()
            if hasattr(self, "_booru_executor"):
                self._invalidate_booru_request()
                self._booru_executor.shutdown(wait=False, cancel_futures=True)
            self._save_startup_state()

            if self._lyrics_renderers_ready:
                self._lyric_widget.stop_workers()
            self.waveform_widget._stop_worker()

            self._close_audio_device_dialog()
            self.media_player.stop()
            self.playback_settings["volume"] = self.volume_slider.value()
            save_playback_settings(self.playback_settings)
            self.media_player.setAudioBufferOutput(None)
            self._stop_pcm_sink()
            self._pcm_worker.close()
            if getattr(self, "_replaygain_job", None) is not None:
                self._replaygain_job.close()
            self._playback_save_timer.stop()

            event.accept()

        def open_pitch_options(self):
            dialog = PitchChangerDialog(
                self.pitch_semitones,
                self.pitch_settings.get("mode", "Semitones"),
                self.handle_pitch_change,
                speed_percent=round(self.playback_speed * 100),
                link_pitch_to_speed=self.pitch_linked_to_speed,
                on_speed_change=self.handle_speed_change,
                on_link_change=self.handle_pitch_link_change,
                parent=self,
            )
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
