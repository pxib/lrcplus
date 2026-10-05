from pathlib import Path
import random
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import requests

from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QThread, QSize, QRect
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QPixmap, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QInputDialog, QMainWindow, QScrollArea, QStackedWidget, QLineEdit,
    QMessageBox, QPushButton, QProgressBar, QSlider, QTextEdit, QToolButton, QVBoxLayout, QWidget,
    QGraphicsView, QGraphicsScene,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QTreeWidget, QTreeWidgetItem, QAbstractItemView, QStyledItemDelegate,
)

from core.audio_controller import AudioController, snap_volume_percentage
from core.lyrics_controller import LyricsController, _FuriganaProgressBridge
from core.settings import (
    load_all_settings,
    save_all_settings,
    get_startup_settings,
    save_startup_settings,
    get_appearance_settings,
    save_appearance_settings,
    load_lrc_search_folders,
    get_global_lyrics_settings,
    get_playback_settings,
    save_playback_settings,
    get_karaoke_settings,
    save_karaoke_settings,
    get_local_lyrics_overrides,
    save_global_lyrics_settings,
    save_local_lyrics_overrides,
    save_lrc_search_folders,
    save_disabled_plugins,
    load_music_library,
    save_music_library,
)
from core.recent import get_recent_songs
from core.music_library import LibraryTrack, read_track, scan_folders
from lyrics.widgets import FuriganaWidget, ScrollingLyricsWidget
from lyrics.waveform import WaveformWidget, find_ffmpeg
from lyrics.writer import convert_lyrics
from ui.settings_dialog import SettingsDialog
from ui.lrc_editor import LrcEditorDialog
from ui.playlist_dialog import PlaylistDialog
from ui.artwork_background import ArtworkBackgroundWidget, extract_embedded_cover, load_embedded_cover_thumbnail
from core.booru_artwork import fetch_booru_artwork
from core.theme import build_theme_palette


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


class ClickableSlider(QSlider):
    """A slider that seeks immediately when its groove is clicked."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.orientation() == Qt.Orientation.Horizontal:
                span = self.width()
                position = event.position().x()
            else:
                span = self.height()
                position = self.height() - event.position().y()

            if span > 0:
                ratio = max(0.0, min(1.0, position / span))
                value = self.minimum() + ratio * (
                    self.maximum() - self.minimum()
                )
                self.setValue(round(value))
                self.sliderMoved.emit(self.value())

        super().mousePressEvent(event)


class AlbumCoverDelegate(QStyledItemDelegate):
    """Paint embedded artwork strictly inside the current cover-column bounds."""

    def paint(self, painter, option, index):
        cover = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(cover, QPixmap) and not cover.isNull():
            painter.save()
            # Keep a small margin and scale to whatever width the user gives this column.
            rect = option.rect.adjusted(4, 3, -4, -3)
            if rect.width() > 0 and rect.height() > 0:
                scaled = cover.scaled(
                    rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = rect.x() + (rect.width() - scaled.width()) // 2
                y = rect.y() + (rect.height() - scaled.height()) // 2
                painter.setClipRect(option.rect)
                painter.drawPixmap(x, y, scaled)
            painter.restore()
            return
        super().paint(painter, option, index)


class CoverPreviewView(QGraphicsView):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        self.image_item = scene.addPixmap(pixmap)
        self.setScene(scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def fit_to_view(self):
        self.resetTransform()
        self.fitInView(self.image_item, Qt.AspectRatioMode.KeepAspectRatio)

    def set_actual_size(self):
        self.resetTransform()
        self.centerOn(self.image_item)

    def zoom(self, factor):
        current_scale = self.transform().m11()
        if 0.05 <= current_scale * factor <= 8:
            self.scale(factor, factor)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.2 if delta > 0 else 1 / 1.2)
            event.accept()
            return
        super().wheelEvent(event)


class CoverPreviewDialog(QDialog):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Album Cover")
        self.resize(900, 700)

        self.view = CoverPreviewView(pixmap, self)
        controls = QHBoxLayout()
        zoom_out = QToolButton(self)
        zoom_out.setText("-")
        zoom_out.setToolTip("Zoom out")
        zoom_in = QToolButton(self)
        zoom_in.setText("+")
        zoom_in.setToolTip("Zoom in")
        fit = QPushButton("Fit", self)
        actual_size = QPushButton("100%", self)
        zoom_out.clicked.connect(lambda: self.view.zoom(1 / 1.2))
        zoom_in.clicked.connect(lambda: self.view.zoom(1.2))
        fit.clicked.connect(self.view.fit_to_view)
        actual_size.clicked.connect(self.view.set_actual_size)
        controls.addWidget(zoom_out)
        controls.addWidget(zoom_in)
        controls.addWidget(fit)
        controls.addWidget(actual_size)
        controls.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.view, 1)
        QTimer.singleShot(0, self.view.fit_to_view)


class LibraryScanWorker(QThread):
    completed = Signal(list)

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.folders = list(folders)

    def run(self):
        self.completed.emit(scan_folders(self.folders))


class Player(AudioController, LyricsController, QMainWindow):
        def __init__(self, plugin_manager=None):
            super().__init__()

            self.plugin_manager = plugin_manager
            self._booru_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="booru-artwork",
            )
            self._booru_future = None
            self._booru_request_id = 0

            self.setWindowTitle("LyricsPlus")
            self.resize(1180, 760)
            self.setObjectName("lyricsPlusWindow")

            self.startup_settings = get_startup_settings()
            self.appearance_settings = get_appearance_settings()

            # --------------------------------------------------
            # AUDIO
            # --------------------------------------------------

            self.media_player = QMediaPlayer(self)

            self.audio_output = QAudioOutput(self)
            self._pcm_buffer_output = None
            self._pcm_sink = None
            self._pcm_sink_io = None
            self._pcm_pending = bytearray()
            self._pcm_drain_timer = QTimer(self)
            self._pcm_drain_timer.setInterval(10)
            self._pcm_drain_timer.timeout.connect(self._drain_pcm_pending)

            self.media_player.setAudioOutput(
                self.audio_output
            )

            self.current_audio_path = None
            self.playlist = []
            self.current_index = -1
            self.lrc_search_paths = load_lrc_search_folders()
            # Persistent music library. Playback controls remain separate from
            # the library; the library is the primary workspace.
            self.music_library = load_music_library()
            self.library_folders = self.music_library["folders"]
            self.library_tracks = []
            self.library_column_state = self.music_library.get("columns", {}) or {}
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
            for raw in self.music_library["tracks"]:
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

            self.lyric_widget = FuriganaWidget()
            self.lyric_widget.setMinimumHeight(250)
            self.lyric_widget.set_direction(self.lyrics_direction)
            self.lyric_widget.set_ruby_visible(self.show_ruby)
            self.lyric_widget.set_romaji_visible(self.show_romaji)
            self.lyric_widget.set_all_romaji_visible(self.all_romaji)
            self.lyric_widget.set_ruby_position(self.ruby_position)
            self.lyric_widget.set_ruby_padding(self.ruby_padding)

            self.scrolling_lyrics_widget = ScrollingLyricsWidget(
                self.lyric_widget.manual_overrides,
                self.lyrics_direction,
                self.lyrics_padding,
                self,
            )

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

            self.current_time_label = QLabel("00:00")
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

            self.playback_speed = 1.0

            # --------------------------------------------------
            # SEEK BAR
            # --------------------------------------------------

            self.seek_slider = ClickableSlider(
                Qt.Orientation.Horizontal
            )
            self.seek_slider.setRange(0, 0)
            self.seek_slider.setFixedHeight(20)
            self.seek_slider.sliderPressed.connect(
                self.start_seeking
            )
            self.seek_slider.sliderReleased.connect(
                self.finish_seeking
            )
            
            # Interactive waveform overview. It mirrors the seek bar but also
            # shows lyric boundaries and supports direct seeking.
            self.waveform_widget = WaveformWidget()
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
            self.volume_slider.setRange(0, 300)
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

            # --------------------------------------------------
            # SPEED CONTROL
            # --------------------------------------------------

            self.speed_slider = QSlider(
                Qt.Orientation.Horizontal
            )
            self.speed_slider.setRange(25, 200)  # 0.5x to 2.0x
            self.speed_slider.setValue(100)  # 1.0x
            self.speed_slider.setFixedWidth(120)
            self.speed_slider.valueChanged.connect(
                self.handle_speed_change
            )

            self.speed_label = QLabel("100%")
            self.speed_label.setFixedWidth(40)


            self.shuffle_enabled = False
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
            self.lyrics_menu.addSeparator()
            search_lyrics_action = QAction("Search Lyrics", self)
            search_lyrics_action.triggered.connect(self.search_lrclib_lyrics)
            self.lyrics_menu.addAction(search_lyrics_action)
            self.lyrics_button.setMenu(self.lyrics_menu)
            self.lyrics_button.setMenu(self.lyrics_menu)

            self.recent_button = QPushButton(
                "Recent"
            )
            self.recent_button.clicked.connect(
                self.show_recent_songs
            )

            self.playlist_button = QPushButton(
                "Playlist"
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
            seek_layout.addWidget(self.seek_slider, 1)
            seek_layout.addWidget(self.duration_label)

            volume_layout = QHBoxLayout()
            volume_layout.setContentsMargins(0, 0, 0, 0)
            volume_layout.setSpacing(8)
            volume_layout.addWidget(self.shuffle_button)
            volume_layout.addSpacing(8)
            volume_layout.addWidget(QLabel("Speed"))
            volume_layout.addWidget(self.speed_slider)
            volume_layout.addWidget(self.speed_label)
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
            brand_label = QLabel("LyricsPlus")
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
            self.lyric_display_stack.addWidget(self.lyric_widget)
            self.lyric_display_stack.addWidget(self.scrolling_lyrics_widget)
            self.lyric_display_stack.setCurrentIndex(
                1 if self.scrolling_lyrics_enabled else 0
            )

            # The lyrics now sit over a dedicated artwork background instead
            # of the old empty grey area.
            self.lyrics_background = ArtworkBackgroundWidget()
            self.lyrics_background.setMinimumHeight(250)
            self.lyrics_background.set_content(self.lyric_display_stack)
            self._apply_appearance_settings()

            # Keep the display pages transparent so the artwork can show
            # through the lyric area.
            for display_widget in (
                self.lyric_widget,
                self.scrolling_lyrics_widget,
            ):
                display_widget.setAttribute(
                    Qt.WidgetAttribute.WA_TranslucentBackground,
                    True,
                )
                display_widget.setAutoFillBackground(False)

            self.lyric_widget.setStyleSheet(
                "background: transparent; border: none;"
            )
            self.scrolling_lyrics_widget.setStyleSheet(
                "background: transparent; border: none;"
                "QScrollArea { background: transparent; border: none; }"
                "QScrollArea::viewport { background: transparent; }"
                "QScrollArea > QWidget > QWidget { background: transparent; }"
            )

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

            self._restore_startup_state()


        def _seek_from_waveform(self, position_ms):
            if self.media_player.duration() <= 0:
                return
            position_ms = max(0, min(int(position_ms), self.media_player.duration()))
            self._flush_pcm_output()
            self.media_player.setPosition(position_ms)
            self.seek_slider.setValue(position_ms)

        # --------------------------------------------------
        # MUSIC LIBRARY
        # --------------------------------------------------

        def _build_library_page(self):
            page = QWidget()
            root = QVBoxLayout(page)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(6)

            toolbar = QHBoxLayout()
            self.library_search = QLineEdit()
            self.library_search.setObjectName("librarySearch")
            self.library_search.setPlaceholderText("Search library...")
            self.library_search.textChanged.connect(self._populate_library_view)
            add_folder = QPushButton("Add Music Folder")
            add_folder.setObjectName("headerAction")
            add_folder.clicked.connect(self.add_music_folder)
            remove_folder = QPushButton("Remove Folder")
            remove_folder.setObjectName("headerAction")
            remove_folder.clicked.connect(self.remove_music_folder)
            rescan = QPushButton("Rescan")
            rescan.setObjectName("headerAction")
            rescan.clicked.connect(self.rescan_music_library)
            toolbar.addWidget(self.library_search, 1)
            toolbar.addWidget(add_folder)
            toolbar.addWidget(remove_folder)
            toolbar.addWidget(rescan)
            root.addLayout(toolbar)

            self.library_folder_label = QLabel()
            self.library_folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(self.library_folder_label)

            self.library_columns = [
                ("cover", "Album cover"), ("track", "#"),
                ("title", "Title / track artist"),
                ("artist", "Artist / album"), ("date", "Date"),
                ("duration", "Duration"), ("codec", "Codec"),
                ("bitrate", "Bitrate"), ("file_size", "File size"),
                ("file_name", "File name"), ("file_path", "File path"),
                ("modified", "File last modified"),
            ]
            self.library_table = QTreeWidget()
            self.library_table.setObjectName("libraryTable")
            self.library_table.setColumnCount(len(self.library_columns))
            self.library_table.setHeaderLabels([label for _, label in self.library_columns])
            self.library_table.setRootIsDecorated(True)
            self.library_table.setAlternatingRowColors(True)
            self.library_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.library_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.library_table.itemDoubleClicked.connect(self._play_library_item)
            self.library_table.itemClicked.connect(self._open_library_cover_preview)
            header = self.library_table.header()
            header.setStretchLastSection(False)
            header.setSectionsMovable(True)
            header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            header.customContextMenuRequested.connect(self._show_library_column_menu)
            self.library_table.setTreePosition(2)
            self.library_table.setIconSize(QSize(52, 52))
            self.library_table.setItemDelegateForColumn(0, AlbumCoverDelegate(self.library_table))
            # Covers are loaded lazily for visible rows only. This prevents a
            # large library from decoding every embedded image at once.
            self.library_table.verticalScrollBar().valueChanged.connect(self._queue_visible_library_covers)
            self.library_table.itemExpanded.connect(lambda _item: self._queue_visible_library_covers())
            self.library_table.setColumnWidth(0, 62)
            self.library_table.setColumnWidth(1, 42)
            self.library_table.setColumnWidth(2, 330)
            self.library_table.setColumnWidth(3, 260)
            self.library_table.setColumnWidth(4, 85)
            self.library_table.setColumnWidth(5, 75)
            self.library_table.setColumnWidth(9, 180)
            self.library_table.setColumnWidth(10, 360)
            self.library_table.setColumnWidth(11, 150)
            self._apply_library_column_state()
            header.sectionResized.connect(lambda *_: self._save_library_column_state())
            header.sectionMoved.connect(lambda *_: self._save_library_column_state())
            root.addWidget(self.library_table, 1)
            return page

        def _show_library_column_menu(self, pos):
            header = self.library_table.header()
            menu = QMenu(self)
            columns_menu = menu.addMenu("Columns")
            for logical, (key, label) in enumerate(self.library_columns):
                action = columns_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(not header.isSectionHidden(logical))
                action.toggled.connect(lambda checked, index=logical: self._set_library_column_visible(index, checked))
            menu.addSeparator()
            reset = menu.addAction("Reset columns")
            reset.triggered.connect(self._reset_library_columns)
            menu.exec(header.mapToGlobal(pos))

        def _set_library_column_visible(self, logical, visible):
            self.library_table.setColumnHidden(logical, not visible)
            self._save_library_column_state()

        def _apply_library_column_state(self):
            state = self.library_column_state or {}
            header = self.library_table.header()
            hidden = set(state.get("hidden", [])) if "hidden" in state else set(
                range(6, len(self.library_columns))
            )
            widths = state.get("widths", {})
            order = state.get("order", [])
            for logical in range(len(self.library_columns)):
                header.setSectionHidden(logical, logical in hidden)
                if str(logical) in widths:
                    try: self.library_table.setColumnWidth(logical, int(widths[str(logical)]))
                    except (TypeError, ValueError): pass
            if isinstance(order, list):
                for visual, logical in enumerate(order):
                    try:
                        logical = int(logical)
                        if 0 <= logical < len(self.library_columns):
                            current = header.visualIndex(logical)
                            if current != visual: header.moveSection(current, visual)
                    except (TypeError, ValueError): pass

        def _save_library_column_state(self):
            if not hasattr(self, "library_table") or not hasattr(self, "library_columns"):
                return
            header = self.library_table.header()
            state = {
                "hidden": [i for i in range(len(self.library_columns)) if header.isSectionHidden(i)],
                "widths": {str(i): self.library_table.columnWidth(i) for i in range(len(self.library_columns))},
                "order": [header.logicalIndex(v) for v in range(len(self.library_columns))],
            }
            self.library_column_state = state
            save_music_library(self.library_folders, [t.to_dict() for t in self.library_tracks], state)

        def _reset_library_columns(self):
            header = self.library_table.header()
            for logical in range(len(self.library_columns)):
                header.setSectionHidden(logical, logical >= 6)
                current = header.visualIndex(logical)
                if current != logical: header.moveSection(current, logical)
            self.library_column_state = {}
            self._save_library_column_state()

        def _apply_main_window_style(self):
            palette = build_theme_palette(
                self.appearance_settings.get("theme_mode", "light"),
                self.appearance_settings.get("theme_color", "#3a9879"),
            )
            style = """
                QMainWindow#lyricsPlusWindow,
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
                    color: @ACCENT_TEXT@;
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
                QTreeWidget#libraryTable {
                    color: @TEXT@;
                    background: @SURFACE@;
                    alternate-background-color: @SURFACE_ALT@;
                    border: 1px solid @BORDER@;
                    border-radius: 6px;
                    outline: none;
                }
                QTreeWidget#libraryTable::item {
                    padding: 4px 3px;
                }
                QTreeWidget#libraryTable::item:selected {
                    color: @ACCENT_TEXT@;
                    background: @ACCENT_SOFT@;
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
            """
            replacements = {
                "@ACCENT@": "accent",
                "@ACCENT_HOVER@": "accent_hover",
                "@ACCENT_BORDER@": "accent_border",
                "@ACCENT_SOFT@": "accent_soft",
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

        @staticmethod
        def _format_library_duration(seconds):
            try:
                total = max(0, int(round(float(seconds))))
            except (TypeError, ValueError):
                return ""
            minutes, seconds = divmod(total, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                return f"{hours}:{minutes:02d}:{seconds:02d}"
            return f"{minutes}:{seconds:02d}"

        @staticmethod
        def _format_library_size(size):
            try:
                value = float(size)
            except (TypeError, ValueError):
                return ""
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if value < 1024 or unit == "TB":
                    return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
                value /= 1024

        def _populate_library_view(self):
            if not hasattr(self, "library_table"):
                return
            query = self.library_search.text().casefold().strip() if hasattr(self, "library_search") else ""
            self.library_table.clear()
            grouped = {}
            for track in self.library_tracks:
                haystack = " ".join([track.display_title, track.artist, track.album, track.album_artist, track.date, track.path]).casefold()
                if query and query not in haystack:
                    continue
                group = (track.album_artist or track.artist or "Unknown artist", track.album or "Unknown album", track.date or "")
                grouped.setdefault(group, []).append(track)

            for (artist, album, date), tracks in grouped.items():
                heading = f"{artist} — {album}"
                if date:
                    heading += f" [{date}]"
                # A group header is a real multi-column row, not a piece of
                # text painted across whichever column happens to be visible.
                # Keep the cover cell genuinely empty and put the heading in
                # the Title column. This is important when the user hides or
                # resizes columns: album/artist text must never appear inside
                # the Album cover column.
                parent_values = ["" for _ in self.library_columns]
                parent_values[2] = heading  # Title / track artist
                parent = QTreeWidgetItem(parent_values)
                parent.setFirstColumnSpanned(False)
                parent.setExpanded(True)
                parent.setData(0, Qt.ItemDataRole.UserRole, None)
                self.library_table.addTopLevelItem(parent)
                tracks.sort(key=lambda t: (self._library_track_number(t.track_number), t.display_title.casefold()))
                for track in tracks:
                    item = QTreeWidgetItem([
                        "",
                        track.track_number or "",
                        track.display_title,
                        track.artist or artist,
                        track.date,
                        self._format_library_duration(track.duration),
                        track.codec,
                        f"{track.bitrate} kbps" if track.bitrate else "",
                        self._format_library_size(track.file_size),
                        Path(track.path).name,
                        track.path,
                        __import__("datetime").datetime.fromtimestamp(track.modified).strftime("%Y-%m-%d %H:%M:%S") if track.modified else "",
                    ])
                    item.setData(0, Qt.ItemDataRole.UserRole, track.path)
                    # Artwork is intentionally deferred until the row is visible.
                    item.setSizeHint(0, QSize(0, 58))
                    parent.addChild(item)

            folder_text = "; ".join(self.library_folders) if self.library_folders else "No music folders yet. Add a folder to build your library."
            self.library_folder_label.setText(f"Library folders: {folder_text}")
            # Do not auto-resize the cover column here: the user controls its width.
            self._queue_visible_library_covers()

        def _queue_visible_library_covers(self, *_args):
            if self._library_cover_load_pending:
                return
            self._library_cover_load_pending = True
            QTimer.singleShot(0, self._load_visible_library_covers)

        def _load_visible_library_covers(self):
            self._library_cover_load_pending = False
            viewport = self.library_table.viewport().rect()
            item = self.library_table.itemAt(viewport.topLeft())
            if item is None:
                item = self.library_table.topLevelItem(0)
            # Walk from the first visible item through the viewport plus a
            # small prefetch margin, not through the entire library.
            y_limit = viewport.bottom() + 240
            current = item
            while current is not None:
                rect = self.library_table.visualItemRect(current)
                if rect.top() > y_limit:
                    break
                path = current.data(0, Qt.ItemDataRole.UserRole)
                if path and current.data(0, Qt.ItemDataRole.DecorationRole) is None:
                    cover = self._library_cover_pixmap(path)
                    if not cover.isNull():
                        current.setData(0, Qt.ItemDataRole.DecorationRole, cover)
                current = self.library_table.itemBelow(current)

        def _library_cover_pixmap(self, path):
            """Return a thumbnail made directly from this track's embedded cover."""
            path = str(path)
            cached = self._library_cover_cache.get(path)
            if cached is not None:
                self._library_cover_cache.move_to_end(path)
                return cached

            # Decode directly to thumbnail dimensions instead of expanding a
            # potentially 4000x4000 cover in memory first.
            pixmap = load_embedded_cover_thumbnail(path, QSize(52, 52))
            self._library_cover_cache[path] = pixmap
            self._library_cover_cache.move_to_end(path)
            while len(self._library_cover_cache) > self._library_cover_cache_limit:
                self._library_cover_cache.popitem(last=False)
            return pixmap

        @staticmethod
        def _library_track_number(value):
            try:
                return int(str(value).split("/", 1)[0])
            except (TypeError, ValueError):
                return 10**9

        def _play_library_item(self, item, column=0):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if not path:
                return
            self.load_audio(path)
            self.media_player.play()

        def _open_library_cover_preview(self, item, column):
            if column != 0:
                return
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if not path:
                return
            cover_data = extract_embedded_cover(path)
            if not cover_data:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(cover_data):
                return
            CoverPreviewDialog(pixmap, self).exec()

        def add_music_folder(self):
            folder = QFileDialog.getExistingDirectory(self, "Add Music Folder")
            if not folder:
                return
            folder = str(Path(folder).resolve())
            if folder not in self.library_folders:
                self.library_folders.append(folder)
            self.rescan_music_library()

        def remove_music_folder(self):
            if not self.library_folders:
                return
            folder, ok = QInputDialog.getItem(self, "Remove Music Folder", "Folder:", self.library_folders, 0, False)
            if not ok or not folder:
                return
            self.library_folders.remove(folder)
            self.rescan_music_library()

        def rescan_music_library(self):
            if self._library_scan_worker is not None and self._library_scan_worker.isRunning():
                return
            self.library_folder_label.setText("Scanning music library...")
            self.library_table.setEnabled(False)
            worker = LibraryScanWorker(self.library_folders, self)
            self._library_scan_worker = worker
            worker.completed.connect(self._library_scan_finished)
            worker.finished.connect(lambda: self.library_table.setEnabled(True))
            worker.start()

        def _library_scan_finished(self, tracks):
            self._library_cover_cache.clear()
            self.library_tracks = list(tracks)
            save_music_library(self.library_folders, [track.to_dict() for track in self.library_tracks], self.library_column_state)
            self._populate_library_view()


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

                restored = [
                    Path(item)
                    for item in paths
                    if isinstance(item, str) and Path(item).is_file()
                ]

                if not restored:
                    return

                self.playlist = restored

                try:
                    index = int(playlist_state.get("current_index", 0))
                except (TypeError, ValueError):
                    index = 0

                index = max(0, min(index, len(self.playlist) - 1))
                self.current_index = index
                self.load_audio(str(self.playlist[index]))


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
            """Refresh the lyrics renderer after returning to the Lyrics page."""
            if not hasattr(self, "workspace_stack"):
                return
            current_page = self.workspace_stack.widget(index)
            self.library_button.setChecked(current_page is self.library_page)
            self.lyrics_view_button.setChecked(current_page is self.lyrics_page)
            if current_page is not self.lyrics_page:
                return

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
                    or len(renderer.rows) != len(source_lines)
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


        def _start_booru_fetch(self, audio_path, fallback_data=None):
            """Fetch Booru artwork without blocking song loading."""
            self._booru_request_id += 1
            request_id = self._booru_request_id
            tags = self.appearance_settings.get("booru_tags", "scenery")

            print(f"[Background] Fetching Booru artwork: tags={tags!r}")
            future = self._booru_executor.submit(
                fetch_booru_artwork,
                tags,
            )
            self._booru_future = future

            def check_result():
                if not future.done():
                    QTimer.singleShot(50, check_result)
                    return

                # Ignore stale results from a previous song.
                if request_id != self._booru_request_id:
                    return

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
            source = self.appearance_settings.get(
                "artwork_source",
                "album_first",
            )

            embedded = None
            if source != "booru_only":
                embedded = extract_embedded_cover(audio_path)

            # New song invalidates any pending result from the old one.
            self._booru_request_id += 1

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

            self.playlist = [Path(path) for path in paths]
            self.current_index = 0
            known = {track.path for track in self.library_tracks}
            changed = False
            for path in self.playlist:
                resolved = str(path.resolve())
                if resolved not in known:
                    try:
                        self.library_tracks.append(read_track(path))
                        known.add(resolved)
                        changed = True
                    except (OSError, ValueError):
                        pass
            if changed:
                save_music_library(self.library_folders, [track.to_dict() for track in self.library_tracks], self.library_column_state)
                self._populate_library_view()
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



        def search_lrclib_lyrics(self):
            """Search LRCLIB, inspect results, and load the selected lyrics."""
            dialog = QDialog(self)
            dialog.setWindowTitle("Search Lyrics")
            dialog.resize(900, 650)

            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("Search LRCLIB"))

            search_row = QHBoxLayout()
            query_input = QLineEdit(dialog)
            query_input.setPlaceholderText("Song title, artist, or keywords")

            # Prefer embedded tags, falling back to the filename when unavailable.
            if self.current_audio_path:
                query_input.setText(
                    _lyrics_search_query(self.current_audio_path)
                )

            search_button = QPushButton("Search", dialog)
            search_row.addWidget(query_input)
            search_row.addWidget(search_button)
            layout.addLayout(search_row)

            splitter = QSplitter(Qt.Orientation.Vertical, dialog)

            results = QTableWidget(0, 4, splitter)
            results.setHorizontalHeaderLabels(
                ["Title", "Album", "Artist", "Synced"]
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

            preview_panel = QWidget(splitter)
            preview_layout = QVBoxLayout(preview_panel)
            preview_layout.setContentsMargins(0, 0, 0, 0)
            preview_layout.addWidget(QLabel("Preview / Inspect Lyrics"))

            preview = QTextEdit(preview_panel)
            preview.setReadOnly(True)
            preview.setAcceptRichText(False)
            preview.setPlaceholderText(
                "Select a result to preview its lyrics here."
            )
            preview_layout.addWidget(preview)

            splitter.addWidget(results)
            splitter.addWidget(preview_panel)
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 1)
            layout.addWidget(splitter, 1)

            status = QLabel("", dialog)
            layout.addWidget(status)

            buttons = QHBoxLayout()
            use_button = QPushButton("Use Selected Lyrics", dialog)
            cancel_button = QPushButton("Cancel", dialog)
            buttons.addStretch(1)
            buttons.addWidget(use_button)
            buttons.addWidget(cancel_button)
            layout.addLayout(buttons)

            records = []

            def selected_record():
                row_index = results.currentRow()
                if 0 <= row_index < len(records):
                    return records[row_index]
                return None

            def update_preview():
                item = selected_record()
                if item is None:
                    preview.clear()
                    return

                synced = item.get("syncedLyrics") or ""
                plain = item.get("plainLyrics") or ""

                if synced:
                    preview.setPlainText(synced)
                elif plain:
                    preview.setPlainText(plain)
                else:
                    preview.setPlainText(
                        "This LRCLIB result does not contain lyrics."
                    )

            def run_search():
                query = query_input.text().strip()
                if not query:
                    status.setText(
                        "Enter a title, artist, or other keywords."
                    )
                    return

                status.setText("Searching LRCLIB…")
                results.setRowCount(0)
                preview.clear()
                records.clear()

                try:
                    response = requests.get(
                        "https://lrclib.net/api/search",
                        params={"q": query},
                        headers={
                            "User-Agent": "LyricsPlus/1.0 (LRCLIB client)"
                        },
                        timeout=15,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except Exception as error:
                    status.setText(f"Search failed: {error}")
                    return

                if not isinstance(payload, list):
                    payload = []

                records.extend(payload)

                for row_index, item in enumerate(records):
                    results.insertRow(row_index)

                    title = (
                        item.get("trackName")
                        or item.get("name")
                        or "Unknown"
                    )
                    album = item.get("albumName") or ""
                    artist = item.get("artistName") or "Unknown artist"
                    synced = "Yes" if item.get("syncedLyrics") else "No"

                    for column, value in enumerate(
                        [title, album, artist, synced]
                    ):
                        table_item = QTableWidgetItem(str(value))
                        results.setItem(
                            row_index, column, table_item
                        )

                if records:
                    results.selectRow(0)
                    update_preview()
                    status.setText(
                        f"{len(records)} result(s). "
                        "Select one to inspect the lyrics."
                    )
                else:
                    status.setText("No lyrics found.")

            def use_selected():
                item = selected_record()
                if item is None:
                    status.setText("Select a result first.")
                    return

                synced = item.get("syncedLyrics")
                plain = item.get("plainLyrics")

                if not synced and not plain:
                    status.setText(
                        "That result contains no lyrics."
                    )
                    return

                if not self.current_audio_path:
                    status.setText(
                        "Open an audio file before loading lyrics."
                    )
                    return

                if synced:
                    destination = (
                        self.current_audio_path.with_suffix(".lrc")
                    )
                    content = synced
                else:
                    destination = (
                        self.current_audio_path.with_suffix(".txt")
                    )
                    content = plain

                try:
                    destination.write_text(
                        content.rstrip() + "\n",
                        encoding="utf-8",
                    )
                    self.load_lrc_file(str(destination))
                    self.current_lyrics_path = destination
                except Exception as error:
                    status.setText(
                        f"Could not save lyrics: {error}"
                    )
                    return

                dialog.accept()

            search_button.clicked.connect(run_search)
            query_input.returnPressed.connect(run_search)
            results.itemSelectionChanged.connect(update_preview)
            results.itemDoubleClicked.connect(
                lambda _: use_selected()
            )
            use_button.clicked.connect(use_selected)
            cancel_button.clicked.connect(dialog.reject)

            dialog.exec()

        def _apply_output_device(self, settings):
            """Apply a persisted Qt audio output device, falling back to default."""
            device_id = str((settings or {}).get("output_device", "") or "")
            selected = None
            if device_id:
                for device in QMediaDevices.audioOutputs():
                    if bytes(device.id()).hex() == device_id:
                        selected = device
                        break

            # Empty ID means Qt/system default.
            if selected is None:
                self.audio_output.setDevice(QMediaDevices.defaultAudioOutput())
            else:
                self.audio_output.setDevice(selected)
            if hasattr(self, "volume_slider") and self.volume_slider.value() > 100:
                self._configure_pcm_volume(
                    self.volume_slider.value(), refresh_format=True
                )

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
            )

            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.startup_settings = dialog.get_startup_settings()
                save_startup_settings(self.startup_settings)

                self.appearance_settings = dialog.get_appearance_settings()
                save_appearance_settings(self.appearance_settings)
                self._apply_main_window_style()
                self._apply_appearance_settings()

                self.playback_settings = dialog.get_playback_settings()
                save_playback_settings(self.playback_settings)
                self.waveform_widget.setVisible(
                    self.playback_settings.get("show_waveform", True)
                    and find_ffmpeg() is not None
                )
                self._apply_output_device(self.playback_settings)
                self._update_shuffle_button_text()

                self.karaoke_settings = dialog.get_karaoke_settings()
                save_karaoke_settings(self.karaoke_settings)

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


        def get_current_lyrics_path(self):
            """Return the active lyrics file, or a default .lrc path."""
            if self.current_lyrics_path:
                return Path(self.current_lyrics_path)

            if self.current_audio_path:
                return self.find_lrc_for_audio(
                    self.current_audio_path
                )

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
            if dialog.exec() == QDialog.DialogCode.Accepted:
                saved_path = getattr(dialog, "result_path", lyrics_path)
                self.load_lrc_file(str(saved_path))
                self.current_lyrics_path = Path(saved_path)


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


        def show_playlist(self):
            dialog = PlaylistDialog(
                self.playlist,
                self.current_index,
                self,
            )

            def refresh_dialog():
                dialog.set_playlist(
                    self.playlist,
                    self.current_index,
                )

            def load_track(index):
                if not 0 <= index < len(self.playlist):
                    return

                self.current_index = index
                self.load_audio(
                    str(self.playlist[index])
                )
                self.media_player.play()
                refresh_dialog()

            def remove_track(index):
                if not 0 <= index < len(self.playlist):
                    return

                removing_current = (
                    index == self.current_index
                )

                del self.playlist[index]

                if not self.playlist:
                    self.current_index = -1

                    if removing_current:
                        self.stop_playback()
                        self.current_audio_path = None

                elif index < self.current_index:
                    self.current_index -= 1

                elif removing_current:
                    if index >= len(self.playlist):
                        self.current_index = len(self.playlist) - 1

                    self.load_audio(
                        str(self.playlist[self.current_index])
                    )
                    self.media_player.play()

                refresh_dialog()
                self.update_transport_buttons()

            def clear_playlist():
                self.playlist.clear()
                self.current_index = -1
                self.stop_playback()
                self.current_audio_path = None
                self.setWindowTitle("LRC+")
                refresh_dialog()
                self.update_transport_buttons()

            dialog.track_requested.connect(load_track)
            dialog.remove_requested.connect(remove_track)
            dialog.clear_requested.connect(clear_playlist)

            dialog.exec()


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
            dialog.exec()


        def closeEvent(self, event):
            if hasattr(self, "_booru_executor"):
                self._booru_executor.shutdown(wait=False, cancel_futures=True)
            self._save_startup_state()

            self.lyric_widget.stop_workers()

            self.media_player.stop()
            self.playback_settings["volume"] = self.volume_slider.value()
            save_playback_settings(self.playback_settings)
            self.media_player.setAudioBufferOutput(None)
            self._stop_pcm_sink()

            event.accept()
