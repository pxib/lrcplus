import json
from pathlib import Path

from PySide6.QtCore import QDateTime, QLocale, QPoint, Qt, Signal
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPolygon
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
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
    QGridLayout,
    QToolButton,
    QInputDialog,
    QMessageBox,
)
from core.settings import APPEARANCE_DEFAULTS, KARAOKE_SWEEP_STYLES, get_karaoke_settings
from core.theme import build_theme_palette, normalize_theme_overrides, THEME_COLOR_FIELDS
from ui.dependency_settings import DependencyManagerWidget
from ui.about_tab import AboutTab
from ui.settings_search import SettingsSearch
from ui.settings_controls import (
    ThemedSpinBox,
    ThemedComboBox,
    QComboBox,
    _SpinStepButton,
    _ComboDropButton,
    SETTINGS_DIALOG_STYLE,
    _apply_settings_dialog_style,
)


LYRIC_KEYS = (
    "scrolling_lyrics",
    "lyrics_direction",
    "lyrics_padding",
    "vertical_padding",
    "show_ruby",
    "show_romaji",
    "all_romaji",
    "ruby_position",
    "furigana_parser",
    "mecab_parse_mode",
    "limited_reading_rendering",
    "limited_reading_range",
    "fade_in_readings",
    "ruby_padding",
    "ruby_font_size",
    "font_family",
    "font_size",
    "lyrics_alignment",
    "focus_transition",
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


def _load_theme_color_presets():
    """Read named accent colors from the repository's shared theme reference."""
    path = Path(__file__).resolve().parents[1] / "themedata" / "optionalthemes.json"
    try:
        with path.open(encoding="utf-8") as theme_file:
            themes = json.load(theme_file).get("themes", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [
        (theme["name"], theme)
        for theme in themes
        if isinstance(theme, dict)
        and isinstance(theme.get("name"), str)
        and all(isinstance(theme.get(key), str) for key in (
            "background", "surface", "accent", "text", "secondaryText"
        ))
    ]


def _blend_theme_colors(first, second, amount):
    first, second = QColor(first), QColor(second)
    return QColor(
        round(first.red() * (1 - amount) + second.red() * amount),
        round(first.green() * (1 - amount) + second.green() * amount),
        round(first.blue() * (1 - amount) + second.blue() * amount),
    ).name()


def _valid_theme_definition(theme):
    if not isinstance(theme, dict):
        return None
    name = str(theme.get("name", "")).strip()
    fields = ("background", "surface", "accent", "text", "secondaryText")
    colors = {key: theme.get(key) for key in fields}
    if not name or any(
        not isinstance(value, str) or not QColor(value).isValid()
        or not value.startswith("#") or len(value) != 7
        for value in colors.values()
    ):
        return None
    return {
        "name": name,
        "description": str(theme.get("description", "Custom theme")),
        "advancedColors": normalize_theme_overrides(theme.get("advancedColors", {})),
        **{key: QColor(value).name() for key, value in colors.items()},
    }

# Shared by global controls and per-file overrides so both scopes stay organized.
LYRIC_SECTIONS = {
    "Display": (
        "scrolling_lyrics", "lyrics_direction", "lyrics_alignment",
        "lyrics_padding", "vertical_padding", "focus_transition",
    ),
    "Readings": (
        "show_ruby", "show_romaji", "all_romaji", "ruby_position",
        "furigana_parser", "mecab_parse_mode", "limited_reading_rendering",
        "limited_reading_range", "fade_in_readings", "ruby_padding", "ruby_font_size",
    ),
    "Typography": ("font_family", "font_size", "lyrics_color"),
    "Highlighting": (
        "highlight_current", "highlight_color", "highlight_animation",
        "highlight_animation_duration", "highlight_animation_out_duration",
        "highlight_animation_easing",
    ),
    "Scrolling": (
        "scrolling_mode", "continuous_scroll_duration_based", "continuous_scroll_speed",
        "continuous_scroll_nudge_current", "scroll_animation_speed",
        "scroll_animation_easing", "scroll_behavior",
    ),
}

# Parser selection is an application-wide engine choice, not a per-file lyric override.
LOCAL_LYRIC_KEYS = tuple(key for key in LYRIC_KEYS if key not in {"furigana_parser", "mecab_parse_mode"})


class PluginSettingsDialog(QDialog):
    """Standalone settings window for one plugin's registered settings."""

    def __init__(self, plugin_manager, plugin_id, plugin_name, parent=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.plugin_id = str(plugin_id)
        theme_color = getattr(
            parent,
            "theme_color",
            APPEARANCE_DEFAULTS["theme_color"],
        )
        theme_mode = getattr(parent, "theme_mode", "light")
        _apply_settings_dialog_style(self, theme_mode, theme_color, getattr(parent, "theme_surface", "classic"), getattr(parent, "theme_overrides", {}))
        self.setWindowTitle(f"{plugin_name} Settings")
        self.resize(520, 460)

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 12)

        self._instances = []
        entries = [
            entry for entry in plugin_manager.get_settings_widgets()
            if entry.get("owner") == self.plugin_id
        ]
        for entry in entries:
            title = entry.get("title", "Settings")
            self._add_section_heading(body_layout, title)
            if entry.get("description"):
                description = QLabel(entry["description"])
                description.setWordWrap(True)
                description.setStyleSheet(
                    "color: #A0A0A0; font-style: italic;"
                )
                body_layout.addWidget(description)
            try:
                widget = entry["widget_factory"](body)
                if widget is None:
                    continue
                body_layout.addWidget(widget)
                self._instances.append((entry, widget))
            except Exception as error:
                label = QLabel(
                    f"Failed to load settings for {title}: {error}"
                )
                label.setWordWrap(True)
                label.setStyleSheet("color: #D08080;")
                body_layout.addWidget(label)

        body_layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _add_section_heading(layout, text):
        heading = QLabel(f"<b>{text}</b>")
        layout.addWidget(heading)

    def _save(self):
        for entry, widget in self._instances:
            callback = entry.get("apply")
            if callback is None:
                continue
            try:
                callback(widget)
            except Exception as error:
                self.plugin_manager.log(
                    self.plugin_id,
                    f"settings apply failed: {error!r}",
                )
                return
        self.accept()


class SettingsDialog(QDialog):
    check_updates_requested = Signal()

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
        update_settings=None,
    ):
        super().__init__(parent)

        self.global_lyrics = dict(global_lyrics or {})
        self.startup_settings = dict(startup_settings or {})
        self.appearance_settings = dict(appearance_settings or {})
        self.custom_themes = [
            valid for theme in self.appearance_settings.get("custom_themes", [])
            if (valid := _valid_theme_definition(theme)) is not None
        ]
        self.theme_preset_name = self.appearance_settings.get("theme_preset_name")
        self.theme_overrides = normalize_theme_overrides(self.appearance_settings.get("theme_overrides"))
        self.theme_surface = self.appearance_settings.get("theme_surface", "classic")
        if self.theme_surface not in {"classic", "neutral"}:
            self.theme_surface = "classic"
        self.theme_mode = self.appearance_settings.get("theme_mode", "light")
        if self.theme_mode not in {"light", "dark"}:
            self.theme_mode = "light"
        self.theme_color = str(
            self.appearance_settings.get(
                "theme_color",
                APPEARANCE_DEFAULTS["theme_color"],
            )
        )
        if not QColor(self.theme_color).isValid():
            self.theme_color = APPEARANCE_DEFAULTS["theme_color"]
        self.theme_color = QColor(self.theme_color).name()
        self.playback_settings = dict(playback_settings or {})
        self.karaoke_settings = get_karaoke_settings({"karaoke": karaoke_settings or {}})
        self.local_overrides = dict(local_overrides or {})
        self.current_file = current_file
        self.plugin_manager = plugin_manager
        self.update_settings = dict(update_settings or {})
        self.plugin_toggles = {}

        _apply_settings_dialog_style(
            self, self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
        )
        self.setWindowTitle("Settings")
        self.resize(880, 640)

        tabs = QTabWidget()
        self.category_pages = tabs
        tabs.tabBar().hide()

        # ===== GENERAL =====
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)

        self._add_section_heading(general_layout, "Startup")

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

        self._add_section_heading(general_layout, "Updates")
        self.automatic_update_checkbox = QCheckBox(
            "Check for updates automatically"
        )
        self.automatic_update_checkbox.setChecked(
            bool(self.update_settings.get("automatic_check", True))
        )
        general_layout.addWidget(self.automatic_update_checkbox)

        update_channel_row = QHBoxLayout()
        update_channel_row.addWidget(QLabel("Update channel"))
        self.update_channel_combo = QComboBox()
        self.update_channel_combo.addItem("Stable", "stable")
        self.update_channel_combo.addItem("Prerelease", "prerelease")
        channel = self.update_settings.get("channel", "stable")
        self.update_channel_combo.setCurrentIndex(
            max(0, self.update_channel_combo.findData(channel))
        )
        update_channel_row.addWidget(self.update_channel_combo)
        update_channel_row.addStretch(1)
        general_layout.addLayout(update_channel_row)
        prerelease_note = QLabel(
            "Prerelease builds are experimental and may contain bugs."
        )
        prerelease_note.setWordWrap(True)
        general_layout.addWidget(prerelease_note)

        update_actions = QHBoxLayout()
        self.check_updates_button = QPushButton("Check for Updates")
        self.check_updates_button.clicked.connect(
            self.check_updates_requested.emit
        )
        update_actions.addWidget(self.check_updates_button)
        last_checked = str(self.update_settings.get("last_checked", "") or "")
        if last_checked:
            checked_at = QDateTime.fromString(
                last_checked, Qt.DateFormat.ISODateWithMs
            )
            if not checked_at.isValid():
                checked_at = QDateTime.fromString(
                    last_checked, Qt.DateFormat.ISODate
                )
            last_checked_text = (
                QLocale().toString(checked_at.toLocalTime(), QLocale.FormatType.ShortFormat)
                if checked_at.isValid() else "Unknown"
            )
        else:
            last_checked_text = "Never"
        self.last_checked_label = QLabel(f"Last checked: {last_checked_text}")
        self.last_checked_label.setWordWrap(True)
        update_actions.addWidget(self.last_checked_label, 1)
        general_layout.addLayout(update_actions)

        general_layout.addStretch()

        # ===== APPEARANCE =====
        appearance_tab = QWidget()
        appearance_layout = QVBoxLayout(appearance_tab)

        self._add_section_heading(appearance_layout, "Theme")
        theme_mode_row = QHBoxLayout()
        theme_mode_row.addWidget(QLabel("Color mode"))
        self.light_theme_button = QPushButton("Light")
        self.dark_theme_button = QPushButton("Dark")
        for button in (self.light_theme_button, self.dark_theme_button):
            button.setObjectName("themeModeButton")
            button.setCheckable(True)
        self.theme_mode_group = QButtonGroup(self)
        self.theme_mode_group.setExclusive(True)
        self.theme_mode_group.addButton(self.light_theme_button)
        self.theme_mode_group.addButton(self.dark_theme_button)
        self.light_theme_button.setChecked(self.theme_mode == "light")
        self.dark_theme_button.setChecked(self.theme_mode == "dark")
        self.light_theme_button.clicked.connect(
            lambda _checked=False: self._set_theme_mode("light")
        )
        self.dark_theme_button.clicked.connect(
            lambda _checked=False: self._set_theme_mode("dark")
        )
        theme_mode_row.addWidget(self.light_theme_button)
        theme_mode_row.addWidget(self.dark_theme_button)
        theme_mode_row.addStretch(1)
        appearance_layout.addLayout(theme_mode_row)

        surface_row = QHBoxLayout()
        surface_row.addWidget(QLabel("Surface palette"))
        self.theme_surface_combo = QComboBox()
        self.theme_surface_combo.addItem("Classic", "classic")
        self.theme_surface_combo.addItem("Neutral", "neutral")
        self.theme_surface_combo.setCurrentIndex(self.theme_surface_combo.findData(self.theme_surface))
        self.theme_surface_combo.currentIndexChanged.connect(self._change_theme_surface)
        surface_row.addWidget(self.theme_surface_combo, 1)
        appearance_layout.addLayout(surface_row)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Theme presets"))
        self.theme_preset_combo = QComboBox()
        self._populate_theme_preset_combo()
        self.theme_preset_combo.activated.connect(self._choose_theme_preset)
        preset_row.addWidget(self.theme_preset_combo, 1)
        self.save_theme_button = QPushButton("Save current")
        self.save_theme_button.clicked.connect(self._save_current_theme)
        preset_row.addWidget(self.save_theme_button)
        appearance_layout.addLayout(preset_row)

        theme_actions_row = QHBoxLayout()
        self.import_theme_button = QPushButton("Import…")
        self.import_theme_button.clicked.connect(self._import_theme)
        self.export_theme_button = QPushButton("Export…")
        self.export_theme_button.clicked.connect(self._export_theme)
        self.delete_theme_button = QPushButton("Delete saved theme")
        self.delete_theme_button.clicked.connect(self._delete_saved_theme)
        theme_actions_row.addWidget(self.import_theme_button)
        theme_actions_row.addWidget(self.export_theme_button)
        theme_actions_row.addWidget(self.delete_theme_button)
        appearance_layout.addLayout(theme_actions_row)
        if self.theme_preset_name:
            saved_index = self.theme_preset_combo.findText(self.theme_preset_name)
            if saved_index >= 0:
                self.theme_preset_combo.setCurrentIndex(saved_index)
        self._update_theme_management_controls()

        theme_color_row = QHBoxLayout()
        theme_color_row.addWidget(QLabel("Accent color"))
        self.theme_color_button = QPushButton()
        self.theme_color_button.setMinimumWidth(120)
        self.theme_color_button.clicked.connect(self._choose_theme_color)
        theme_color_row.addWidget(self.theme_color_button, 1)
        appearance_layout.addLayout(theme_color_row)
        self._update_theme_color_button()

        self.theme_simple_color_buttons = {}
        self.theme_simple_reset_buttons = {}
        for key, label in (("surface", "Surface color"), ("window", "Background color")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            color_button = QPushButton()
            color_button.clicked.connect(lambda _checked=False, role=key: self._choose_theme_color_for_role(role))
            row.addWidget(color_button, 1)
            reset_button = QPushButton("Reset")
            reset_button.clicked.connect(lambda _checked=False, role=key: self._reset_theme_color_for_role(role))
            row.addWidget(reset_button)
            self.theme_simple_color_buttons[key] = color_button
            self.theme_simple_reset_buttons[key] = reset_button
            appearance_layout.addLayout(row)

        self.advanced_theme_toggle = QPushButton("Show advanced colors")
        self.advanced_theme_toggle.setCheckable(True)
        appearance_layout.addWidget(self.advanced_theme_toggle)
        self.advanced_theme_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_theme_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(QLabel("Customize individual colors for the selected light or dark mode."))
        advanced_row = QHBoxLayout()
        self.theme_role_combo = QComboBox()
        for key, label in THEME_COLOR_FIELDS.items():
            self.theme_role_combo.addItem(label, key)
        self.theme_role_combo.currentIndexChanged.connect(self._update_advanced_theme_controls)
        advanced_row.addWidget(self.theme_role_combo, 1)
        self.theme_role_color_button = QPushButton()
        self.theme_role_color_button.clicked.connect(self._choose_theme_role_color)
        advanced_row.addWidget(self.theme_role_color_button)
        self.theme_role_reset_button = QPushButton("Reset color")
        self.theme_role_reset_button.clicked.connect(self._reset_theme_role_color)
        advanced_row.addWidget(self.theme_role_reset_button)
        advanced_layout.addLayout(advanced_row)
        self.theme_reset_mode_button = QPushButton("Reset all custom colors for this mode")
        self.theme_reset_mode_button.clicked.connect(self._reset_theme_mode_colors)
        advanced_layout.addWidget(self.theme_reset_mode_button)
        appearance_layout.addWidget(self.advanced_theme_panel)
        self.advanced_theme_panel.hide()
        self.advanced_theme_toggle.toggled.connect(self.advanced_theme_panel.setVisible)
        self.advanced_theme_toggle.toggled.connect(
            lambda expanded: self.advanced_theme_toggle.setText(
                "Hide advanced colors" if expanded else "Show advanced colors"
            )
        )
        self._update_advanced_theme_controls()

        appearance_layout.addStretch()

        background_tab = QWidget()
        background_layout = QVBoxLayout(background_tab)
        self._add_section_heading(background_layout, "Artwork")

        self.background_enabled_checkbox = QCheckBox(
            "Show artwork behind lyrics"
        )
        self.background_enabled_checkbox.setChecked(
            bool(self.appearance_settings.get("background_enabled", True))
        )
        background_layout.addWidget(self.background_enabled_checkbox)

        background_layout_row = QHBoxLayout()
        background_layout_row.addWidget(QLabel("Background layout"))
        self.background_layout_combo = QComboBox()
        for mode in ("center", "fill", "fit", "stretch", "tile"):
            self.background_layout_combo.addItem(mode.title(), mode)
        layout_index = self.background_layout_combo.findData(
            self.appearance_settings.get("background_layout", "fit")
        )
        self.background_layout_combo.setCurrentIndex(
            layout_index if layout_index >= 0 else self.background_layout_combo.findData("fit")
        )
        self.background_layout_combo.setToolTip(
            "Center: original size, centered. Fill: cover the area with cropping.\n"
            "Fit: show the whole image. Stretch: resize to the area.\n"
            "Tile: repeat the image at its original size."
        )
        self.background_layout_combo.setEnabled(
            self.background_enabled_checkbox.isChecked()
        )
        self.background_enabled_checkbox.toggled.connect(
            self.background_layout_combo.setEnabled
        )
        background_layout_row.addWidget(self.background_layout_combo, 1)
        background_layout.addLayout(background_layout_row)

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
        self.artwork_source_combo.addItem("Custom", "custom")
        current_source = self.appearance_settings.get(
            "artwork_source",
            "album_first",
        )
        source_index = self.artwork_source_combo.findData(current_source)
        self.artwork_source_combo.setCurrentIndex(max(0, source_index))
        artwork_source_row.addWidget(self.artwork_source_combo, 1)
        background_layout.addLayout(artwork_source_row)

        self.custom_folders_row_widget = QWidget()
        custom_folders_row = QHBoxLayout(self.custom_folders_row_widget)
        custom_folders_row.setContentsMargins(0, 0, 0, 0)
        custom_folders_row.addWidget(QLabel("Custom folders"))
        self.custom_background_folders_list = QListWidget()
        from ui.content_list import configure_content_list
        configure_content_list(self.custom_background_folders_list)
        self.custom_background_folders_list.setMaximumHeight(76)
        self.custom_background_folders_list.setToolTip(
            "A random image from these folders, including subfolders, is "
            "used for each track."
        )
        for folder in self.appearance_settings.get(
            "custom_background_folders", []
        ):
            self.custom_background_folders_list.addItem(folder)
        custom_folders_row.addWidget(self.custom_background_folders_list, 1)
        custom_folder_buttons = QVBoxLayout()
        self.add_custom_background_folder_button = QPushButton("Add folder...")
        self.add_custom_background_folder_button.clicked.connect(
            self._add_custom_background_folder
        )
        custom_folder_buttons.addWidget(
            self.add_custom_background_folder_button
        )
        self.remove_custom_background_folder_button = QPushButton("Remove")
        self.remove_custom_background_folder_button.clicked.connect(
            self._remove_custom_background_folder
        )
        custom_folder_buttons.addWidget(
            self.remove_custom_background_folder_button
        )
        custom_folder_buttons.addStretch(1)
        custom_folders_row.addLayout(custom_folder_buttons)
        background_layout.addWidget(self.custom_folders_row_widget)

        self.booru_tags_row_widget = QWidget()
        booru_tags_row = QHBoxLayout(self.booru_tags_row_widget)
        booru_tags_row.setContentsMargins(0, 0, 0, 0)
        booru_tags_row.addWidget(QLabel("Booru tags"))
        self.booru_tags_edit = QLineEdit(
            self.appearance_settings.get("booru_tags", "scenery")
        )
        self.booru_tags_edit.setPlaceholderText("e.g. scenery night city")
        self.booru_tags_edit.setToolTip(
            "Safebooru search tags. A random matching image is chosen."
        )
        booru_tags_row.addWidget(self.booru_tags_edit, 1)
        background_layout.addWidget(self.booru_tags_row_widget)
        self.custom_background_folders_list.currentRowChanged.connect(
            self._update_custom_folder_controls
        )
        self.artwork_source_combo.currentIndexChanged.connect(
            self._update_custom_folder_controls
        )
        self.background_enabled_checkbox.toggled.connect(
            self._update_custom_folder_controls
        )
        self._update_custom_folder_controls()

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
        background_layout.addLayout(blur_mode_row)

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
        background_layout.addWidget(self.background_blur_bleed_checkbox)

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
        background_layout.addLayout(blur_row)

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
        background_layout.addLayout(darkness_row)

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

        background_layout.addStretch()

        # ===== PLAYBACK =====
        playback_tab = self._make_scroll_tab(
            "Playback behavior and timing settings.",
            lambda body_layout: self._create_playback_controls(body_layout),
        )

        # ===== LYRICS =====
        lyrics_tab = QWidget()
        lyrics_layout = QVBoxLayout(lyrics_tab)
        self.scope_tabs = QTabWidget()

        global_tab = self._make_lyrics_page(
            "Default lyric display settings for all files.",
            self._create_global_lyrics_controls,
        )
        self.global_controls = self._last_scroll_controls

        if current_file:
            local_tab = self._make_lyrics_page(
                f"Overrides for: {current_file}\nEnable Override to replace a global setting for this file.",
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
        from ui.content_list import configure_content_list
        configure_content_list(self.folder_list)
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
            "Plugin changes are saved immediately when Settings is accepted and take effect after restarting LRCPlus    ."
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
            self.plugin_manager.dependency_manager.configure(catalog)

        if catalog:
            disabled = set(self.plugin_manager.disabled_plugins)
            for plugin in catalog:
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(8, 8, 8, 8)

                details = QVBoxLayout()
                title = QLabel(
                    f"<b>{plugin['name']}</b>  "
                    f"<span style='color: gray;'>{plugin['category']} · v{plugin['version']}</span>"
                )
                details.addWidget(title)
                description = QLabel(plugin['description'] or "No description available.")
                description.setWordWrap(True)
                description.setStyleSheet("color: #A0A0A0;")
                details.addWidget(description)
                problem = self.plugin_manager.load_errors.get(plugin["plugin_id"])
                if problem:
                    failure = QLabel(problem)
                    failure.setTextFormat(Qt.TextFormat.PlainText)
                    failure.setWordWrap(True)
                    details.addWidget(failure)
                if plugin.get("dependencies"):
                    dependencies_button = QPushButton("Dependencies")
                    dependencies_button.clicked.connect(
                        lambda _checked=False: tabs.setCurrentWidget(self.dependencies_tab)
                    )
                    row.addWidget(dependencies_button)
                row.addLayout(details, 1)

                plugin_id = plugin['plugin_id']
                plugin_settings = []
                if self.plugin_manager is not None:
                    plugin_settings = [
                        entry for entry in self.plugin_manager.get_settings_widgets()
                        if entry.get("owner") == plugin_id
                    ]

                if plugin_settings:
                    settings_button = QPushButton("Settings")
                    settings_button.clicked.connect(
                        lambda _checked=False, pid=plugin_id, pname=plugin['name']:
                            self._open_plugin_settings(pid, pname)
                    )
                    row.addWidget(settings_button)

                toggle = QCheckBox("Enabled")
                toggle.setProperty("settingsLabel", f"Enable {plugin['name']}")
                toggle.setProperty("settingsKeywords", plugin.get("description") or "")
                is_core_plugin = plugin["category"] == "Core"
                toggle.setChecked(is_core_plugin or plugin_id not in disabled)
                toggle.setEnabled(not is_core_plugin)
                if is_core_plugin:
                    toggle.setToolTip("Core plugins cannot be disabled.")
                row.addWidget(toggle)
                self.plugin_toggles[plugin_id] = toggle

                plugin_body_layout.addWidget(row_widget)
        else:
            empty = QLabel("No plugins were found.")
            empty.setStyleSheet("color: #A0A0A0;")
            plugin_body_layout.addWidget(empty)

        plugin_body_layout.addStretch()
        plugin_scroll.setWidget(plugin_body)
        plugins_layout.addWidget(plugin_scroll, 1)

        tabs.addTab(self._scroll_page(general_tab), "General")
        tabs.addTab(self._scroll_page(appearance_tab), "Appearance")
        tabs.addTab(self._scroll_page(background_tab), "Background")
        tabs.addTab(playback_tab, "Playback")
        tabs.addTab(lyrics_tab, "Lyrics")
        tabs.addTab(self._make_scroll_tab(
            "Karaoke", self._create_karaoke_controls
        ), "Karaoke")
        tabs.addTab(files_tab, "Files")
        tabs.addTab(plugins_tab, "Plugins")
        self.dependencies_tab = DependencyManagerWidget(
            self.plugin_manager, self, defer_refresh=True
        )
        tabs.addTab(self.dependencies_tab, "Dependencies")
        self._dependencies_loaded = False

        def load_dependencies(index):
            if tabs.widget(index) is self.dependencies_tab and not self._dependencies_loaded:
                self.dependencies_tab.refresh()
                self._dependencies_loaded = True

        tabs.currentChanged.connect(load_dependencies)
        self.about_tab = AboutTab(self.plugin_manager, self)
        tabs.addTab(self.about_tab, "About")

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        self.category_list = QListWidget()
        self.category_list.setObjectName("settingsCategories")
        self.category_list.setAccessibleName("Settings categories")
        self.category_list.setFixedWidth(190)
        self.category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for index in range(tabs.count()):
            self.category_list.addItem(tabs.tabText(index))
        self.category_list.currentRowChanged.connect(tabs.setCurrentIndex)
        tabs.currentChanged.connect(self.category_list.setCurrentRow)
        self.category_list.setCurrentRow(0)

        self.settings_search = SettingsSearch(tabs, self.category_list, self)
        self._index_search_settings()
        sidebar = QVBoxLayout()
        sidebar.addWidget(self.settings_search.search_edit)
        sidebar.addWidget(self.category_list, 1)
        content = QHBoxLayout()
        content.addLayout(sidebar)
        content.addWidget(self.settings_search, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(content, 1)
        layout.addWidget(button_box)

    def _index_search_settings(self):
        # Labels set by the shared row helpers stay in sync with the visible UI.
        labels = {
            "update_channel_combo": "Update channel",
            "light_theme_button": "Light color mode",
            "dark_theme_button": "Dark color mode",
            "theme_color_button": "Accent color",
            "theme_surface_combo": "Surface palette",
            "advanced_theme_toggle": "Advanced theme colors",
            "theme_role_combo": "Advanced theme colors",
            "theme_role_color_button": "Advanced color",
            "theme_role_reset_button": "Reset advanced color",
            "theme_reset_mode_button": "Reset advanced colors",
            "theme_preset_combo": "Theme presets",
            "background_layout_combo": "Background layout",
            "artwork_source_combo": "Artwork source",
            "custom_background_folders_list": "Custom background folders",
            "booru_tags_edit": "Booru tags",
            "background_blur_mode_combo": "Blur quality",
            "background_blur_slider": "Background blur",
            "background_darkness_slider": "Background darkness",
            "empty_timestamp_placeholder_edit": "Empty timestamp placeholder",
            "folder_list": "Lyrics search folders",
            "check_updates_button": "Check for updates",
        }
        for name, label in labels.items():
            getattr(self, name).setProperty("settingsLabel", label)
        for key, widget in self.global_controls.items():
            if key in LYRIC_KEYS:
                widget.setProperty("settingsKeywords", key)
        for key, (override, widget) in self.local_controls.items():
            widget.setProperty("settingsLabel", self._local_setting_name(key))
            widget.setProperty("settingsKeywords", key)
            widget.setProperty("settingsFocusWidget", override)
        for key, widget in self.karaoke_controls.items():
            widget.setProperty("settingsKeywords", key)
        conditional = {
            self.custom_background_folders_list: (
                self.artwork_source_combo, "Choose Custom as the artwork source to edit custom folders."
            ),
            self.booru_tags_edit: (
                self.artwork_source_combo, "Choose an artwork source that uses Booru to edit Booru tags."
            ),
        }
        for key, style in (("softness", "Soft"), ("glow_intensity", "Glow"),
                           ("glow_radius", "Glow"), ("shimmer_width", "Shimmer")):
            conditional[self.karaoke_controls[key]] = (
                self.karaoke_controls["sweep_style"],
                f"Choose the {style} sweep effect to edit this setting.",
            )
        for index in range(self.category_pages.count()):
            page = self.category_pages.widget(index)
            title = self.category_pages.tabText(index)
            keywords = {"Dependencies": "install packages tools plugins", "About": "version updates application"}.get(title, "")
            self.settings_search.add(page, title, keywords)
            if title in {"Dependencies", "About"}:
                continue
            for widget in page.findChildren(QWidget):
                label = widget.property("settingsLabel")
                if not label and isinstance(widget, QCheckBox) and widget.text() != "Override":
                    label = widget.text()
                if not label:
                    continue
                fallback, availability = conditional.get(widget, (None, ""))
                self.settings_search.add(
                    widget, label, widget.property("settingsKeywords") or "",
                    fallback=fallback, availability=availability,
                    focus_widget=widget.property("settingsFocusWidget"),
                )

    def done(self, result):
        if hasattr(self, "dependencies_tab") and self.dependencies_tab.busy:
            self.dependencies_tab.status.setText(
                "Please wait for the dependency operation to finish before closing Settings."
            )
            return
        super().done(result)

    @staticmethod
    def _scroll_page(body):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        return scroll

    def _make_lyrics_page(self, description, builder):
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(description)
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(hint)
        self._last_scroll_controls = builder(layout)
        return page

    def _create_lyric_sections(self, layout):
        tabs = QTabWidget()
        tabs.setObjectName("lyricsSections")
        tabs.setAccessibleName("Lyric settings sections")
        sections = {}
        for title in LYRIC_SECTIONS:
            body = QWidget()
            body_layout = QVBoxLayout(body)
            body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            sections[title] = body_layout
            tabs.addTab(self._scroll_page(body), title)
        layout.addWidget(tabs, 1)
        return sections

    def _create_global_lyrics_controls(self, layout):
        sections = self._create_lyric_sections(layout)
        controls = self._create_lyrics_controls(
            self.global_lyrics, layout, include_playback=False,
            section_layouts=sections,
        )
        self._create_placeholder_controls(sections["Display"])
        return controls

    def _create_placeholder_controls(self, layout):
        self._add_section_heading(layout, "Empty timestamp placeholder")
        self.empty_timestamp_placeholder_checkbox = QCheckBox(
            "Show placeholder for empty timed lyric lines"
        )
        self.empty_timestamp_placeholder_checkbox.setChecked(
            bool(self.appearance_settings.get(
                "empty_timestamp_placeholder_enabled", True
            ))
        )
        layout.addWidget(self.empty_timestamp_placeholder_checkbox)

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
        layout.addLayout(placeholder_row)
        placeholder_hint = QLabel(
            "Example: ♪ {countdown}  →  ♪ 12.5"
        )
        muted_color = build_theme_palette(
            self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
        )["muted"]
        placeholder_hint.setStyleSheet(
            f"color: {muted_color}; font-style: italic;"
        )
        layout.addWidget(placeholder_hint)
        self.empty_timestamp_placeholder_edit.setEnabled(
            self.empty_timestamp_placeholder_checkbox.isChecked()
        )
        self.empty_timestamp_placeholder_checkbox.toggled.connect(
            self.empty_timestamp_placeholder_edit.setEnabled
        )

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
        self.gapless_checkbox = QCheckBox("Gapless playback")
        self.gapless_checkbox.setChecked(bool(self.playback_settings.get("gapless_playback", False)))
        self.gapless_checkbox.setToolTip(
            "Prebuffer the next track for uninterrupted playback. Requires FFmpeg; "
            "available at normal speed and pitch. Takes effect on the next track."
        )
        layout.addWidget(self.gapless_checkbox)
        self.replaygain_combo = QComboBox()
        for label, value in (("Off", "off"), ("Track", "track"), ("Album", "album")):
            self.replaygain_combo.addItem(label, value)
        self.replaygain_combo.setCurrentIndex(max(0, self.replaygain_combo.findData(
            self.playback_settings.get("replaygain_mode", "off"))))
        self.replaygain_combo.setToolTip(
            "Use existing ReplayGain tags to balance loudness. Album mode preserves "
            "relative track levels and falls back to track tags. Untagged files are unchanged."
        )
        self._add_labeled(layout, "ReplayGain:", self.replaygain_combo)
        self.replaygain_preamp = QSpinBox()
        self.replaygain_preamp.setRange(-24, 24)
        self.replaygain_preamp.setSuffix(" dB")
        self.replaygain_preamp.setValue(int(self.playback_settings.get("replaygain_preamp", 0)))
        self._add_labeled(layout, "ReplayGain preamp:", self.replaygain_preamp)
        self.replaygain_clipping = QCheckBox("Prevent clipping using ReplayGain peak tags")
        self.replaygain_clipping.setChecked(bool(self.playback_settings.get("replaygain_prevent_clipping", True)))
        layout.addWidget(self.replaygain_clipping)
        self.show_waveform_checkbox = QCheckBox(
            "Show playback waveform"
        )
        self.show_waveform_checkbox.setChecked(
            bool(self.playback_settings.get("show_waveform", True))
        )
        self.show_waveform_checkbox.setToolTip(
            "Show the audio waveform in the main player."
        )
        layout.addWidget(self.show_waveform_checkbox)

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
        self.play_mode_combo.addItem("Queue", "playlist")
        self.play_mode_combo.addItem("Library", "library")
        play_mode = self.playback_settings.get("play_mode", "library")
        index = self.play_mode_combo.findData(play_mode)
        self.play_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self._add_labeled(layout, "Play Mode:", self.play_mode_combo)
        self.play_mode_combo.setToolTip(
            "Choose whether previous/next playback follows the current queue or the full music library. "
            "Adding tracks to the queue or loading a saved playlist selects Queue mode."
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
            "Choose which Windows audio output device LRCPlus uses for playback."
        )

    def _create_lyrics_controls(
        self, values, layout, include_playback=False, section_layouts=None
    ):
        controls = {}
        sections = section_layouts if section_layouts is not None else self._create_lyric_sections(layout)

        layout = sections["Display"]
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

        layout = sections["Readings"]
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

        all_romaji = QCheckBox("All romaji")
        all_romaji.setChecked(bool(values.get("all_romaji", False)))
        if all_romaji.isChecked():
            show_ruby.setChecked(False)
            show_romaji.setChecked(False)
        controls["all_romaji"] = all_romaji
        layout.addWidget(all_romaji)

        # Furigana, romaji, and all-romaji are mutually exclusive display modes.
        # Keep the controls synchronized so there is only ever one reading style.
        show_ruby.toggled.connect(
            lambda checked: (
                (show_romaji.setChecked(False), all_romaji.setChecked(False))
                if checked else None
            )
        )
        show_romaji.toggled.connect(
            lambda checked: (
                (show_ruby.setChecked(False), all_romaji.setChecked(False))
                if checked else None
            )
        )
        all_romaji.toggled.connect(
            lambda checked: (
                (show_ruby.setChecked(False), show_romaji.setChecked(False))
                if checked else None
            )
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

        mecab_parse_mode = QComboBox()
        mecab_parse_mode.addItem("Batch parsing (faster)", "batch")
        mecab_parse_mode.addItem("Individual lines (more accurate)", "line")
        mecab_parse_mode.setToolTip(
            "Controls how local MeCab analyzes Japanese lyrics. Batch parsing "
            "is faster but surrounding lines can occasionally affect tokenization. "
            "Individual-line parsing isolates each lyric line for more consistent readings."
        )
        mecab_parse_mode.setCurrentIndex(max(0, mecab_parse_mode.findData(
            values.get("mecab_parse_mode", "batch")
        )))
        controls["mecab_parse_mode"] = mecab_parse_mode

        def update_mecab_parse_mode_enabled(*_args):
            mecab_parse_mode.setEnabled(parser.currentData() == "mecab_local")

        update_mecab_parse_mode_enabled()
        parser.currentIndexChanged.connect(update_mecab_parse_mode_enabled)

        self._add_labeled(layout, "MeCab parsing:", mecab_parse_mode)

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

        ruby_font_size = ThemedSpinBox()
        ruby_font_size.setRange(6, 72)
        ruby_font_size.setValue(int(values.get("ruby_font_size", 18)))
        ruby_font_size.setSuffix(" pt")
        controls["ruby_font_size"] = ruby_font_size
        self._add_labeled(layout, "Ruby text font size:", ruby_font_size)

        layout = sections["Typography"]
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

        font_size = ThemedSpinBox()
        font_size.setRange(10, 96)
        font_size.setValue(int(values.get("font_size", 32)))
        font_size.setSuffix(" pt")
        controls["font_size"] = font_size
        self._add_labeled(layout, "Font size:", font_size)

        rendering_group = QGroupBox("Rendering")
        rendering_layout = QVBoxLayout(rendering_group)
        focus = QCheckBox("Focus transition")
        focus.setChecked(bool(values.get("focus_transition", True)))
        focus.setToolTip("Bring the current line into focus while gently blurring and dimming surrounding lyrics.")
        controls["focus_transition"] = focus
        rendering_layout.addWidget(focus)
        sections["Display"].addWidget(rendering_group)

        layout = sections["Highlighting"]
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
            muted_color = build_theme_palette(
                self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
            )["muted"]
            karaoke_notice.setStyleSheet(
                f"color: {muted_color}; font-style: italic;"
            )
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
            sections["Typography"], "Lyrics color:",
            values.get("lyrics_color", "#ffffff")
        )
        controls["lyrics_color"] = lyrics_color

        layout = sections["Scrolling"]
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
        continuous_speed.setProperty("settingsLabel", "Continuous scroll speed")
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
        widget.setProperty("settingsLabel", title)
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        row.addWidget(widget, 1)
        layout.addLayout(row)

    def _add_slider(self, layout, title, value, minimum, maximum, suffix):
        row = QHBoxLayout()
        row.addWidget(QLabel(title))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setProperty("settingsLabel", title)
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

    def _update_theme_color_button(self):
        color = QColor(self.theme_color)
        palette = build_theme_palette(self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides)
        self.theme_color_button.setText(color.name().upper())
        self.theme_color_button.setToolTip("Choose the app accent color")
        self.theme_color_button.setStyleSheet(
            "QPushButton {"
            f" color: {palette['accent_text']}; background: {color.name()};"
            f" border: 1px solid {palette['field_border']}; border-radius: 5px;"
            " padding: 6px 11px; text-align: left; }"
            f"QPushButton:hover {{ border-color: {palette['accent_border']}; }}"
        )

    def _update_advanced_theme_controls(self, *_args):
        if not hasattr(self, "theme_role_color_button"):
            return
        key = self.theme_role_combo.currentData()
        palette = build_theme_palette(self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides)
        def update_color_button(button, value):
            color = QColor(value)
            text = "#000000" if color.lightness() > 140 else "#ffffff"
            button.setText(color.name().upper())
            button.setStyleSheet(
                f"QPushButton {{ background: {color.name()}; color: {text}; padding: 6px 12px; }}"
            )

        update_color_button(self.theme_role_color_button, palette[key])
        for role, button in self.theme_simple_color_buttons.items():
            update_color_button(button, palette[role])
            self.theme_simple_reset_buttons[role].setEnabled(
                role in self.theme_overrides.get(self.theme_mode, {})
            )
        self.theme_role_reset_button.setEnabled(key in self.theme_overrides.get(self.theme_mode, {}))
        self.theme_reset_mode_button.setEnabled(bool(self.theme_overrides.get(self.theme_mode)))

    def _refresh_advanced_theme(self):
        self._update_theme_color_button()
        self._update_advanced_theme_controls()
        _apply_settings_dialog_style(self, self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides)

    def _choose_theme_role_color(self):
        self._choose_theme_color_for_role(self.theme_role_combo.currentData())

    def _choose_theme_color_for_role(self, key):
        palette = build_theme_palette(self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides)
        color = QColorDialog.getColor(QColor(palette[key]), self, "Choose " + THEME_COLOR_FIELDS[key].lower())
        if color.isValid():
            self.theme_overrides.setdefault(self.theme_mode, {})[key] = color.name()
            self._refresh_advanced_theme()

    def _reset_theme_role_color(self):
        self._reset_theme_color_for_role(self.theme_role_combo.currentData())

    def _reset_theme_color_for_role(self, key):
        colors = self.theme_overrides.get(self.theme_mode, {})
        colors.pop(key, None)
        if not colors:
            self.theme_overrides.pop(self.theme_mode, None)
        self._refresh_advanced_theme()

    def _reset_theme_mode_colors(self):
        self.theme_overrides.pop(self.theme_mode, None)
        self._refresh_advanced_theme()

    def _change_theme_surface(self, _index):
        self.theme_surface = self.theme_surface_combo.currentData()
        self._update_advanced_theme_controls()
        self._update_theme_color_button()
        _apply_settings_dialog_style(self, self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides)

    def _choose_theme_preset(self, index):
        preset = self.theme_preset_combo.itemData(index)
        if not preset:
            return
        if isinstance(preset, str):
            # Keep the original standalone accent presets available.
            self.theme_color = preset
            self.theme_preset_name = None
        else:
            self.theme_preset_name = preset["name"]
            self.theme_mode = "dark" if QColor(preset["background"]).lightness() < 128 else "light"
            self.theme_color = QColor(preset["accent"]).name()
            background = QColor(preset["background"])
            surface = QColor(preset["surface"])
            accent = QColor(preset["accent"])
            text = QColor(preset["text"])
            secondary_text = QColor(preset["secondaryText"])
            dock = background if self.theme_mode == "dark" else text
            dock_control = surface if self.theme_mode == "dark" else dock.lighter(135)
            dock_text = text if self.theme_mode == "dark" else surface
            raised_base = surface if self.theme_mode == "dark" else background
            colors = self.theme_overrides.setdefault(self.theme_mode, {})
            colors.update({
                "window": background.name(),
                "header": background.name(),
                "surface": surface.name(),
                "surface_alt": _blend_theme_colors(background, surface, 0.55),
                "raised": _blend_theme_colors(raised_base.name(), text.name(), 0.10),
                "raised_hover": _blend_theme_colors(raised_base.name(), accent.name(), 0.10),
                "pressed": _blend_theme_colors(raised_base.name(), accent.name(), 0.20),
                "text": text.name(),
                "brand": text.name(),
                "muted": secondary_text.name(),
                "border": _blend_theme_colors(background.name(), text.name(), 0.16),
                "field_border": _blend_theme_colors(background.name(), text.name(), 0.22),
                "header_button_text": text.name(),
                "dock": dock.name(),
                "dock_border": dock.lighter(150).name(),
                "dock_control": dock_control.name(),
                "dock_control_hover": dock_control.lighter(115).name(),
                "dock_control_border": dock_control.lighter(130).name(),
                "dock_disabled": dock.darker(110).name(),
                "dock_text": dock_text.name(),
                "dock_muted": secondary_text.name(),
                "seek_track": accent.name(),
                "slider_handle": dock_text.name(),
                "scroll_handle": accent.name(),
            })
            advanced = normalize_theme_overrides(preset.get("advancedColors", {}))
            for mode, advanced_colors in advanced.items():
                self.theme_overrides.setdefault(mode, {}).update(advanced_colors)
            colors.update(advanced.get(self.theme_mode, {}))
            self.light_theme_button.setChecked(self.theme_mode == "light")
            self.dark_theme_button.setChecked(self.theme_mode == "dark")
        self._update_advanced_theme_controls()
        self._update_theme_color_button()
        _apply_settings_dialog_style(self, self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides)
        self._update_theme_management_controls()

    def _update_theme_management_controls(self):
        if not hasattr(self, "delete_theme_button"):
            return
        self.delete_theme_button.setEnabled(
            self.theme_preset_combo.currentText() in {theme["name"] for theme in self.custom_themes}
        )

    def _add_theme_combo_section(self, label):
        self.theme_preset_combo.addItem(label)
        item = self.theme_preset_combo.model().item(self.theme_preset_combo.count() - 1)
        if item is not None:
            item.setEnabled(False)
            item.setSelectable(False)
            font = item.font()
            font.setBold(True)
            font.setPointSize(font.pointSize() + 1)
            item.setFont(font)
            palette = build_theme_palette(
                self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
            )
            item.setForeground(QColor(palette["accent"]))
            item.setBackground(QColor(palette["raised"]))

    def _populate_theme_preset_combo(self):
        self.theme_preset_combo.clear()
        self.theme_preset_combo.addItem("Choose a preset", None)
        self._add_theme_combo_section("Core")
        for name, color in (("Green", "#3a9879"), ("Blue", "#4477cc"), ("Purple", "#895cc4"), ("Rose", "#c45278"), ("Orange", "#bd691f")):
            self.theme_preset_combo.addItem(name, color)
        for name, theme in _load_theme_color_presets():
            self.theme_preset_combo.addItem(name, theme)
        self._add_theme_combo_section("User")
        for theme in self.custom_themes:
            self.theme_preset_combo.addItem(theme["name"], theme)

    def _current_theme_definition(self, name):
        palette = build_theme_palette(
            self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
        )
        return {
            "name": name,
            "description": "Saved from the current appearance",
            "background": palette["window"],
            "surface": palette["surface"],
            "accent": palette["accent"],
            "text": palette["text"],
            "secondaryText": palette["muted"],
            "advancedColors": normalize_theme_overrides(self.theme_overrides),
        }

    def _reload_theme_presets(self, selected_name=None):
        self.theme_preset_combo.blockSignals(True)
        self._populate_theme_preset_combo()
        if selected_name:
            self.theme_preset_combo.setCurrentIndex(self.theme_preset_combo.findText(selected_name))
        else:
            self.theme_preset_combo.setCurrentIndex(0)
        self.theme_preset_combo.blockSignals(False)
        self._update_theme_management_controls()

    def _save_current_theme(self):
        name, accepted = QInputDialog.getText(self, "Save theme", "Theme name:")
        name = name.strip()
        if not accepted or not name:
            return
        builtin_names = {theme_name.casefold() for theme_name, _ in _load_theme_color_presets()}
        existing = next((theme for theme in self.custom_themes if theme["name"].casefold() == name.casefold()), None)
        if name.casefold() in builtin_names and existing is None:
            QMessageBox.warning(self, "Theme already exists", "Choose a name that is not used by a built-in theme.")
            return
        theme = self._current_theme_definition(name)
        if existing:
            self.custom_themes[self.custom_themes.index(existing)] = theme
        else:
            self.custom_themes.append(theme)
        self.theme_preset_name = name
        self._reload_theme_presets(name)

    def _import_theme(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import theme", "", "Theme JSON (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "Could not import theme", str(error))
            return
        candidates = payload.get("themes", []) if isinstance(payload, dict) and "themes" in payload else [payload]
        if not isinstance(candidates, list):
            QMessageBox.warning(self, "Could not import theme", "The themes field must be a list.")
            return
        imported = [_valid_theme_definition(theme) for theme in candidates]
        if not imported or any(theme is None for theme in imported):
            QMessageBox.warning(self, "Could not import theme", "Each theme needs a name and five valid hex colors.")
            return
        builtin_names = {name.casefold() for name, _ in _load_theme_color_presets()}
        if any(theme["name"].casefold() in builtin_names for theme in imported):
            QMessageBox.warning(self, "Could not import theme", "An imported theme uses a built-in theme name.")
            return
        for theme in imported:
            existing = next((item for item in self.custom_themes if item["name"].casefold() == theme["name"].casefold()), None)
            if existing:
                self.custom_themes[self.custom_themes.index(existing)] = theme
            else:
                self.custom_themes.append(theme)
        self.theme_preset_name = imported[0]["name"]
        self._reload_theme_presets(self.theme_preset_name)
        self._choose_theme_preset(self.theme_preset_combo.currentIndex())

    def _export_theme(self):
        name = self.theme_preset_combo.currentText()
        name, accepted = QInputDialog.getText(self, "Export theme", "Theme name:", text=name if name != "Choose a preset" else "Custom")
        if not accepted:
            return
        theme = self._current_theme_definition(name.strip() or "Custom")
        path, _ = QFileDialog.getSaveFileName(self, "Export theme", f"{theme['name']}.json", "Theme JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps({"themes": [theme]}, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Could not export theme", str(error))

    def _delete_saved_theme(self):
        name = self.theme_preset_combo.currentText()
        index = next((i for i, theme in enumerate(self.custom_themes) if theme["name"] == name), None)
        if index is None:
            return
        self.custom_themes.pop(index)
        self.theme_preset_name = None
        self._reload_theme_presets()

    def _set_theme_mode(self, theme_mode):
        self.theme_mode = theme_mode
        self._update_advanced_theme_controls()
        self._update_theme_color_button()
        _apply_settings_dialog_style(
            self, self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
        )

    def _update_custom_folder_controls(self, *_args):
        enabled = (
            self.background_enabled_checkbox.isChecked()
            and self.artwork_source_combo.currentData() == "custom"
        )
        source = self.artwork_source_combo.currentData()
        self.custom_folders_row_widget.setVisible(source == "custom")
        self.booru_tags_row_widget.setVisible(
            source in {"album_first", "booru_first", "booru_only"}
        )
        self.custom_background_folders_list.setEnabled(enabled)
        self.add_custom_background_folder_button.setEnabled(enabled)
        self.remove_custom_background_folder_button.setEnabled(
            enabled
            and self.custom_background_folders_list.currentRow() >= 0
        )

    def _add_custom_background_folder(self):
        current = self.custom_background_folders_list.currentItem()
        start = current.text() if current else ""
        folder = QFileDialog.getExistingDirectory(
            self, "Choose custom background folder", start
        )
        if not folder:
            return
        existing = {
            self.custom_background_folders_list.item(index).text()
            for index in range(self.custom_background_folders_list.count())
        }
        if folder not in existing:
            self.custom_background_folders_list.addItem(folder)
        self._update_custom_folder_controls()

    def _remove_custom_background_folder(self):
        row = self.custom_background_folders_list.currentRow()
        if row >= 0:
            self.custom_background_folders_list.takeItem(row)
        self._update_custom_folder_controls()

    def _choose_theme_color(self):
        color = QColorDialog.getColor(
            QColor(self.theme_color), self, "Choose accent color"
        )
        if not color.isValid():
            return
        self.theme_color = color.name()
        self.theme_preset_name = None
        self.theme_preset_combo.setCurrentIndex(0)
        self._update_theme_color_button()
        _apply_settings_dialog_style(
            self, self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
        )

    def _local_setting_name(self, key):
        names = {
            "scrolling_lyrics": "Display mode:", "lyrics_direction": "Lyrics direction:",
            "lyrics_padding": "Lyrics padding:", "vertical_padding": "Vertical padding:", "show_ruby": "Show furigana:", "show_romaji": "Show romaji:",
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

        sections = self._create_lyric_sections(layout)
        grids = {}
        for title, section_layout in sections.items():
            grid = QGridLayout()
            grid.setColumnStretch(2, 1)
            grid.setVerticalSpacing(10)
            section_layout.addLayout(grid)
            grids[title] = grid
        section_for_key = {
            key: title for title, keys in LYRIC_SECTIONS.items() for key in keys
        }
        for key in (key for keys in LYRIC_SECTIONS.values() for key in keys
                    if key in LOCAL_LYRIC_KEYS):
            grid = grids[section_for_key[key]]
            row_index = grid.rowCount()
            if key == "highlight_color" and self.plugin_manager is not None and "karaoke" in self.plugin_manager.plugins:
                notice = QLabel(
                    "Karaoke timing uses its own progressive highlighting "
                    "and ignores current lyric highlighting."
                )
                notice.setWordWrap(True)
                notice.setStyleSheet("color: palette(mid); font-style: italic;")
                grid.addWidget(notice, row_index, 0, 1, 4)
                row_index += 1
            setting_name = QLabel(self._local_setting_name(key))
            setting_name.setMinimumWidth(185)
            grid.addWidget(setting_name, row_index, 0)

            enabled = QCheckBox("Override")
            enabled.setChecked(key in overrides)

            value = overrides.get(key, global_values.get(key))

            editor = self._editor_for_key(key, value)
            label = self._editor_label(key, editor)

            def sync(state, widget=editor):
                widget.setEnabled(bool(state))

            enabled.toggled.connect(sync)
            sync(enabled.isChecked())

            grid.addWidget(enabled, row_index, 1)
            grid.addWidget(editor, row_index, 2)
            if label is not None:
                grid.addWidget(label, row_index, 3)
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
        if key in {"show_ruby", "show_romaji", "all_romaji", "highlight_current", "focus_transition"}:
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
            editor = ThemedSpinBox()
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
        if key in {"show_ruby", "show_romaji", "all_romaji", "highlight_current", "continuous_scroll_duration_based", "continuous_scroll_nudge_current", "limited_reading_rendering", "fade_in_readings", "focus_transition"}:
            return control.isChecked()
        if key in {
            "lyrics_direction",
            "scrolling_mode",
            "furigana_parser",
            "mecab_parse_mode",
            "lyrics_alignment",
            "ruby_position",
            "scroll_animation_easing",
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

    def get_update_settings(self):
        return {
            **self.update_settings,
            "automatic_check": self.automatic_update_checkbox.isChecked(),
            "channel": self.update_channel_combo.currentData(),
        }


    def get_appearance_settings(self):
        return {
            "theme_mode": self.theme_mode,
            "theme_color": self.theme_color,
            "theme_preset_name": self.theme_preset_name or "",
            "custom_themes": list(self.custom_themes),
            "theme_surface": self.theme_surface,
            "theme_overrides": normalize_theme_overrides(self.theme_overrides),
            "background_enabled": self.background_enabled_checkbox.isChecked(),
            "background_layout": self.background_layout_combo.currentData(),
            "custom_background_folders": [
                self.custom_background_folders_list.item(index).text()
                for index in range(
                    self.custom_background_folders_list.count()
                )
            ],
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


    def _open_plugin_settings(self, plugin_id, plugin_name):
        if self.plugin_manager is None:
            return
        dialog = PluginSettingsDialog(
            self.plugin_manager, plugin_id, plugin_name, parent=self
        )
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()


    def apply_plugin_settings(self):
        """Commit registered plugin settings after this dialog is accepted."""
        for entry, widget in getattr(
            self, "_plugin_settings_instances", []
        ):
            callback = entry.get("apply")
            if callback is None:
                continue
            try:
                callback(widget)
            except Exception as error:
                owner = entry.get("owner", "unknown")
                if self.plugin_manager is not None:
                    self.plugin_manager.log(
                        owner,
                        f"settings apply failed: {error!r}",
                    )
                else:
                    print(
                        f"[Plugin:{owner}] settings apply failed: {error!r}"
                    )

    def get_playback_settings(self):
        return {
            "precise_timestamp_fps": int(
                self.precise_timestamp_fps.currentData()
            ),
            "play_mode": self.play_mode_combo.currentData(),
            "shuffle_enabled": self.playback_settings.get("shuffle_enabled", False),
            "shuffle_mode": self.shuffle_mode_combo.currentData(),
            "after_playback": self.after_playback_combo.currentData(),
            "gapless_playback": self.gapless_checkbox.isChecked(),
            "replaygain_mode": self.replaygain_combo.currentData(),
            "replaygain_preamp": self.replaygain_preamp.value(),
            "replaygain_prevent_clipping": self.replaygain_clipping.isChecked(),
            "output_device": self.output_device_combo.currentData(),
            "show_waveform": self.show_waveform_checkbox.isChecked(),
            "volume": self.playback_settings.get("volume", 80),
            "pitch_mode": self.playback_settings.get("pitch_mode", "Semitones"),
            "pitch_value": self.playback_settings.get("pitch_value", 0),
            "speed_percent": self.playback_settings.get("speed_percent", 100),
            "pitch_linked_to_speed": self.playback_settings.get("pitch_linked_to_speed", False),
        }

    def _create_karaoke_controls(self, layout):
        self.karaoke_controls = {}
        for key, title, options in (
            ("sweep_style", "Sweep effect:", KARAOKE_SWEEP_STYLES.items()),
            ("sweep_easing", "Easing:", (
                ("linear", "Linear"), ("ease_in", "Ease In"),
                ("ease_out", "Ease Out"), ("ease_in_out", "Ease In-Out"),
            )),
            ("sweep_fps", "Karaoke sweep FPS:", ((fps, f"{fps} FPS") for fps in (15, 30, 60, 120))),
        ):
            combo = QComboBox()
            for value, label in options:
                combo.addItem(label, value)
            combo.setCurrentIndex(combo.findData(self.karaoke_settings[key]))
            self._add_labeled(layout, title, combo)
            self.karaoke_controls[key] = combo
        self.karaoke_sweep_fps = self.karaoke_controls["sweep_fps"]
        self.karaoke_sweep_fps.setToolTip(
            "How often karaoke highlighting is refreshed during playback."
        )
        description = QLabel()
        description.setWordWrap(True)
        layout.addWidget(description)
        effect_controls = {}
        for key, title, minimum, maximum, suffix, styles in (
            ("softness", "Edge softness:", 1, 40, "", {"soft"}),
            ("glow_intensity", "Glow intensity:", 0, 255, "", {"glow"}),
            ("glow_radius", "Glow spread:", 1, 8, "", {"glow"}),
            ("shimmer_width", "Shimmer width:", 1, 40, "", {"shimmer"}),
            ("sweep_delay_ms", "Maximum sweep delay:", 0, 500, "ms", None),
            ("sweep_delay_percent", "Sweep delay:", 0, 40, "%", None),
        ):
            slider = self._add_slider(
                layout, title, self.karaoke_settings[key], minimum, maximum, suffix
            )
            self.karaoke_controls[key] = slider
            if styles:
                effect_controls[key] = (styles, layout.itemAt(layout.count() - 1).layout())
                if key != "glow_intensity":
                    slider.setToolTip(
                        "Scales with font size. Segment length and duration do not change the effect width."
                    )

        def update_effect_controls():
            style = self.karaoke_controls["sweep_style"].currentData()
            description.setText({
                "classic": "A crisp highlight follows each timed segment.",
                "soft": "Blend the highlight smoothly at the sweep edge.",
                "glow": "Add a luminous glow to the sweep edge.",
                "shimmer": "A bright shimmer travels along the sweep edge.",
                "rainbow": "Sweep through the lyrics with shifting rainbow colors.",
                "pulse": "Pulse the highlight brightness as each segment is sung.",
                "spotlight": "A bright spotlight follows the active part of the lyric.",
            }[style])
            for styles, row in effect_controls.values():
                for index in range(row.count()):
                    row.itemAt(index).widget().setVisible(style in styles)

        self.karaoke_controls["sweep_style"].currentIndexChanged.connect(update_effect_controls)
        update_effect_controls()
        self._add_section_heading(layout, "Animations")
        for key, label, tooltip in (
            ("line_entrance", "Line entrance", "Fade each new line in with a gentle upward drift."),
            ("word_lift", "Word lift", "Lift the active karaoke word and its readings, then settle them as the word finishes."),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(self.karaoke_settings[key])
            checkbox.setToolTip(tooltip)
            self.karaoke_controls[key] = checkbox
            layout.addWidget(checkbox)
        width_note = QLabel("Effect widths scale with font size to stay consistent across short and long segments.")
        width_note.setWordWrap(True)
        layout.addWidget(width_note)
        note = QLabel("Sweep delay is limited by both the percentage of each segment and the maximum delay.")
        note.setWordWrap(True)
        layout.addWidget(note)
        return self.karaoke_controls

    def get_karaoke_settings(self):
        return {
            key: (widget.currentData() if isinstance(widget, QComboBox)
                  else widget.isChecked() if isinstance(widget, QCheckBox)
                  else widget.value())
            for key, widget in self.karaoke_controls.items()
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
