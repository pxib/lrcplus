import json
from pathlib import Path

from PySide6.QtCore import QDateTime, QLocale, QPoint, QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QPainter, QPolygon
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
from core.lyrics_settings import LYRICS_DEFAULTS
from core.settings import (
    APPEARANCE_DEFAULTS, KARAOKE_SWEEP_STYLES, MAX_DEVICE_LYRIC_DELAY_MS,
    get_appearance_settings, get_karaoke_settings, get_startup_settings, get_statistics_settings,
    get_update_settings,
)
from core.theme import build_theme_palette, normalize_theme_overrides, THEME_COLOR_FIELDS
from ui.dependency_settings import DependencyManagerWidget
from ui.about_tab import AboutTab
from ui.settings_search import SettingsSearch
from ui.lyric_layers import LyricLayerOrder
from ui.lyric_settings_fields import (
    LOCAL_LYRIC_KEYS, LYRIC_FIELDS, LYRIC_KEYS, LYRIC_SECTIONS,
)
from ui.settings_transfer import SettingsTransferMixin
from ui.settings_debug import SettingsDebugMixin
from ui.settings_controls import (
    DeferredOptionsComboBox,
    ThemedSpinBox,
    ThemedComboBox,
    QComboBox,
    _SpinStepButton,
    _ComboDropButton,
    SETTINGS_DIALOG_STYLE,
    _apply_settings_dialog_style,
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


def apply_theme_preset(appearance, preset):
    """Apply a preset using the same color mapping as the Settings UI."""
    from copy import deepcopy
    result = deepcopy(appearance)
    if isinstance(preset, str):
        # Core color presets cover the whole UI and replace the previous theme.
        # Start with neutral surfaces so every hue has its own coherent palette.
        overrides = {}
        foreground_roles = {
            "text", "muted", "brand", "header_button_text", "dock_text",
            "dock_muted", "slider_handle",
        }
        for mode in ("light", "dark"):
            base = build_theme_palette(mode, preset, "neutral")
            colors = {}
            for role in THEME_COLOR_FIELDS:
                if role in foreground_roles:
                    amount = 0.08
                elif role.startswith("dock") or role in {"selection", "item_hover"}:
                    amount = 0.30
                elif role in {"surface", "surface_alt"}:
                    amount = 0.12 if mode == "light" else 0.20
                else:
                    amount = 0.20 if mode == "light" else 0.26
                colors[role] = _blend_theme_colors(base[role], preset, amount)
            overrides[mode] = colors
        result.update(theme_color=preset, theme_preset_name=None,
                      theme_overrides=overrides)
        return result
    mode = "dark" if QColor(preset["background"]).lightness() < 128 else "light"
    background, surface, accent, text, secondary = (QColor(preset[key]) for key in
        ("background", "surface", "accent", "text", "secondaryText"))
    dock = background if mode == "dark" else text
    dock_control = surface if mode == "dark" else dock.lighter(135)
    dock_text = text if mode == "dark" else surface
    raised_base = surface if mode == "dark" else background
    overrides = normalize_theme_overrides(result.get("theme_overrides"))
    colors = overrides.setdefault(mode, {})
    colors.update({
        "window": background.name(), "header": background.name(), "surface": surface.name(),
        "surface_alt": _blend_theme_colors(background, surface, 0.55),
        "raised": _blend_theme_colors(raised_base, text, 0.10),
        "raised_hover": _blend_theme_colors(raised_base, accent, 0.10),
        "pressed": _blend_theme_colors(raised_base, accent, 0.20),
        "text": text.name(), "brand": text.name(), "muted": secondary.name(),
        "border": _blend_theme_colors(background, text, 0.16),
        "field_border": _blend_theme_colors(background, text, 0.22),
        "header_button_text": text.name(), "dock": dock.name(),
        "dock_border": dock.lighter(150).name(), "dock_control": dock_control.name(),
        "dock_control_hover": dock_control.lighter(115).name(),
        "dock_control_border": dock_control.lighter(130).name(),
        "dock_disabled": dock.darker(110).name(), "dock_text": dock_text.name(),
        "dock_muted": secondary.name(), "seek_track": accent.name(),
        "slider_handle": dock_text.name(), "scroll_handle": accent.name(),
    })
    for key, values in normalize_theme_overrides(preset.get("advancedColors", {})).items():
        overrides.setdefault(key, {}).update(values)
    result.update(theme_preset_name=preset["name"], theme_mode=mode,
                  theme_color=accent.name(), theme_overrides=overrides)
    return result


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


class SettingsDialog(SettingsDebugMixin, SettingsTransferMixin, QDialog):
    check_updates_requested = Signal()
    relaunch_wizard_requested = Signal()
    clear_statistics_requested = Signal()

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
        statistics_settings=None,
    ):
        super().__init__(parent)

        self.global_lyrics = dict(global_lyrics or {})
        self._reading_dictionary_draft = None
        self._imported_settings = {}
        self.startup_settings = get_startup_settings({"startup": startup_settings or {}})
        self.statistics_settings = (
            get_statistics_settings() if statistics_settings is None else
            get_statistics_settings({"listening_statistics": statistics_settings})
        )
        self.appearance_settings = get_appearance_settings({"appearance": appearance_settings or {}})
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
        if self.theme_mode not in {"light", "dark", "qt"}:
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
        self._device_lyric_delays = dict(self.playback_settings.get("device_lyric_delays_ms", {}))
        self.karaoke_settings = get_karaoke_settings({"karaoke": karaoke_settings or {}})
        self.local_overrides = dict(local_overrides or {})
        self.current_file = current_file
        self.plugin_manager = plugin_manager
        self.update_settings = get_update_settings({"updates": update_settings or {}})
        self.plugin_toggles = {}

        self.setWindowTitle("Settings")
        self.resize(880, 640)

        tabs = QTabWidget()
        self.category_pages = tabs
        tabs.tabBar().hide()

        # ===== GENERAL =====
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_sections = self._create_settings_sections(
            general_layout, ("Startup", "Listening history", "Updates"), "generalSections"
        )
        general_layout = general_sections["Startup"]

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

        self.relaunch_wizard_button = QPushButton("Relaunch Wizard")
        self.relaunch_wizard_button.clicked.connect(self.relaunch_wizard_requested.emit)
        general_layout.addWidget(self.relaunch_wizard_button, alignment=Qt.AlignmentFlag.AlignLeft)

        general_layout = general_sections["Listening history"]
        self._add_section_heading(general_layout, "Listening statistics")
        self.statistics_enabled_checkbox = QCheckBox("Record listening statistics on this device")
        self.statistics_enabled_checkbox.setChecked(self.statistics_settings.get("enabled", True))
        general_layout.addWidget(self.statistics_enabled_checkbox)
        statistics_note = QLabel("Listening history stays on this device. Turning recording off keeps your existing totals.")
        statistics_note.setWordWrap(True)
        general_layout.addWidget(statistics_note)
        self.clear_statistics_button = QPushButton("Clear listening statistics…")
        self.clear_statistics_button.clicked.connect(self.clear_statistics_requested.emit)
        general_layout.addWidget(self.clear_statistics_button, alignment=Qt.AlignmentFlag.AlignLeft)

        general_layout = general_sections["Updates"]
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
        self.qt_theme_button = QPushButton("Use Qt Theme")
        self.qt_theme_button.setToolTip("Use Qt's default widget style and system palette.")
        for button in (self.light_theme_button, self.dark_theme_button, self.qt_theme_button):
            button.setObjectName("themeModeButton")
            button.setCheckable(True)
        self.theme_mode_group = QButtonGroup(self)
        self.theme_mode_group.setExclusive(True)
        self.theme_mode_group.addButton(self.light_theme_button)
        self.theme_mode_group.addButton(self.dark_theme_button)
        self.theme_mode_group.addButton(self.qt_theme_button)
        self.qt_theme_button.setChecked(self.theme_mode == "qt")
        self.qt_theme_button.clicked.connect(lambda: self._set_theme_mode("qt"))
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
        theme_mode_row.addWidget(self.qt_theme_button)
        theme_mode_row.addStretch(1)
        appearance_layout.addLayout(theme_mode_row)

        theme_layout = appearance_layout
        self.custom_theme_panel = QWidget()
        appearance_layout.addWidget(self.custom_theme_panel)
        appearance_layout = QVBoxLayout(self.custom_theme_panel)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_theme_panel.setEnabled(self.theme_mode != "qt")

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

        appearance_layout = theme_layout
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
        playback_tab = QWidget()
        self._create_playback_controls(QVBoxLayout(playback_tab))

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
        plugins_header = QHBoxLayout()
        plugins_header.addWidget(QLabel("Installed plugins"))
        plugins_header.addStretch()
        get_plugins_button = QPushButton("Get more plugins")
        get_plugins_button.setToolTip("Open the wiki's plugin directory and installation guide")
        get_plugins_button.clicked.connect(
            lambda _checked=False: QDesktopServices.openUrl(
                QUrl("https://pxib.github.io/lrcpluswiki/#plugin-directory")
            )
        )
        plugins_header.addWidget(get_plugins_button)
        plugins_layout.addLayout(plugins_header)
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

        tabs.addTab(general_tab, "General")
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
        self.advanced_tab = self._create_advanced_tab()
        tabs.addTab(self.advanced_tab, "Advanced")
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
        footer = QHBoxLayout()
        footer.addWidget(self._create_settings_debug_button())
        footer.addStretch(1)
        footer.addWidget(button_box)
        layout.addLayout(footer)
        _apply_settings_dialog_style(
            self, self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
        )

    def _index_search_settings(self):
        # Labels set by the shared row helpers stay in sync with the visible UI.
        labels = {
            "update_channel_combo": "Update channel",
            "light_theme_button": "Light color mode",
            "dark_theme_button": "Dark color mode",
            "qt_theme_button": "Use Qt Theme",
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
            "reading_dictionary_button": "Reading dictionaries (furigana)",
        }
        for name, label in labels.items():
            getattr(self, name).setProperty("settingsLabel", label)
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
                if widget.property("settingsSearchSkip"):
                    continue
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
        if hasattr(self, "settings_recorder"):
            self._finish_settings_recording()
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

    def _create_settings_sections(self, layout, titles, name):
        tabs = QTabWidget()
        tabs.setObjectName(name)
        tabs.setAccessibleName("Settings subcategories")
        sections = {}
        for title in titles:
            body = QWidget()
            body_layout = QVBoxLayout(body)
            body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            sections[title] = body_layout
            tabs.addTab(self._scroll_page(body), title)
        layout.addWidget(tabs, 1)
        return sections

    def _create_global_lyrics_controls(self, layout):
        sections = self._create_lyric_sections(layout)
        controls = self._create_lyrics_controls(self.global_lyrics, sections)
        self._create_placeholder_controls(sections["Display"])
        self._add_section_heading(sections["Readings"], "Personal reading dictionaries")
        dictionary_hint = QLabel("Reuse your word readings across songs. Manage named dictionaries, enable or disable them, "
                                "and import or export entries.")
        dictionary_hint.setWordWrap(True)
        sections["Readings"].addWidget(dictionary_hint)
        self.reading_dictionary_button = QPushButton("Manage reading dictionaries…")
        self.reading_dictionary_button.setToolTip("Custom furigana, pinyin and romanization corrections; user dictionary editor")
        self.reading_dictionary_button.clicked.connect(self._manage_reading_dictionaries)
        sections["Readings"].addWidget(self.reading_dictionary_button)
        return controls

    def _manage_reading_dictionaries(self):
        from core.reading_dictionary import load_dictionaries
        from ui.reading_dictionary import ReadingDictionaryDialog
        try:
            data = self._reading_dictionary_draft
            if data is None:
                data = load_dictionaries(reload=True)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "Reading Dictionaries", f"Could not load your dictionaries:\n{error}")
            return
        dialog = ReadingDictionaryDialog(data, self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._reading_dictionary_draft = dialog.data
        finally:
            dialog.deleteLater()

    def accept(self):
        if hasattr(self, "dependencies_tab") and self.dependencies_tab.busy:
            super().accept()
            return
        if self._reading_dictionary_draft is not None:
            from core.reading_dictionary import save_dictionaries
            try:
                save_dictionaries(self._reading_dictionary_draft)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Reading Dictionaries", f"Could not save your dictionaries:\n{error}")
                return
        try:
            self._save_imported_settings()
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "Save Settings", f"Could not save settings:\n{error}")
            return
        super().accept()

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
        controls["continuous_scroll_speed_row"].setEnabled(speed_enabled)

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
        sections = self._create_settings_sections(
            layout, ("Behavior", "Shuffle", "Transitions", "Audio", "Display"), "playbackSections"
        )
        layout = sections["Transitions"]
        self._add_section_heading(layout, "Track transitions")
        self.gapless_checkbox = QCheckBox("Gapless playback")
        self.gapless_checkbox.setChecked(bool(self.playback_settings.get("gapless_playback", False)))
        self.gapless_checkbox.setToolTip(
            "Prebuffer the next track for uninterrupted playback. Requires FFmpeg; "
            "available at normal speed and pitch. Takes effect on the next track."
        )
        layout.addWidget(self.gapless_checkbox)
        self.crossfade_seconds = ThemedSpinBox()
        self.crossfade_seconds.setRange(0, 10)
        self.crossfade_seconds.setSuffix(" s")
        self.crossfade_seconds.setSpecialValueText("Off")
        self.crossfade_seconds.setValue(int(self.playback_settings.get("crossfade_seconds", 0)))
        self.crossfade_seconds.setToolTip(
            "Overlap automatic track transitions. Requires FFmpeg and normal speed and pitch; "
            "takes effect on the next track. Short tracks use a shorter fade."
        )
        self._add_labeled(layout, "Crossfade:", self.crossfade_seconds)
        layout = sections["Audio"]
        self._add_section_heading(layout, "Volume normalization")
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
        self.replaygain_preamp = ThemedSpinBox()
        self.replaygain_preamp.setRange(-24, 24)
        self.replaygain_preamp.setSuffix(" dB")
        self.replaygain_preamp.setValue(int(self.playback_settings.get("replaygain_preamp", 0)))
        self._add_labeled(layout, "ReplayGain preamp:", self.replaygain_preamp)
        self.replaygain_clipping = QCheckBox("Prevent clipping using ReplayGain peak tags")
        self.replaygain_clipping.setChecked(bool(self.playback_settings.get("replaygain_prevent_clipping", True)))
        layout.addWidget(self.replaygain_clipping)
        layout = sections["Display"]
        self._add_section_heading(layout, "Waveform and timing")
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

        layout = sections["Behavior"]
        self._add_section_heading(layout, "Playback order")
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

        layout = sections["Shuffle"]
        self._add_section_heading(layout, "Shuffle preferences")
        self.shuffle_mode_combo = QComboBox()
        self.shuffle_mode_combo.addItem("Tracks", "tracks")
        self.shuffle_mode_combo.addItem("Albums", "albums")
        self.shuffle_mode_combo.addItem("Weighted tracks", "weighted")
        shuffle_mode = self.playback_settings.get("shuffle_mode", "tracks")
        index = self.shuffle_mode_combo.findData(shuffle_mode)
        self.shuffle_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self._add_labeled(layout, "Shuffle:", self.shuffle_mode_combo)
        self.shuffle_mode_combo.setToolTip(
            "When shuffle is enabled, randomize tracks, albums, or use weighted track preferences."
        )
        self.shuffle_favorites_checkbox = QCheckBox("Favor favorite songs")
        self.shuffle_favorites_checkbox.setChecked(self.playback_settings.get("shuffle_prefer_favorites", True))
        self.shuffle_favorites_checkbox.setToolTip("Give favorite songs twice the chance in weighted shuffle.")
        layout.addWidget(self.shuffle_favorites_checkbox)
        self.shuffle_older_checkbox = QCheckBox("Favor songs not played recently")
        self.shuffle_older_checkbox.setChecked(self.playback_settings.get("shuffle_prefer_older", True))
        self.shuffle_older_checkbox.setToolTip(
            "In weighted shuffle, reduce the chance of recently heard songs, recovering over 14 days. "
            "Uses local listening history only while statistics recording is enabled."
        )
        layout.addWidget(self.shuffle_older_checkbox)

        def sync_weighted_controls():
            weighted = self.shuffle_mode_combo.currentData() == "weighted"
            self.shuffle_favorites_checkbox.setEnabled(weighted)
            self.shuffle_older_checkbox.setEnabled(weighted)

        self.shuffle_mode_combo.currentIndexChanged.connect(sync_weighted_controls)
        sync_weighted_controls()

        layout = sections["Behavior"]
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

        layout = sections["Audio"]
        self._add_section_heading(layout, "Output")
        current_device = str(self.playback_settings.get("output_device", "") or "")

        def output_choices():
            return [
                (device.description().strip() or "Unnamed output", bytes(device.id()).hex())
                for device in QMediaDevices.audioOutputs()
            ]

        self.output_device_combo = DeferredOptionsComboBox(
            "System Default",
            output_choices,
            current_device,
            selected_text="Saved output device",
        )
        self._add_labeled(layout, "Output device:", self.output_device_combo)
        self.output_device_combo.setToolTip(
            "Choose which Windows audio output device LRCPlus uses for playback."
        )

        active_device = getattr(self.parent(), "_active_output_device_id", current_device)
        active_name = getattr(self.parent(), "_active_output_device_name", "")
        self.lyric_delay_device_combo = DeferredOptionsComboBox(
            "Choose a device", output_choices, active_device,
            selected_text=active_name or "Saved output device",
        )
        self._add_labeled(layout, "Device to calibrate:", self.lyric_delay_device_combo)
        self.lyric_delay_device_combo.setToolTip(
            "Choose the physical audio output whose lyric delay you want to adjust."
        )
        self.lyric_delay_spin = ThemedSpinBox()
        self.lyric_delay_spin.setRange(-MAX_DEVICE_LYRIC_DELAY_MS, MAX_DEVICE_LYRIC_DELAY_MS)
        self.lyric_delay_spin.setSuffix(" ms")
        self.lyric_delay_spin.setSingleStep(10)
        self.lyric_delay_spin.setToolTip(
            "Positive values show lyrics later. Negative values show them earlier. "
            "Set to 0 ms to clear this device's delay."
        )
        self._add_labeled(layout, "Lyric delay:", self.lyric_delay_spin)
        self.lyric_delay_device_combo.currentIndexChanged.connect(self._show_device_lyric_delay)
        self.lyric_delay_spin.valueChanged.connect(self._edit_device_lyric_delay)
        self._show_device_lyric_delay()

    def _show_device_lyric_delay(self):
        device_id = self.lyric_delay_device_combo.currentData()
        with QSignalBlocker(self.lyric_delay_spin):
            self.lyric_delay_spin.setValue(self._device_lyric_delays.get(device_id, 0))
        self.lyric_delay_spin.setEnabled(bool(device_id))

    def _edit_device_lyric_delay(self, delay):
        device_id = self.lyric_delay_device_combo.currentData()
        if device_id:
            if delay:
                self._device_lyric_delays[device_id] = delay
            else:
                self._device_lyric_delays.pop(device_id, None)

    def _create_lyrics_controls(self, values, sections):
        controls = {}

        def add(key, layout):
            field = LYRIC_FIELDS[key]
            value = values.get(key, LYRICS_DEFAULTS[key])
            editor = self._create_lyric_editor(field, value)
            controls[key] = editor
            if field.kind == "checkbox":
                editor.setText(field.label)
                layout.addWidget(editor)
            elif field.kind == "slider":
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(QLabel(f"{field.label}:"))
                row.addWidget(editor, 1)
                row.addWidget(self._slider_readout(editor, field.unit))
                layout.addWidget(row_widget)
                if key == "continuous_scroll_speed":
                    controls["continuous_scroll_speed_row"] = row_widget
            elif field.kind == "layers":
                layout.addWidget(editor)
            else:
                self._add_labeled(layout, f"{field.label}:", editor)
            return editor

        layout = sections["Display"]
        self._add_section_heading(layout, "Display")
        display = add("scrolling_lyrics", layout)
        direction = add("lyrics_direction", layout)
        alignment = add("lyrics_alignment", layout)

        def sync_vertical_alignment():
            vertical = direction.currentData() == "vertical"
            alignment.model().item(alignment.findData("center")).setEnabled(not vertical)
            if vertical and alignment.currentData() == "center":
                alignment.setCurrentIndex(alignment.findData("left"))

        direction.currentIndexChanged.connect(sync_vertical_alignment)
        sync_vertical_alignment()
        add("lyrics_padding", layout)
        add("vertical_padding", layout)

        rendering_group = QGroupBox("Rendering")
        add("focus_transition", QVBoxLayout(rendering_group))
        layout.addWidget(rendering_group)

        layout = sections["Readings"]
        self._add_section_heading(layout, "Lyric layers")
        note = QLabel("Drag layers to change their display order, or use Move up / Move down. Keep at least one layer checked.")
        note.setWordWrap(True)
        layout.addWidget(note)
        order = add("lyric_layer_order", layout)
        order.setToolTip(
            "Layers render in this order. Furigana stays attached to its words: place it "
            "before Lyrics for above, or after Lyrics for below. Translation uses translations "
            "saved in the lyric file."
        )
        moves = QHBoxLayout()
        for title, delta in (("Move up", -1), ("Move down", 1)):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, delta=delta: order.move_current(delta))
            moves.addWidget(button)
        layout.addLayout(moves)
        layers = {
            "lyrics": "show_lyrics", "romaji": "show_romaji",
            "translation": "show_translation", "furigana": "show_ruby",
        }
        visibility = {}
        for layer, key in layers.items():
            checkbox = add(key, layout)
            checkbox.hide()
            checkbox.setProperty("settingsSearchSkip", True)
            visibility[layer] = checkbox
        order.bind_visibility(visibility)

        # Keep legacy settings readable by older plugins and preference presets.
        for key in ("all_romaji", "ruby_position"):
            editor = self._create_lyric_editor(
                LYRIC_FIELDS[key], False if key == "all_romaji" else values.get(key, LYRICS_DEFAULTS[key])
            )
            controls[key] = editor
            layout.addWidget(editor)
            editor.hide()
            editor.setProperty("settingsSearchSkip", True)

        parser = add("furigana_parser", layout)
        mecab_parse_mode = add("mecab_parse_mode", layout)

        def sync_mecab_mode():
            mecab_parse_mode.setEnabled(parser.currentData() == "mecab_local")

        parser.currentIndexChanged.connect(sync_mecab_mode)
        sync_mecab_mode()

        limited_rendering = add("limited_reading_rendering", layout)
        reading_range = add("limited_reading_range", layout)
        fade_readings = add("fade_in_readings", layout)

        def sync_limited_readings(state):
            reading_range.setEnabled(bool(state))
            fade_readings.setEnabled(bool(state))

        limited_rendering.stateChanged.connect(sync_limited_readings)
        sync_limited_readings(limited_rendering.checkState())
        add("ruby_padding", layout)
        add("ruby_font_size", layout)

        layout = sections["Typography"]
        self._add_section_heading(layout, "Typography")
        for key in LYRIC_SECTIONS["Typography"]:
            add(key, layout)

        layout = sections["Highlighting"]
        self._add_section_heading(layout, "Highlighting")
        add("highlight_current", layout)
        if self.plugin_manager is not None and "karaoke" in self.plugin_manager.plugins:
            karaoke_notice = QLabel(
                "Note: Current lyric highlighting is automatically ignored "
                "while karaoke timing is active."
            )
            karaoke_notice.setWordWrap(True)
            muted_color = build_theme_palette(
                self.theme_mode, self.theme_color, self.theme_surface, self.theme_overrides
            )["muted"]
            karaoke_notice.setStyleSheet(f"color: {muted_color}; font-style: italic;")
            layout.addWidget(karaoke_notice)

        for key in (
            "highlight_animation", "highlight_animation_duration",
            "highlight_animation_out_duration", "highlight_animation_easing", "highlight_color",
        ):
            add(key, layout)

        layout = sections["Scrolling"]
        self._add_section_heading(layout, "Scrolling")
        scrolling_mode = add("scrolling_mode", layout)
        if self.current_file and Path(self.current_file).suffix.lower() == ".txt":
            scrolling_mode.setCurrentIndex(scrolling_mode.findData("continuous"))
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
        duration_based = add("continuous_scroll_duration_based", continuous_layout)
        add("continuous_scroll_speed", continuous_layout)
        add("continuous_scroll_nudge_current", continuous_layout)
        layout.addWidget(continuous_group)
        controls["continuous_group"] = continuous_group

        automatic_group = QGroupBox("Automatic scrolling settings")
        automatic_layout = QVBoxLayout(automatic_group)
        automatic_layout.setContentsMargins(12, 16, 12, 10)
        layout.addWidget(automatic_group)
        controls["automatic_group"] = automatic_group
        for key in ("scroll_animation_speed", "scroll_animation_easing", "scroll_behavior"):
            add(key, automatic_layout)

        display.currentIndexChanged.connect(lambda: self._update_compact_lyrics_controls(controls))
        scrolling_mode.currentIndexChanged.connect(lambda: self._update_compact_lyrics_controls(controls))
        duration_based.toggled.connect(lambda: self._update_compact_lyrics_controls(controls))
        self._update_compact_lyrics_controls(controls)
        return controls

    def get_disabled_plugins(self):
        from core.settings import get_disabled_plugins
        if "disabled_plugins" in self._imported_settings:
            disabled = get_disabled_plugins(self._imported_settings)
        else:
            disabled = get_disabled_plugins()
        disabled.difference_update(self.plugin_toggles)
        return disabled | {
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
        row.addWidget(slider, 1)
        row.addWidget(self._slider_readout(slider, suffix))
        layout.addLayout(row)
        return slider

    def _slider_readout(self, slider, unit):
        label = QLabel(f"{slider.value()} {unit}")
        label.setFixedWidth(55)
        slider.valueChanged.connect(lambda value: label.setText(f"{value} {unit}"))
        return label

    def _create_color_button(self, value):
        button = QPushButton()

        def set_color(color):
            button.setText(str(color))
            button.setStyleSheet(f"QPushButton {{ text-align: left; background: {color}; }}")

        def choose():
            color = QColorDialog.getColor()
            if color.isValid():
                set_color(color.name())

        set_color(value)
        button.clicked.connect(choose)
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
        values = apply_theme_preset({
            "theme_mode": self.theme_mode, "theme_color": self.theme_color,
            "theme_preset_name": self.theme_preset_name, "theme_overrides": self.theme_overrides,
        }, preset)
        self.theme_mode = values["theme_mode"]
        self.theme_color = values["theme_color"]
        self.theme_preset_name = values["theme_preset_name"]
        self.theme_overrides = values["theme_overrides"]
        self.light_theme_button.setChecked(self.theme_mode == "light")
        self.dark_theme_button.setChecked(self.theme_mode == "dark")
        self.qt_theme_button.setChecked(self.theme_mode == "qt")
        self.custom_theme_panel.setEnabled(self.theme_mode != "qt")
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
        self.light_theme_button.setChecked(theme_mode == "light")
        self.dark_theme_button.setChecked(theme_mode == "dark")
        self.qt_theme_button.setChecked(theme_mode == "qt")
        self.custom_theme_panel.setEnabled(theme_mode != "qt")
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
            field = LYRIC_FIELDS[key]
            title = field.local_label or field.label
            setting_name = QLabel(f"{title}:")
            setting_name.setMinimumWidth(185)
            grid.addWidget(setting_name, row_index, 0)

            enabled = QCheckBox("Override")
            enabled.setChecked(key in overrides)

            value = overrides.get(key, global_values.get(key, LYRICS_DEFAULTS[key]))
            editor = self._create_lyric_editor(field, value)
            editor.setProperty("settingsLabel", title)
            editor.setProperty("settingsKeywords", f"{key} {field.label}")
            editor.setProperty("settingsFocusWidget", enabled)

            def sync(state, widget=editor):
                widget.setEnabled(bool(state))

            enabled.toggled.connect(sync)
            sync(enabled.isChecked())

            grid.addWidget(enabled, row_index, 1)
            grid.addWidget(editor, row_index, 2)
            if field.kind == "slider":
                grid.addWidget(self._slider_readout(editor, field.unit), row_index, 3)
            controls[key] = (enabled, editor)

        visibility_keys = ("show_lyrics", "show_romaji", "show_translation", "show_ruby")

        def keep_local_layer_visible():
            if any(editor.isChecked() if override.isChecked()
                   else self.global_controls[key].isChecked()
                   for key in visibility_keys for override, editor in (controls[key],)):
                return
            override, editor = controls["show_lyrics"]
            override.setChecked(True)
            editor.setChecked(True)

        for key in visibility_keys:
            override, editor = controls[key]
            override.clicked.connect(keep_local_layer_visible)
            editor.clicked.connect(keep_local_layer_visible)

        return controls

    def _font_options(self):
        # Both lyric scopes share one enumeration for this dialog. A new dialog
        # can pick up fonts installed since the previous one was closed.
        if not hasattr(self, "_font_choices"):
            self._font_choices = [(family, family) for family in QFontDatabase.families()]
        return self._font_choices

    def _create_lyric_editor(self, field, value):
        kind = field.kind
        if kind == "layers":
            editor = LyricLayerOrder(value)
        elif kind == "choice":
            editor = QComboBox()
            for title, data in field.choices:
                editor.addItem(title, data)
            default = LYRICS_DEFAULTS[field.key]
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(value, str):
                value = value.lower()
            index = editor.findData(value)
            if index < 0:
                index = editor.findData(default)
            editor.setCurrentIndex(index)
        elif kind == "checkbox":
            editor = QCheckBox()
            editor.setChecked(bool(value))
        elif kind == "font":
            editor = DeferredOptionsComboBox("System default", self._font_options, value)
        elif kind == "slider":
            editor = QSlider(Qt.Orientation.Horizontal)
            editor.setRange(*field.bounds)
            editor.setValue(int(value))
        elif kind == "spinbox":
            editor = ThemedSpinBox()
            editor.setRange(*field.bounds)
            editor.setValue(int(value))
            editor.setSuffix(f" {field.unit}")
        elif kind == "color":
            editor = self._create_color_button(value)
        else:
            raise ValueError(f"Unknown lyric settings control: {kind}")

        editor.setProperty("settingsLabel", field.label)
        editor.setProperty("settingsKeywords", field.key)
        if field.tooltip:
            editor.setToolTip(field.tooltip)
        return editor

    def _read_lyric_value(self, key, control):
        kind = LYRIC_FIELDS[key].kind
        if kind in {"choice", "font"}:
            return control.currentData()
        if kind == "checkbox":
            return control.isChecked()
        if kind == "color":
            return control.text().strip()
        return control.value()

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


    def get_statistics_settings(self):
        return {"enabled": self.statistics_enabled_checkbox.isChecked()}


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
            "shuffle_prefer_favorites": self.shuffle_favorites_checkbox.isChecked(),
            "shuffle_prefer_older": self.shuffle_older_checkbox.isChecked(),
            "after_playback": self.after_playback_combo.currentData(),
            "gapless_playback": self.gapless_checkbox.isChecked(),
            "crossfade_seconds": self.crossfade_seconds.value(),
            "replaygain_mode": self.replaygain_combo.currentData(),
            "replaygain_preamp": self.replaygain_preamp.value(),
            "replaygain_prevent_clipping": self.replaygain_clipping.isChecked(),
            "output_device": self.output_device_combo.currentData(),
            "device_lyric_delays_ms": dict(self._device_lyric_delays),
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
            key: self._read_lyric_value(key, self.global_controls[key])
            for key in LYRIC_KEYS
        }

    def get_local_lyrics_overrides(self):
        overrides = {}
        for key, (enabled, editor) in self.local_controls.items():
            if enabled.isChecked():
                overrides[key] = self._read_lyric_value(key, editor)
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
