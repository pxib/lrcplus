from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QColorDialog,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QTabWidget,
    QSpinBox,
    QScrollArea,
    QLineEdit,
    QGroupBox,
)

LYRIC_KEYS = (
    "scrolling_lyrics",
    "lyrics_direction",
    "lyrics_padding",
    "vertical_padding",
    "show_ruby",
    "furigana_parser",
    "limited_reading_rendering",
    "limited_reading_range",
    "fade_in_readings",
    "ruby_padding",
    "ruby_font_size",
    "font_family",
    "font_size",
    "lyrics_alignment",
    "highlight_current",
    "highlight_animation",
    "highlight_animation_duration",
    "highlight_animation_out_duration",
    "highlight_animation_easing",
    "highlight_color",
    "lyrics_color",
    "scrolling_mode",
    "continuous_scroll_speed",
    "continuous_scroll_duration_based",
    "continuous_scroll_nudge_current",
    "scroll_animation_speed",
    "scroll_animation_easing",
    "scroll_behavior",
)

# Parser selection is an application-wide engine choice, not a per-file lyric override.
LOCAL_LYRIC_KEYS = tuple(key for key in LYRIC_KEYS if key != "furigana_parser")


class SettingsDialog(QDialog):
    def __init__(
        self,
        folders,
        global_lyrics,
        startup_settings=None,
        appearance_settings=None,
        playback_settings=None,
        karaoke_settings=None,
        local_overrides=None,
        current_file=None,
        plugin_manager=None,
        parent=None,
    ):
        super().__init__(parent)

        self.global_lyrics = dict(global_lyrics or {})
        self.startup_settings = dict(startup_settings or {})
        self.appearance_settings = dict(appearance_settings or {})
        self.playback_settings = dict(playback_settings or {})
        self.karaoke_settings = dict(karaoke_settings or {})
        self.local_overrides = dict(local_overrides or {})
        self.current_file = current_file
        self.plugin_manager = plugin_manager
        self.plugin_toggles = {}

        self.setWindowTitle("Settings")
        self.resize(700, 600)

        tabs = QTabWidget()

        # ===== GENERAL =====
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)

        startup_heading = QLabel("<b>Startup</b>")
        general_layout.addWidget(startup_heading)

        self.remember_window_checkbox = QCheckBox(
            "Remember window size and position"
        )
        self.remember_window_checkbox.setChecked(
            bool(self.startup_settings.get("remember_window", True))
        )
        general_layout.addWidget(self.remember_window_checkbox)

        self.restore_playlist_checkbox = QCheckBox(
            "Restore previous playlist"
        )
        self.restore_playlist_checkbox.setChecked(
            bool(self.startup_settings.get("restore_playlist", False))
        )
        general_layout.addWidget(self.restore_playlist_checkbox)

        general_layout.addStretch()

        # ===== APPEARANCE =====
        appearance_tab = QWidget()
        appearance_layout = QVBoxLayout(appearance_tab)

        background_heading = QLabel("<b>Background</b>")
        appearance_layout.addWidget(background_heading)

        self.background_enabled_checkbox = QCheckBox(
            "Show artwork behind lyrics"
        )
        self.background_enabled_checkbox.setChecked(
            bool(self.appearance_settings.get("background_enabled", True))
        )
        appearance_layout.addWidget(self.background_enabled_checkbox)

        artwork_source_row = QHBoxLayout()
        artwork_source_row.addWidget(QLabel("Artwork source"))
        self.artwork_source_combo = QComboBox()
        self.artwork_source_combo.addItem(
            "Album cover first (fallback to Booru)",
            "album_first",
        )
        self.artwork_source_combo.addItem(
            "Booru first (fallback to album cover)",
            "booru_first",
        )
        self.artwork_source_combo.addItem(
            "Album cover only",
            "album_only",
        )
        self.artwork_source_combo.addItem(
            "Booru only",
            "booru_only",
        )
        current_source = self.appearance_settings.get(
            "artwork_source",
            "album_first",
        )
        source_index = self.artwork_source_combo.findData(current_source)
        self.artwork_source_combo.setCurrentIndex(max(0, source_index))
        artwork_source_row.addWidget(self.artwork_source_combo, 1)
        appearance_layout.addLayout(artwork_source_row)

        booru_tags_row = QHBoxLayout()
        booru_tags_row.addWidget(QLabel("Booru tags"))
        self.booru_tags_edit = QLineEdit(
            self.appearance_settings.get("booru_tags", "scenery")
        )
        self.booru_tags_edit.setPlaceholderText("e.g. scenery night city")
        self.booru_tags_edit.setToolTip(
            "Safebooru search tags. A random matching image is chosen."
        )
        booru_tags_row.addWidget(self.booru_tags_edit, 1)
        appearance_layout.addLayout(booru_tags_row)

        blur_mode_row = QHBoxLayout()
        blur_mode_row.addWidget(QLabel("Blur quality"))
        self.background_blur_mode_combo = QComboBox()
        self.background_blur_mode_combo.addItem("High quality (Gaussian)", "quality")
        self.background_blur_mode_combo.addItem("Low quality (funny)", "low_quality")
        current_blur_mode = self.appearance_settings.get(
            "background_blur_mode",
            "quality",
        )
        index = self.background_blur_mode_combo.findData(current_blur_mode)
        self.background_blur_mode_combo.setCurrentIndex(
            max(0, index)
        )
        blur_mode_row.addWidget(self.background_blur_mode_combo, 1)
        appearance_layout.addLayout(blur_mode_row)

        self.background_blur_bleed_checkbox = QCheckBox(
            "Let blur bleed beyond artwork edges"
        )
        self.background_blur_bleed_checkbox.setChecked(
            bool(
                self.appearance_settings.get(
                    "background_blur_bleed",
                    False,
                )
            )
        )
        appearance_layout.addWidget(self.background_blur_bleed_checkbox)

        blur_row = QHBoxLayout()
        blur_row.addWidget(QLabel("Blur"))
        self.background_blur_slider = QSlider(Qt.Orientation.Horizontal)
        self.background_blur_slider.setRange(0, 30)
        self.background_blur_slider.setValue(
            int(self.appearance_settings.get("background_blur", 12))
        )
        self.background_blur_value = QLabel()
        self.background_blur_value.setFixedWidth(32)
        blur_row.addWidget(self.background_blur_slider, 1)
        blur_row.addWidget(self.background_blur_value)
        appearance_layout.addLayout(blur_row)

        darkness_row = QHBoxLayout()
        darkness_row.addWidget(QLabel("Darkness"))
        self.background_darkness_slider = QSlider(Qt.Orientation.Horizontal)
        self.background_darkness_slider.setRange(0, 220)
        self.background_darkness_slider.setValue(
            int(self.appearance_settings.get("background_darkness", 155))
        )
        self.background_darkness_value = QLabel()
        self.background_darkness_value.setFixedWidth(42)
        darkness_row.addWidget(self.background_darkness_slider, 1)
        darkness_row.addWidget(self.background_darkness_value)
        appearance_layout.addLayout(darkness_row)

        def update_background_labels(*_):
            self.background_blur_value.setText(
                str(self.background_blur_slider.value())
            )
            self.background_darkness_value.setText(
                f"{self.background_darkness_slider.value()}"
            )

        self.background_blur_slider.valueChanged.connect(
            update_background_labels
        )
        self.background_darkness_slider.valueChanged.connect(
            update_background_labels
        )
        update_background_labels()

        self.background_blur_slider.setEnabled(
            self.background_enabled_checkbox.isChecked()
        )
        self.background_darkness_slider.setEnabled(
            self.background_enabled_checkbox.isChecked()
        )

        self.background_enabled_checkbox.toggled.connect(
            self.artwork_source_combo.setEnabled
        )
        self.background_enabled_checkbox.toggled.connect(
            self.booru_tags_edit.setEnabled
        )
        self.background_enabled_checkbox.toggled.connect(
            self.background_blur_slider.setEnabled
        )
        self.background_enabled_checkbox.toggled.connect(
            self.background_darkness_slider.setEnabled
        )
        self.background_enabled_checkbox.toggled.connect(
            self.background_blur_mode_combo.setEnabled
        )
        self.background_enabled_checkbox.toggled.connect(
            self.background_blur_bleed_checkbox.setEnabled
        )

        self._add_section_heading(appearance_layout, "Empty timestamp placeholder")
        self.empty_timestamp_placeholder_checkbox = QCheckBox(
            "Show placeholder for empty timed lyric lines"
        )
        self.empty_timestamp_placeholder_checkbox.setChecked(
            bool(self.appearance_settings.get(
                "empty_timestamp_placeholder_enabled", True
            ))
        )
        appearance_layout.addWidget(self.empty_timestamp_placeholder_checkbox)

        placeholder_row = QHBoxLayout()
        placeholder_row.addWidget(QLabel("Placeholder:"))
        self.empty_timestamp_placeholder_edit = QLineEdit(
            self.appearance_settings.get(
                "empty_timestamp_placeholder", "♪ {countdown}"
            )
        )
        self.empty_timestamp_placeholder_edit.setPlaceholderText("♪ {countdown}")
        self.empty_timestamp_placeholder_edit.setToolTip(
            "Use {countdown} where the remaining time until the next lyric should appear."
        )
        placeholder_row.addWidget(self.empty_timestamp_placeholder_edit, 1)
        appearance_layout.addLayout(placeholder_row)
        placeholder_hint = QLabel(
            "Example: ♪ {countdown}  →  ♪ 12.5"
        )
        placeholder_hint.setStyleSheet("color: palette(mid); font-style: italic;")
        appearance_layout.addWidget(placeholder_hint)
        self.empty_timestamp_placeholder_edit.setEnabled(
            self.empty_timestamp_placeholder_checkbox.isChecked()
        )
        self.empty_timestamp_placeholder_checkbox.toggled.connect(
            self.empty_timestamp_placeholder_edit.setEnabled
        )

        appearance_layout.addStretch()

        # ===== PLAYBACK =====
        playback_tab = self._make_scroll_tab(
            "Playback behavior and timing settings.",
            lambda body_layout: self._create_playback_controls(body_layout),
        )

        # ===== LYRICS =====
        lyrics_tab = QWidget()
        lyrics_layout = QVBoxLayout(lyrics_tab)
        self.scope_tabs = QTabWidget()

        global_tab = self._make_scroll_tab(
            "Default lyric display settings for all files.",
            lambda body_layout: self._create_lyrics_controls(
                self.global_lyrics,
                body_layout,
                include_playback=False,
            ),
        )
        self.global_controls = self._last_scroll_controls

        if current_file:
            local_tab = self._make_scroll_tab(
                f"Overrides for:\n{current_file}",
                lambda body_layout: self._create_local_controls(
                    self.global_lyrics,
                    self.local_overrides,
                    body_layout,
                ),
            )
            self.local_controls = self._last_scroll_controls
        else:
            local_tab = QWidget()
            local_layout = QVBoxLayout(local_tab)
            local_layout.addWidget(
                QLabel(
                    "Load an audio file to configure Local (File) "
                    "lyrics settings."
                )
            )
            local_layout.addStretch()
            self.local_controls = {}

        self.scope_tabs.addTab(global_tab, "Global")
        self.scope_tabs.addTab(local_tab, "Local (File)")
        lyrics_layout.addWidget(self.scope_tabs)

        # ===== FILES =====
        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)
        files_layout.addWidget(QLabel("Lyrics search folders"))
        files_layout.addWidget(
            QLabel("These folders are searched for matching .lrc/.lrcx files.")
        )
        self.folder_list = QListWidget()
        for folder in folders:
            self.folder_list.addItem(QListWidgetItem(folder))
        files_layout.addWidget(self.folder_list, 1)

        folder_controls = QHBoxLayout()
        add_button = QPushButton("Add Folder")
        add_button.clicked.connect(self.add_folder)
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self.remove_folder)
        folder_controls.addWidget(add_button)
        folder_controls.addWidget(remove_button)
        folder_controls.addStretch()
        files_layout.addLayout(folder_controls)

        # ===== PLUGINS =====
        plugins_tab = QWidget()
        plugins_layout = QVBoxLayout(plugins_tab)
        plugins_layout.addWidget(QLabel("Installed plugins"))
        plugins_notice = QLabel(
            "Plugin changes are saved immediately when Settings is accepted and take effect after restarting LRC+."
        )
        plugins_notice.setWordWrap(True)
        plugins_notice.setStyleSheet("color: #A0A0A0; font-style: italic;")
        plugins_layout.addWidget(plugins_notice)

        plugin_scroll = QScrollArea()
        plugin_scroll.setWidgetResizable(True)
        plugin_body = QWidget()
        plugin_body_layout = QVBoxLayout(plugin_body)
        plugin_body_layout.setContentsMargins(8, 8, 8, 8)
        plugin_body_layout.setSpacing(8)

        catalog = []
        if self.plugin_manager is not None and self.plugin_manager.plugins_dir:
            catalog = self.plugin_manager.discover_plugins(
                self.plugin_manager.plugins_dir
            )

        if catalog:
            disabled = set(self.plugin_manager.disabled_plugins)
            for plugin in catalog:
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(8, 8, 8, 8)

                details = QVBoxLayout()
                title = QLabel(
                    f"<b>{plugin['name']}</b>  <span style='color: gray;'>v{plugin['version']}</span>"
                )
                details.addWidget(title)
                description = QLabel(plugin['description'] or "No description available.")
                description.setWordWrap(True)
                description.setStyleSheet("color: #A0A0A0;")
                details.addWidget(description)
                row.addLayout(details, 1)

                toggle = QCheckBox("Enabled")
                plugin_id = plugin['plugin_id']
                toggle.setChecked(plugin_id not in disabled)
                row.addWidget(toggle)
                self.plugin_toggles[plugin_id] = toggle

                plugin_body_layout.addWidget(row_widget)
        else:
            empty = QLabel("No plugins were found.")
            empty.setStyleSheet("color: #A0A0A0;")
            plugin_body_layout.addWidget(empty)

        self._add_section_heading(plugin_body_layout, "Karaoke")
        self.karaoke_sweep_fps = QComboBox()
        for fps in (15, 30, 60, 120):
            self.karaoke_sweep_fps.addItem(f"{fps} FPS", fps)
        current_sweep_fps = self.karaoke_settings.get("sweep_fps", 60)
        index = self.karaoke_sweep_fps.findData(current_sweep_fps)
        self.karaoke_sweep_fps.setCurrentIndex(
            index if index >= 0 else self.karaoke_sweep_fps.findData(60)
        )
        self._add_labeled(
            plugin_body_layout,
            "Karaoke sweep:",
            self.karaoke_sweep_fps,
        )
        self.karaoke_sweep_fps.setToolTip(
            "How often karaoke highlighting is refreshed during playback."
        )

        plugin_body_layout.addStretch()
        plugin_scroll.setWidget(plugin_body)
        plugins_layout.addWidget(plugin_scroll, 1)

        tabs.addTab(general_tab, "General")
        tabs.addTab(appearance_tab, "Appearance")
        tabs.addTab(playback_tab, "Playback")
        tabs.addTab(lyrics_tab, "Lyrics")
        tabs.addTab(files_tab, "Files")
        tabs.addTab(plugins_tab, "Plugins")

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(button_box)

    def _make_scroll_tab(self, heading, builder):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.addWidget(QLabel(heading))
        controls = builder(body_layout)
        body_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        page_layout.addWidget(scroll, 1)

        self._last_scroll_controls = controls
        return page

    def _update_compact_lyrics_controls(self, controls):
        """Apply mode-dependent enable/disable rules for lyric settings."""
        display = controls.get("scrolling_lyrics")
        if display is None:
            return

        scrolling = bool(display.currentData())

        # Padding belongs to the scrolling layout, so Compact cannot use it.
        for key in ("lyrics_padding", "scrolling_mode"):
            widget = controls.get(key)
            if widget is not None:
                forced = bool(widget.property("_forced_continuous"))
                widget.setEnabled(scrolling and not forced)

        mode_widget = controls.get("scrolling_mode")
        mode = (
            mode_widget.currentData()
            if mode_widget is not None else "automatic"
        )
        continuous = mode == "continuous"

        # Continuous settings are a single category. Manual speed is disabled
        # when duration-based speed is active.
        continuous_group = controls.get("continuous_group")
        if continuous_group is not None:
            continuous_group.setEnabled(scrolling and continuous)
        duration_based = controls.get("continuous_scroll_duration_based")
        duration_enabled = bool(duration_based and duration_based.isChecked())
        speed_enabled = scrolling and continuous and not duration_enabled
        for key in ("continuous_scroll_speed", "continuous_scroll_speed_label", "continuous_scroll_speed_value"):
            widget = controls.get(key)
            if widget is not None:
                widget.setEnabled(speed_enabled)

        # Automatic/timestamp-following settings use the same category style.
        automatic_group = controls.get("automatic_group")
        if automatic_group is not None:
            automatic_group.setEnabled(scrolling and not continuous)

        # Compact uses one current lyric widget, so multi-line highlight
        # animations do not apply. Force None while the mode is Compact.
        animation = controls.get("highlight_animation")
        if animation is not None:
            if not scrolling:
                none_index = animation.findData("none")
                if none_index >= 0:
                    animation.setCurrentIndex(none_index)
            animation.setEnabled(scrolling)

        for key in (
            "highlight_animation_duration",
            "highlight_animation_out_duration",
            "highlight_animation_easing",
        ):
            widget = controls.get(key)
            if widget is not None:
                widget.setEnabled(scrolling and animation is not None
                                  and animation.currentData() != "none")

        if animation is not None:
            # Avoid accumulating duplicate signal connections by storing a
            # small marker on the widget.
            if not animation.property("_mode_sync_connected"):
                animation.currentIndexChanged.connect(
                    lambda *_: self._update_compact_lyrics_controls(controls)
                )
                animation.setProperty("_mode_sync_connected", True)

    def _create_playback_controls(self, layout):
        self._add_section_heading(layout, "Playback")

        self.precise_timestamp_fps = QComboBox()
        for fps in (15, 30, 60, 120):
            self.precise_timestamp_fps.addItem(f"{fps} FPS", fps)
        current_fps = self.playback_settings.get("precise_timestamp_fps", 30)
        index = self.precise_timestamp_fps.findData(current_fps)
        self.precise_timestamp_fps.setCurrentIndex(
            index if index >= 0 else self.precise_timestamp_fps.findData(30)
        )
        self._add_labeled(layout, "Precise timestamp display:", self.precise_timestamp_fps)
        self.precise_timestamp_fps.setToolTip(
            "How often the precise playback timestamp updates."
        )

        self.play_mode_combo = QComboBox()
        self.play_mode_combo.addItem("Playlist", "playlist")
        self.play_mode_combo.addItem("Library", "library")
        play_mode = self.playback_settings.get("play_mode", "playlist")
        index = self.play_mode_combo.findData(play_mode)
        self.play_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self._add_labeled(layout, "Play Mode:", self.play_mode_combo)
        self.play_mode_combo.setToolTip(
            "Choose whether previous/next playback follows the current playlist or the full music library."
        )

        self.shuffle_mode_combo = QComboBox()
        self.shuffle_mode_combo.addItem("Tracks", "tracks")
        self.shuffle_mode_combo.addItem("Albums", "albums")
        shuffle_mode = self.playback_settings.get("shuffle_mode", "tracks")
        index = self.shuffle_mode_combo.findData(shuffle_mode)
        self.shuffle_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self._add_labeled(layout, "Shuffle:", self.shuffle_mode_combo)
        self.shuffle_mode_combo.setToolTip(
            "When shuffle is enabled, randomize individual tracks or entire albums."
        )

        self.after_playback_combo = QComboBox()
        self.after_playback_combo.addItem("Next Track", "next_track")
        self.after_playback_combo.addItem("Stop", "stop")
        self.after_playback_combo.addItem("Repeat All", "repeat_all")
        self.after_playback_combo.addItem("Repeat Track", "repeat_track")
        after_playback = self.playback_settings.get("after_playback", "next_track")
        index = self.after_playback_combo.findData(after_playback)
        self.after_playback_combo.setCurrentIndex(index if index >= 0 else 0)
        self._add_labeled(layout, "After Playback:", self.after_playback_combo)
        self.after_playback_combo.setToolTip(
            "Choose what happens when playback reaches the end of the current play mode."
        )

        self.output_device_combo = QComboBox()
        self.output_device_combo.addItem("System Default", "")
        for device in QMediaDevices.audioOutputs():
            device_name = device.description().strip() or "Unnamed output"
            device_id = bytes(device.id()).hex()
            self.output_device_combo.addItem(device_name, device_id)

        current_device = str(self.playback_settings.get("output_device", "") or "")
        device_index = self.output_device_combo.findData(current_device)
        self.output_device_combo.setCurrentIndex(
            device_index if device_index >= 0 else 0
        )
        self._add_labeled(layout, "Output device:", self.output_device_combo)
        self.output_device_combo.setToolTip(
            "Choose which Windows audio output device LyricsPlus uses for playback."
        )

    def _create_lyrics_controls(
        self, values, layout, include_playback=False
    ):
        controls = {}

        self._add_section_heading(layout, "Display")
        display = QComboBox()
        display.addItem("Compact", False)
        display.addItem("Scrolling", True)
        display.setCurrentIndex(
            display.findData(bool(values.get("scrolling_lyrics", False)))
        )
        controls["scrolling_lyrics"] = display
        self._add_labeled(layout, "Display mode:", display)

        direction = QComboBox()
        direction.addItem("Horizontal", "horizontal")
        direction.addItem("Vertical", "vertical")
        direction.addItem("Auto (CJK-aware)", "auto")
        direction.setCurrentIndex(
            max(0, direction.findData(values.get("lyrics_direction", "horizontal")))
        )
        controls["lyrics_direction"] = direction
        self._add_labeled(layout, "Direction:", direction)

        alignment = QComboBox()
        alignment.addItem("Left", "left")
        alignment.addItem("Center", "center")
        alignment.addItem("Right", "right")
        alignment.setCurrentIndex(
            max(0, alignment.findData(values.get("lyrics_alignment", "center")))
        )
        controls["lyrics_alignment"] = alignment

        def sync_vertical_alignment(*_):
            vertical = direction.currentData() == "vertical"
            center_index = alignment.findData("center")
            item = alignment.model().item(center_index) if center_index >= 0 else None
            if item is not None:
                item.setEnabled(not vertical)
            if vertical and alignment.currentData() == "center":
                left_index = alignment.findData("left")
                if left_index >= 0:
                    alignment.setCurrentIndex(left_index)

        direction.currentIndexChanged.connect(sync_vertical_alignment)
        sync_vertical_alignment()
        self._add_labeled(layout, "Lyric alignment:", alignment)

        lyrics_padding = self._add_slider(
            layout, "Lyrics padding:",
            values.get("lyrics_padding", 8), 0, 24, "px"
        )
        controls["lyrics_padding"] = lyrics_padding


        vertical_padding = self._add_slider(
            layout, "Vertical padding:",
            values.get("vertical_padding", 0), 0, 200, "px"
        )
        vertical_padding.setToolTip(
            "Adds top and bottom padding when lyrics use Vertical direction."
        )
        controls["vertical_padding"] = vertical_padding

        self._add_section_heading(layout, "Furigana")
        show_ruby = QCheckBox("Show furigana")
        show_ruby.setChecked(bool(values.get("show_ruby", True)))
        controls["show_ruby"] = show_ruby
        layout.addWidget(show_ruby)

        show_romaji = QCheckBox("Show romaji")
        show_romaji.setChecked(bool(values.get("show_romaji", False)))
        if show_romaji.isChecked():
            show_ruby.setChecked(False)
        controls["show_romaji"] = show_romaji
        layout.addWidget(show_romaji)

        show_ruby.toggled.connect(
            lambda checked: show_romaji.setChecked(False) if checked else None
        )
        show_romaji.toggled.connect(
            lambda checked: show_ruby.setChecked(False) if checked else None
        )

        ruby_position = QComboBox()
        ruby_position.addItem("Above lyrics", "above")
        ruby_position.addItem("Below lyrics", "below")
        ruby_position.setCurrentIndex(max(0, ruby_position.findData(values.get("ruby_position", "above"))))
        controls["ruby_position"] = ruby_position
        self._add_labeled(layout, "Ruby position:", ruby_position)

        parser = QComboBox()
        parser.addItem("Yomi (online, recommended)", "yomi")
        parser.addItem("MeCab (local, optional)", "mecab_local")
        parser.setToolTip(
            "Choose the automatic Japanese parser. Local MeCab requires "
            "fugashi and a dictionary installed separately."
        )
        parser.setCurrentIndex(max(0, parser.findData(
            values.get("furigana_parser", "yomi")
        )))
        controls["furigana_parser"] = parser
        self._add_labeled(layout, "Automatic parser:", parser)

        limited_rendering = QCheckBox("Limited reading rendering")
        limited_rendering.setChecked(bool(values.get("limited_reading_rendering", False)))
        limited_rendering.setToolTip(
            "Only render furigana/pinyin for lyrics near the current line. "
            "This can improve performance on long lyrics."
        )
        controls["limited_reading_rendering"] = limited_rendering
        layout.addWidget(limited_rendering)

        reading_range = self._add_slider(
            layout, "Nearby lyrics:",
            values.get("limited_reading_range", 10), 5, 15, " lines"
        )
        controls["limited_reading_range"] = reading_range

        # Keep the range control explicitly synchronized with the checkbox.
        # Using stateChanged here is more robust than relying on the overloaded
        # toggled signal when this dialog is rebuilt/restored by Qt.
        def _sync_limited_reading_range(state):
            reading_range.setEnabled(bool(state))

        limited_rendering.stateChanged.connect(_sync_limited_reading_range)
        _sync_limited_reading_range(limited_rendering.checkState())

        fade_readings = QCheckBox("Fade readings")
        fade_readings.setChecked(bool(values.get("fade_in_readings", True)))
        fade_readings.setToolTip(
            "Smoothly fade furigana or pinyin in and out as they enter or leave the rendered range."
        )
        controls["fade_in_readings"] = fade_readings
        layout.addWidget(fade_readings)

        # Fading only has an effect when readings are rendered on demand.
        def _sync_fade_readings(state):
            fade_readings.setEnabled(bool(state))

        limited_rendering.stateChanged.connect(_sync_fade_readings)
        _sync_fade_readings(limited_rendering.checkState())

        ruby_padding = self._add_slider(
            layout, "Ruby padding:",
            values.get("ruby_padding", 2), 0, 18, "px"
        )
        controls["ruby_padding"] = ruby_padding

        ruby_font_size = QSpinBox()
        ruby_font_size.setRange(6, 72)
        ruby_font_size.setValue(int(values.get("ruby_font_size", 18)))
        ruby_font_size.setSuffix(" pt")
        controls["ruby_font_size"] = ruby_font_size
        self._add_labeled(layout, "Ruby text font size:", ruby_font_size)

        self._add_section_heading(layout, "Typography")
        font_family = QComboBox()
        font_family.setEditable(False)
        font_family.addItem("System default", "")
        for family in QFontDatabase.families():
            font_family.addItem(family, family)
        index = font_family.findData(values.get("font_family", ""))
        font_family.setCurrentIndex(index if index >= 0 else 0)
        controls["font_family"] = font_family
        self._add_labeled(layout, "Font family:", font_family)

        font_size = QSpinBox()
        font_size.setRange(10, 96)
        font_size.setValue(int(values.get("font_size", 32)))
        font_size.setSuffix(" pt")
        controls["font_size"] = font_size
        self._add_labeled(layout, "Font size:", font_size)

        self._add_section_heading(layout, "Highlighting")
        highlight_current = QCheckBox("Highlight current lyric")
        highlight_current.setChecked(bool(values.get("highlight_current", True)))
        controls["highlight_current"] = highlight_current
        layout.addWidget(highlight_current)
        if self.plugin_manager is not None and "karaoke" in self.plugin_manager.plugins:
            karaoke_notice = QLabel(
                "Note: Current lyric highlighting is automatically ignored "
                "while karaoke timing is active."
            )
            karaoke_notice.setWordWrap(True)
            karaoke_notice.setStyleSheet("color: palette(mid); font-style: italic;")
            layout.addWidget(karaoke_notice)

        highlight_animation = QComboBox()
        highlight_animation.addItem("None", "none")
        highlight_animation.addItem("Fade", "fade")
        highlight_animation.addItem("Pop", "pop")
        highlight_animation.addItem("Scale", "scale")
        highlight_animation.addItem("Slide", "slide")
        highlight_animation.setCurrentIndex(
            max(
                0,
                highlight_animation.findData(
                    values.get("highlight_animation", "none")
                ),
            )
        )
        controls["highlight_animation"] = highlight_animation
        self._add_labeled(
            layout,
            "Highlight animation:",
            highlight_animation,
        )

        highlight_duration = self._add_slider(
            layout,
            "Animation in duration:",
            values.get("highlight_animation_duration", 300),
            0,
            2000,
            "ms",
        )
        controls["highlight_animation_duration"] = highlight_duration

        highlight_out_duration = self._add_slider(
            layout,
            "Animation out duration:",
            values.get("highlight_animation_out_duration", 300),
            0,
            2000,
            "ms",
        )
        controls["highlight_animation_out_duration"] = (
            highlight_out_duration
        )

        highlight_easing = QComboBox()
        for text, data in (
            ("Linear", "linear"),
            ("In Quad", "in_quad"),
            ("Out Quad", "out_quad"),
            ("In-Out Quad", "in_out_quad"),
            ("In Cubic", "in_cubic"),
            ("Out Cubic", "out_cubic"),
            ("In-Out Cubic", "in_out_cubic"),
            ("Out Back", "out_back"),
            ("Out Bounce", "out_bounce"),
        ):
            highlight_easing.addItem(text, data)
        easing_index = highlight_easing.findData(
            values.get("highlight_animation_easing", "out_cubic")
        )
        highlight_easing.setCurrentIndex(
            easing_index if easing_index >= 0 else 0
        )
        controls["highlight_animation_easing"] = highlight_easing
        self._add_labeled(
            layout,
            "Highlight animation easing:",
            highlight_easing,
        )

        highlight_color = self._add_color_button(
            layout, "Current lyric color:",
            values.get("highlight_color", "#ffaa00")
        )
        controls["highlight_color"] = highlight_color

        lyrics_color = self._add_color_button(
            layout, "Lyrics color:",
            values.get("lyrics_color", "#ffffff")
        )
        controls["lyrics_color"] = lyrics_color

        self._add_section_heading(layout, "Scrolling")
        scrolling_mode = QComboBox()
        scrolling_mode.addItem("Automatic (timestamp-driven)", "automatic")
        scrolling_mode.addItem("Continuous (independent of timestamps)", "continuous")
        scrolling_mode.setCurrentIndex(
            max(0, scrolling_mode.findData(
                values.get("scrolling_mode", "automatic")
            ))
        )
        controls["scrolling_mode"] = scrolling_mode
        self._add_labeled(layout, "Scrolling mode:", scrolling_mode)

        current_suffix = ""
        if self.current_file:
            try:
                current_suffix = str(self.current_file).lower().rsplit(".", 1)[-1]
            except Exception:
                current_suffix = ""
        if current_suffix == "txt":
            continuous_index = scrolling_mode.findData("continuous")
            if continuous_index >= 0:
                scrolling_mode.setCurrentIndex(continuous_index)
            scrolling_mode.setProperty("_forced_continuous", True)
            scrolling_mode.setEnabled(False)
            txt_notice = QLabel(
                "TXT lyrics always use Continuous scrolling until converted to timed lyrics."
            )
            txt_notice.setWordWrap(True)
            txt_notice.setStyleSheet("color: palette(mid); font-style: italic;")
            layout.addWidget(txt_notice)

        continuous_group = QGroupBox("Continuous scrolling settings")
        continuous_layout = QVBoxLayout(continuous_group)
        continuous_layout.setContentsMargins(12, 16, 12, 10)

        duration_based = QCheckBox("Calculate speed based on song duration")
        duration_based.setChecked(bool(values.get("continuous_scroll_duration_based", True)))
        controls["continuous_scroll_duration_based"] = duration_based
        continuous_layout.addWidget(duration_based)

        speed_row = QHBoxLayout()
        speed_label = QLabel("Continuous scroll speed:")
        continuous_speed = QSlider(Qt.Orientation.Horizontal)
        continuous_speed.setRange(1, 500)
        continuous_speed.setValue(max(1, min(500, int(values.get("continuous_scroll_speed", 35)))))
        speed_value = QLabel(f"{continuous_speed.value()} px/s")
        speed_value.setFixedWidth(55)
        continuous_speed.valueChanged.connect(lambda v: speed_value.setText(f"{v} px/s"))
        speed_row.addWidget(speed_label)
        speed_row.addWidget(continuous_speed, 1)
        speed_row.addWidget(speed_value)
        continuous_layout.addLayout(speed_row)
        controls["continuous_scroll_speed"] = continuous_speed
        controls["continuous_scroll_speed_label"] = speed_label
        controls["continuous_scroll_speed_value"] = speed_value

        nudge_current = QCheckBox("Nudge to the center of current lyric when out of view")
        nudge_current.setChecked(bool(values.get("continuous_scroll_nudge_current", True)))
        controls["continuous_scroll_nudge_current"] = nudge_current
        continuous_layout.addWidget(nudge_current)
        layout.addWidget(continuous_group)
        controls["continuous_group"] = continuous_group

        automatic_group = QGroupBox("Automatic scrolling settings")
        automatic_layout = QVBoxLayout(automatic_group)
        automatic_layout.setContentsMargins(12, 16, 12, 10)
        layout.addWidget(automatic_group)
        controls["automatic_group"] = automatic_group

        animation = self._add_slider(
            automatic_layout, "Scroll animation speed:",
            values.get("scroll_animation_speed", 300), 0, 1000, "ms"
        )
        controls["scroll_animation_speed"] = animation

        easing = QComboBox()
        for label, value in (
            ("Linear", "linear"),
            ("In / Out Quad", "in_out_quad"),
            ("Out Cubic", "out_cubic"),
            ("Out Quart", "out_quart"),
            ("Out Expo", "out_expo"),
        ):
            easing.addItem(label, value)
        easing.setCurrentIndex(max(0, easing.findData(values.get("scroll_animation_easing", "out_cubic"))))
        controls["scroll_animation_easing"] = easing
        self._add_labeled(automatic_layout, "Scroll easing:", easing)

        behavior = QComboBox()
        behavior.addItem("Center current lyric", "center")
        behavior.addItem("Focus on current lyric", "focus_current")
        behavior.addItem("Keep current lyric visible", "ensure_visible")
        behavior.addItem("Do not auto-scroll", "off")
        behavior.setCurrentIndex(
            max(0, behavior.findData(values.get("scroll_behavior", "center")))
        )
        controls["scroll_behavior"] = behavior
        self._add_labeled(automatic_layout, "Scrolling behavior:", behavior)

        display.currentIndexChanged.connect(
            lambda *_: self._update_compact_lyrics_controls(controls)
        )
        scrolling_mode.currentIndexChanged.connect(
            lambda *_: self._update_compact_lyrics_controls(controls)
        )
        duration_based.toggled.connect(
            lambda *_: self._update_compact_lyrics_controls(controls)
        )
        self._update_compact_lyrics_controls(controls)


        return controls

    def get_disabled_plugins(self):
        return {
            plugin_id
            for plugin_id, toggle in self.plugin_toggles.items()
            if not toggle.isChecked()
        }

    def _add_section_heading(self, layout, title):
        heading = QLabel(title)
        heading.setStyleSheet(
            "font-weight: bold; font-size: 14px; margin-top: 10px;"
        )
        layout.addWidget(heading)

    def _add_labeled(self, layout, title, widget):
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        row.addWidget(widget, 1)
        layout.addLayout(row)

    def _add_slider(self, layout, title, value, minimum, maximum, suffix):
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(
            max(minimum, min(maximum, int(value)))
        )
        label = QLabel(f"{slider.value()} {suffix}")
        label.setFixedWidth(55)
        slider.valueChanged.connect(
            lambda current, label=label, suffix=suffix:
            label.setText(f"{current} {suffix}")
        )
        row.addWidget(slider, 1)
        row.addWidget(label)
        layout.addLayout(row)
        return slider

    def _add_color_button(self, layout, title, value):
        button = QPushButton()
        button.setText(str(value))
        button.setStyleSheet(
            f"QPushButton {{ text-align: left; background: {value}; }}"
        )

        def choose():
            color = QColorDialog.getColor()
            if not color.isValid():
                return
            hex_color = color.name()
            button.setText(hex_color)
            button.setStyleSheet(
                f"QPushButton {{ text-align: left; background: {hex_color}; }}"
            )

        button.clicked.connect(choose)
        self._add_labeled(layout, title, button)
        return button

    def _local_setting_name(self, key):
        names = {
            "scrolling_lyrics": "Display mode:", "lyrics_direction": "Lyrics direction:",
            "lyrics_padding": "Lyrics padding:", "vertical_padding": "Vertical padding:", "show_ruby": "Show furigana:",
            "ruby_padding": "Ruby padding:", "ruby_font_size": "Ruby font size:",
            "font_family": "Font family:", "font_size": "Font size:",
            "lyrics_alignment": "Lyrics alignment:", "highlight_current": "Highlight current lyric:",
            "highlight_animation": "Highlight animation:",
            "highlight_animation_duration": "Animation in duration:",
            "highlight_animation_out_duration": "Animation out duration:",
            "highlight_animation_easing": "Highlight animation easing:",
            "highlight_color": "Current lyric color:", "lyrics_color": "Lyrics color:",
            "scrolling_mode": "Scrolling mode:", "continuous_scroll_speed": "Continuous scroll speed:",
            "continuous_scroll_duration_based": "Calculate speed based on song duration:",
            "continuous_scroll_nudge_current": "Nudge current lyric into view:",
            "scroll_animation_speed": "Scroll animation speed:",
            "scroll_animation_easing": "Scroll easing:", "scroll_behavior": "Scrolling behavior:",
        }
        return names.get(key, key.replace("_", " ").capitalize() + ":")

    def _create_local_controls(self, global_values, overrides, layout):
        controls = {}

        # Same set as global, but every row can be inherited or overridden.
        sections = {
            "scrolling_lyrics": "Display",
            "show_ruby": "Furigana",
            "font_family": "Typography",
            "highlight_current": "Highlighting",
            "scroll_animation_speed": "Scrolling",
        }
        for key in LOCAL_LYRIC_KEYS:
            if key in sections:
                self._add_section_heading(layout, sections[key])
            if key == "highlight_color" and self.plugin_manager is not None and "karaoke" in self.plugin_manager.plugins:
                notice = QLabel(
                    "Karaoke timing uses its own progressive highlighting "
                    "and ignores current lyric highlighting."
                )
                notice.setWordWrap(True)
                notice.setStyleSheet("color: palette(mid); font-style: italic;")
                layout.addWidget(notice)
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            setting_name = QLabel(self._local_setting_name(key))
            setting_name.setMinimumWidth(185)
            row.addWidget(setting_name)

            enabled = QCheckBox("Override")
            enabled.setChecked(key in overrides)

            value = overrides.get(key, global_values.get(key))

            editor = self._editor_for_key(key, value)
            label = self._editor_label(key, editor)

            if label is not None:
                row.addWidget(label)

            def sync(state, widget=editor):
                widget.setEnabled(bool(state))

            enabled.toggled.connect(sync)
            sync(enabled.isChecked())

            row.addWidget(enabled)
            if label is None:
                row.addWidget(editor, 1)
            else:
                row.addWidget(editor, 1)

            layout.addWidget(row_widget)
            controls[key] = (enabled, editor)

        return controls

    def _editor_for_key(self, key, value):
        if key == "scrolling_lyrics":
            editor = QComboBox()
            editor.addItem("Compact", False)
            editor.addItem("Scrolling", True)
            editor.setCurrentIndex(editor.findData(bool(value)))
            return editor
        if key == "scrolling_mode":
            editor = QComboBox()
            editor.addItem("Automatic (timestamp-driven)", "automatic")
            editor.addItem("Continuous (independent of timestamps)", "continuous")
            index = editor.findData(str(value).lower())
            editor.setCurrentIndex(index if index >= 0 else 0)
            return editor
        if key == "continuous_scroll_speed":
            editor = QSlider(Qt.Orientation.Horizontal)
            editor.setRange(1, 500)
            editor.setValue(max(1, min(500, int(value))))
            return editor
        if key == "lyrics_direction":
            editor = QComboBox()
            for text, data in (
                ("Horizontal", "horizontal"),
                ("Vertical", "vertical"),
                ("Auto (CJK-aware)", "auto"),
            ):
                editor.addItem(text, data)
            index = editor.findData(value)
            editor.setCurrentIndex(index if index >= 0 else 0)
            return editor
        if key == "lyrics_alignment":
            editor = QComboBox()
            for text, data in (("Left","left"),("Center","center"),("Right","right")):
                editor.addItem(text, data)
            index = editor.findData(value)
            editor.setCurrentIndex(index if index >= 0 else 1)
            return editor
        if key == "show_ruby" or key == "highlight_current":
            editor = QCheckBox()
            editor.setChecked(bool(value))
            return editor
        if key in {"font_family"}:
            editor = QComboBox()
            editor.addItem("System default", "")
            for family in QFontDatabase.families():
                editor.addItem(family, family)
            index = editor.findData(value)
            editor.setCurrentIndex(index if index >= 0 else 0)
            return editor
        if key in {"font_size", "ruby_font_size"}:
            editor = QSpinBox()
            if key == "ruby_font_size":
                editor.setRange(6, 72)
            else:
                editor.setRange(10, 96)
            editor.setValue(int(value))
            editor.setSuffix(" pt")
            return editor
        if key in {"lyrics_padding", "vertical_padding"}:
            editor = QSlider(Qt.Orientation.Horizontal)
            editor.setRange(0, 24)
            editor.setValue(int(value))
            return editor
        if key in {"ruby_padding"}:
            editor = QSlider(Qt.Orientation.Horizontal)
            editor.setRange(0, 18)
            editor.setValue(int(value))
            return editor
        if key in {"scroll_animation_speed"}:
            editor = QSlider(Qt.Orientation.Horizontal)
            editor.setRange(0, 1000)
            editor.setValue(int(value))
            return editor
        if key == "highlight_animation_easing":
            editor = QComboBox()
            for text, data in (
                ("Linear", "linear"),
                ("In Quad", "in_quad"),
                ("Out Quad", "out_quad"),
                ("In-Out Quad", "in_out_quad"),
                ("In Cubic", "in_cubic"),
                ("Out Cubic", "out_cubic"),
                ("In-Out Cubic", "in_out_cubic"),
                ("Out Back", "out_back"),
                ("Out Bounce", "out_bounce"),
            ):
                editor.addItem(text, data)
            index = editor.findData(value)
            editor.setCurrentIndex(index if index >= 0 else 0)
            return editor
        if key == "highlight_animation":
            editor = QComboBox()
            for text, data in (
                ("None", "none"),
                ("Fade", "fade"),
                ("Pop", "pop"),
                ("Scale", "scale"),
                ("Slide", "slide"),
            ):
                editor.addItem(text, data)
            index = editor.findData(value)
            editor.setCurrentIndex(index if index >= 0 else 0)
            return editor
        if key in {"highlight_animation_duration"}:
            editor = QSlider(Qt.Orientation.Horizontal)
            editor.setRange(0, 2000)
            editor.setValue(int(value))
            return editor
        if key in {"highlight_color", "lyrics_color"}:
            editor = QPushButton(str(value))
            def choose():
                color = QColorDialog.getColor()
                if color.isValid():
                    color_name = color.name()
                    editor.setText(color_name)
                    editor.setStyleSheet(
                        f"QPushButton {{ text-align: left; background: {color_name}; }}"
                    )
            editor.clicked.connect(choose)
            editor.setStyleSheet(
                f"QPushButton {{ text-align: left; background: {value}; }}"
            )
            return editor
        if key == "scroll_behavior":
            editor = QComboBox()
            for text, data in (
                ("Center current lyric", "center"),
                ("Keep current lyric visible", "ensure_visible"),
                ("Do not auto-scroll", "off"),
            ):
                editor.addItem(text, data)
            index = editor.findData(value)
            editor.setCurrentIndex(index if index >= 0 else 0)
            return editor
        return QLineEdit(str(value))

    def _editor_label(self, key, editor):
        if key in {"lyrics_padding", "vertical_padding", "ruby_padding", "continuous_scroll_speed"}:
            label = QLabel("0 px/s" if key == "continuous_scroll_speed" else "0 px")
            label.setFixedWidth(45)
            slider = editor
            suffix = "px/s" if key == "continuous_scroll_speed" else "px"
            label.setText(f"{slider.value()} {suffix}")
            slider.valueChanged.connect(
                lambda v, label=label, suffix=suffix: label.setText(f"{v} {suffix}")
            )
            return label
        if (
            key in {
                "scroll_animation_speed",
                "highlight_animation_duration",
                "highlight_animation_out_duration",
            }
            and isinstance(editor, QSlider)
        ):
            label = QLabel(f"{editor.value()} ms")
            label.setFixedWidth(55)
            editor.valueChanged.connect(
                lambda v, label=label: label.setText(f"{v} ms")
            )
            return label
        return None

    def _read_value(self, key, control):
        if key == "scrolling_lyrics":
            return control.currentData()
        if key in {"show_ruby", "highlight_current", "continuous_scroll_duration_based", "continuous_scroll_nudge_current", "limited_reading_rendering", "fade_in_readings"}:
            return control.isChecked()
        if key in {
            "lyrics_direction",
            "scrolling_mode",
            "furigana_parser",
            "lyrics_alignment",
            "ruby_position",
            "scroll_behavior",
            "font_family",
            "highlight_animation",
            "highlight_animation_easing",
        }:
            return control.currentData()
        if key in {"highlight_color", "lyrics_color"}:
            return control.text().strip()
        return control.value() if hasattr(control, "value") else control.currentText()

    def get_startup_settings(self):
        return {
            "remember_window": self.remember_window_checkbox.isChecked(),
            "restore_playlist": self.restore_playlist_checkbox.isChecked(),
        }


    def get_appearance_settings(self):
        return {
            "background_enabled": self.background_enabled_checkbox.isChecked(),
            "artwork_source": self.artwork_source_combo.currentData(),
            "booru_tags": self.booru_tags_edit.text().strip() or "scenery",
            "background_blur_mode": self.background_blur_mode_combo.currentData(),
            "background_blur_bleed": (
                self.background_blur_bleed_checkbox.isChecked()
            ),
            "background_blur": self.background_blur_slider.value(),
            "background_darkness": self.background_darkness_slider.value(),
            "empty_timestamp_placeholder_enabled": (
                self.empty_timestamp_placeholder_checkbox.isChecked()
            ),
            "empty_timestamp_placeholder": (
                self.empty_timestamp_placeholder_edit.text().strip()
                or "♪ {countdown}"
            ),
        }


    def get_playback_settings(self):
        return {
            "precise_timestamp_fps": int(
                self.precise_timestamp_fps.currentData()
            )
        }

    def get_karaoke_settings(self):
        return {
            "sweep_fps": int(self.karaoke_sweep_fps.currentData()),
            # Sweep style is now edited from Lyrics Editor > Karaoke.
            # Preserve the existing saved value when the general Settings
            # dialog is saved.
            "sweep_style": self.karaoke_settings.get("sweep_style", "classic"),
        }

    def get_global_lyrics_settings(self):
        return {
            key: self._read_value(key, self.global_controls[key])
            for key in LYRIC_KEYS
        }

    def get_local_lyrics_overrides(self):
        overrides = {}
        for key, (enabled, editor) in self.local_controls.items():
            if enabled.isChecked():
                overrides[key] = self._read_value(key, editor)
        return overrides

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Lyrics Folder", "")
        if not folder:
            return
        if not any(
            self.folder_list.item(row).text() == folder
            for row in range(self.folder_list.count())
        ):
            self.folder_list.addItem(QListWidgetItem(folder))

    def remove_folder(self):
        current_row = self.folder_list.currentRow()
        if current_row >= 0:
            self.folder_list.takeItem(current_row)

    def get_folders(self):
        return [
            self.folder_list.item(row).text()
            for row in range(self.folder_list.count())
        ]
