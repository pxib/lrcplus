from __future__ import annotations

import ast
import importlib.util
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.plugin_api import (
    LyricsPlusAPI, PLUGIN_API_VERSION, MIN_PLUGIN_API_VERSION,
)
from core.settings import load_all_settings, save_all_settings
from lyrics.reading_providers import (
    register_reading_provider as _register_reading_provider,
    unregister_reading_provider as _unregister_reading_provider,
)
from lyrics.furigana import analyze_lyric_words, clear_reading_segment_cache


@dataclass
class PluginContext:
    manager: "PluginManager"
    plugin_id: str

    @property
    def api(self) -> LyricsPlusAPI:
        return self.manager.get_api(self.plugin_id)

    # Compatibility helpers for existing plugins.
    def register_service(self, name: str, value: Any) -> None:
        self.manager.register_service(name, value, self.plugin_id)

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.manager.get_service(name, default)

    def on(self, event_name: str, callback: Callable[..., Any]) -> None:
        self.manager.on(event_name, callback, self.plugin_id)

    def log(self, message: str) -> None:
        self.manager.log(self.plugin_id, message)


@dataclass
class LoadedPlugin:
    plugin_id: str
    instance: Any
    context: PluginContext
    module_name: str
    metadata: dict[str, Any]


class PluginManager:
    def __init__(self, disabled_plugins=None) -> None:
        self.plugins: dict[str, LoadedPlugin] = {}
        self.services: dict[str, tuple[str, Any]] = {}
        self.listeners: dict[str, list[tuple[str, Callable[..., Any]]]] = {}
        self.reading_providers: dict[str, str] = {}
        self._host_window = None
        self._owned_actions: list[tuple[str, Any, Any]] = []
        self._pending_menu_actions: list[tuple[str, str, str, Callable[..., Any], str | None]] = []
        self._owned_buttons: list[tuple[str, Any, Any]] = []
        self._pending_buttons: list[tuple[str, str, Callable[..., Any]]] = []
        self._settings_widgets: list[dict[str, Any]] = []
        self.disabled_plugins = {str(plugin_id) for plugin_id in (disabled_plugins or set())}
        self.plugins_dir: Path | None = None
        self.load_errors: dict[str, str] = {}

    def log(self, plugin_id: str, message: str) -> None:
        print(f"[Plugin:{plugin_id}] {message}")

    def get_api(self, plugin_id: str) -> LyricsPlusAPI:
        return LyricsPlusAPI(self, plugin_id)


    # ---------------------------- host/UI ----------------------------
    def set_host_window(self, window: Any) -> None:
        """Attach the main window and realize any queued plugin UI."""
        self._host_window = window
        for plugin_id, menu, text, callback, shortcut in list(self._pending_menu_actions):
            self._create_menu_action(plugin_id, menu, text, callback, shortcut)
        self._pending_menu_actions.clear()
        for plugin_id, text, callback in list(self._pending_buttons):
            self._create_main_window_button(plugin_id, text, callback)
        self._pending_buttons.clear()

    def add_menu_action(self, owner: str, menu: str, text: str, callback: Callable[..., Any], *, shortcut: str | None = None) -> None:
        if self._host_window is None:
            self._pending_menu_actions.append((owner, menu, text, callback, shortcut))
            return
        self._create_menu_action(owner, menu, text, callback, shortcut)

    def _create_menu_action(self, owner: str, menu: str, text: str, callback: Callable[..., Any], shortcut: str | None) -> None:
        from PySide6.QtGui import QAction, QKeySequence
        menu_bar = self._host_window.menuBar()
        target = None
        for action in menu_bar.actions():
            candidate = action.menu()
            if candidate is not None and candidate.title().replace("&", "").casefold() == menu.replace("&", "").casefold():
                target = candidate
                break
        if target is None:
            target = menu_bar.addMenu(menu)
        action = QAction(text, self._host_window)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        target.addAction(action)
        self._owned_actions.append((owner, target, action))

    def add_main_window_button(
        self,
        owner: str,
        text: str,
        callback: Callable[..., Any],
    ) -> None:
        if self._host_window is None:
            self._pending_buttons.append((owner, text, callback))
            return
        self._create_main_window_button(owner, text, callback)

    def _create_main_window_button(
        self,
        owner: str,
        text: str,
        callback: Callable[..., Any],
    ) -> None:
        from PySide6.QtWidgets import QPushButton

        layout = getattr(self._host_window, "plugin_buttons_layout", None)
        if layout is None:
            raise RuntimeError("The host window does not expose plugin buttons")
        button = QPushButton(text, self._host_window)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self._owned_buttons.append((owner, layout, button))

    # -------------------------- plugin settings -----------------------
    def register_settings_widget(
        self,
        owner: str,
        title: str,
        widget_factory: Callable[..., Any],
        *,
        apply: Callable[[Any], Any] | None = None,
        description: str = "",
        order: int = 0,
    ) -> None:
        # A plugin may register multiple sections, but duplicate titles within
        # the same plugin would make the Settings UI ambiguous.
        key = (owner, title.casefold())
        for entry in self._settings_widgets:
            if (entry["owner"], entry["title"].casefold()) == key:
                raise ValueError(
                    f"Settings widget {title!r} is already registered by {owner!r}"
                )
        self._settings_widgets.append({
            "owner": owner,
            "title": title,
            "description": description,
            "widget_factory": widget_factory,
            "apply": apply,
            "order": int(order),
        })

    def get_settings_widgets(self) -> list[dict[str, Any]]:
        return sorted(
            (dict(entry) for entry in self._settings_widgets),
            key=lambda entry: (
                entry["order"],
                entry["title"].casefold(),
                entry["owner"].casefold(),
            ),
        )

    # -------------------------- plugin settings -----------------------
    def get_plugin_settings(self, plugin_id: str) -> dict[str, Any]:
        payload = load_all_settings()
        all_settings = payload.get("plugin_settings", {})
        values = all_settings.get(plugin_id, {}) if isinstance(all_settings, dict) else {}
        return dict(values) if isinstance(values, dict) else {}

    def get_plugin_setting(self, plugin_id: str, key: str, default: Any = None) -> Any:
        return self.get_plugin_settings(plugin_id).get(key, default)

    def set_plugin_setting(self, plugin_id: str, key: str, value: Any) -> None:
        payload = load_all_settings()
        all_settings = payload.setdefault("plugin_settings", {})
        if not isinstance(all_settings, dict):
            all_settings = {}
            payload["plugin_settings"] = all_settings
        values = all_settings.setdefault(plugin_id, {})
        if not isinstance(values, dict):
            values = {}
            all_settings[plugin_id] = values
        values[key] = value
        save_all_settings(payload)

    # ----------------------------- player -----------------------------
    def _player(self):
        return self._host_window

    def get_current_track(self) -> str | None:
        player = self._player()
        path = getattr(player, "current_audio_path", None) if player is not None else None
        return str(path) if path else None

    def get_current_lyrics(self) -> list[dict[str, Any]]:
        player = self._player()
        lines = getattr(player, "lyric_lines", []) if player is not None else []
        return [
            {
                "text": str(getattr(line, "text", "") or ""),
                "start_ms": round(float(getattr(line, "start", 0) or 0) * 1000),
            }
            for line in lines
        ]

    def analyze_lyric_words(
        self,
        lyrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return analyze_lyric_words(lyrics)

    def get_position(self) -> int:
        player = self._player()
        return int(player.media_player.position()) if player is not None else 0

    def get_duration(self) -> int:
        player = self._player()
        return int(player.media_player.duration()) if player is not None else 0

    def play(self) -> None:
        player = self._player()
        if player is not None:
            player.media_player.play()

    def pause(self) -> None:
        player = self._player()
        if player is not None:
            player.media_player.pause()

    def toggle_playback(self) -> None:
        player = self._player()
        if player is not None and hasattr(player, "toggle_playback"):
            player.toggle_playback()

    def seek(self, position_ms: int) -> None:
        player = self._player()
        if player is not None:
            player.media_player.setPosition(max(0, int(position_ms)))

    def _manifest_metadata(self, child: Path) -> dict[str, Any] | None:
        manifest_file = child / "manifest.json"
        if not manifest_file.exists():
            return None
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("manifest root must be an object")
            return data
        except Exception as error:
            return {"plugin_id": child.name, "name": child.name, "version": "Invalid", "description": f"Invalid manifest: {error}"}

    def discover_plugins(self, plugins_dir: Path) -> list[dict[str, str]]:
        """Return plugin metadata without importing plugin modules."""
        plugins_dir = Path(plugins_dir)
        result = []
        if not plugins_dir.exists():
            return result

        for child in sorted(plugins_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            plugin_file = child / "plugin.py"
            if not plugin_file.exists():
                continue

            metadata = {
                "plugin_id": child.name,
                "name": child.name.replace("_", " ").title(),
                "version": "Unknown",
                "description": "",
                "api_version": PLUGIN_API_VERSION,
            }
            manifest = self._manifest_metadata(child)
            if manifest is not None:
                metadata.update({k: v for k, v in manifest.items() if k in {"plugin_id", "name", "version", "description", "api_version"}})
                metadata["plugin_id"] = child.name
            else:
                try:
                    tree = ast.parse(plugin_file.read_text(encoding="utf-8"))
                    class_name = None
                    for node in tree.body:
                        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLUGIN_CLASS" for t in node.targets) and isinstance(node.value, ast.Name):
                            class_name = node.value.id
                            break
                    if class_name:
                        for node in tree.body:
                            if isinstance(node, ast.ClassDef) and node.name == class_name:
                                for item in node.body:
                                    if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name) and item.targets[0].id in {"name", "version", "description"} and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                                        metadata[item.targets[0].id] = item.value.value
                                break
                except (OSError, SyntaxError, UnicodeError):
                    pass
            result.append(metadata)
        return result

    def discover_and_load(self, plugins_dir: Path) -> None:
        self.plugins_dir = Path(plugins_dir)
        for metadata in self.discover_plugins(self.plugins_dir):
            plugin_id = str(metadata["plugin_id"])
            if plugin_id in self.disabled_plugins:
                continue
            api_version = metadata.get("api_version", PLUGIN_API_VERSION)
            try:
                requested = int(api_version)
                compatible = MIN_PLUGIN_API_VERSION <= requested <= PLUGIN_API_VERSION
            except (TypeError, ValueError):
                compatible = False
            if not compatible:
                self.load_errors[plugin_id] = (
                    f"Requires Plugin API v{api_version}; supported versions are "
                    f"v{MIN_PLUGIN_API_VERSION} through v{PLUGIN_API_VERSION}."
                )
                self.log(plugin_id, self.load_errors[plugin_id])
                continue
            try:
                self.load_from_file(plugin_id, self.plugins_dir / plugin_id / "plugin.py", metadata)
            except Exception as error:
                self.load_errors[plugin_id] = f"{type(error).__name__}: {error}"
                self.log(plugin_id, f"failed to load: {self.load_errors[plugin_id]}")
                traceback.print_exc()

    def load_from_file(self, plugin_id: str, plugin_file: Path, metadata: dict[str, Any] | None = None) -> None:
        if plugin_id in self.plugins:
            raise ValueError(f"Plugin already loaded: {plugin_id}")
        module_name = f"_lyricsplus_plugin_{plugin_id}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load plugin: {plugin_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            plugin_class = getattr(module, "PLUGIN_CLASS", None)
            if plugin_class is None:
                raise RuntimeError(f"{plugin_file} does not define PLUGIN_CLASS")
            instance = plugin_class()
            context = PluginContext(self, plugin_id)
            loaded = LoadedPlugin(plugin_id, instance, context, module_name, metadata or {"plugin_id": plugin_id})
            self.plugins[plugin_id] = loaded
            hook = getattr(instance, "on_load", None)
            if callable(hook):
                hook(context)
        except Exception:
            sys.modules.pop(module_name, None)
            self._remove_owned(plugin_id)
            raise

    def unload(self, plugin_id: str) -> None:
        loaded = self.plugins.get(plugin_id)
        if loaded is None:
            return
        hook = getattr(loaded.instance, "on_unload", None)
        if callable(hook):
            try:
                hook(loaded.context)
            except Exception as error:
                self.log(plugin_id, f"unload failed: {error!r}")
        self._remove_owned(plugin_id)
        self.plugins.pop(plugin_id, None)
        sys.modules.pop(loaded.module_name, None)

    def unload_all(self) -> None:
        for plugin_id in list(self.plugins):
            self.unload(plugin_id)

    def register_reading_provider(self, provider_id: str, name: str, generator: Callable[..., Any], owner: str, *, description: str = "", can_handle: Callable[..., bool] | None = None, batch_generator: Callable[..., Any] | None = None) -> None:
        existing = self.reading_providers.get(provider_id)
        if existing is not None and existing != owner:
            raise ValueError(f"Reading provider {provider_id!r} already registered by {existing!r}")
        _register_reading_provider(provider_id, name, generator, description=description, can_handle=can_handle, batch_generator=batch_generator)
        # Earlier fallback results may have been cached before this optional
        # provider became available.
        clear_reading_segment_cache()
        self.reading_providers[provider_id] = owner

    def register_service(self, name: str, value: Any, owner: str) -> None:
        if name in self.services:
            existing_owner, _ = self.services[name]
            raise ValueError(f"Service {name!r} already registered by {existing_owner!r}")
        self.services[name] = (owner, value)

    def get_service(self, name: str, default: Any = None) -> Any:
        entry = self.services.get(name)
        return default if entry is None else entry[1]

    def on(self, event_name: str, callback: Callable[..., Any], owner: str) -> None:
        self.listeners.setdefault(event_name, []).append((owner, callback))

    def emit(self, event_name: str, **kwargs: Any) -> None:
        for plugin_id, callback in list(self.listeners.get(event_name, [])):
            try:
                callback(**kwargs)
            except Exception as error:
                self.log(plugin_id, f"event {event_name!r} failed: {error!r}")

    def _remove_owned(self, plugin_id: str) -> None:
        self._pending_menu_actions = [item for item in self._pending_menu_actions if item[0] != plugin_id]
        self._pending_buttons = [item for item in self._pending_buttons if item[0] != plugin_id]
        self._settings_widgets = [
            entry for entry in self._settings_widgets
            if entry["owner"] != plugin_id
        ]
        for owner, menu, action in list(self._owned_actions):
            if owner == plugin_id:
                menu.removeAction(action)
                action.deleteLater()
                self._owned_actions.remove((owner, menu, action))
        for owner, layout, button in list(self._owned_buttons):
            if owner == plugin_id:
                layout.removeWidget(button)
                button.deleteLater()
                self._owned_buttons.remove((owner, layout, button))
        for provider_id, owner in list(self.reading_providers.items()):
            if owner == plugin_id:
                _unregister_reading_provider(provider_id)
                self.reading_providers.pop(provider_id, None)
        for name, (owner, _) in list(self.services.items()):
            if owner == plugin_id:
                self.services.pop(name, None)
        for event_name, callbacks in list(self.listeners.items()):
            kept = [pair for pair in callbacks if pair[0] != plugin_id]
            if kept:
                self.listeners[event_name] = kept
            else:
                self.listeners.pop(event_name, None)
