"""Public LyricsPlus Plugin API v2.

The API deliberately exposes stable capabilities instead of requiring plugins
to import internal LyricsPlus modules or poke at the main window directly.
Version 2 keeps every v1 capability and adds events, services, persistent
plugin settings, menu actions and playback controls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

PLUGIN_API_VERSION = 2
MIN_PLUGIN_API_VERSION = 1


@dataclass(frozen=True)
class ReadingProviderInfo:
    provider_id: str
    name: str
    description: str = ""


class LyricsPlusAPI:
    def __init__(self, manager: Any, plugin_id: str) -> None:
        self._manager = manager
        self.plugin_id = plugin_id
        self.version = PLUGIN_API_VERSION

    # --------------------------- diagnostics ---------------------------
    def log(self, message: str) -> None:
        self._manager.log(self.plugin_id, message)

    # ----------------------------- events ------------------------------
    def on(self, event_name: str, callback: Callable[..., Any]) -> None:
        """Subscribe to a public application event.

        Built-in events currently include ``app_ready``, ``track_changed``,
        ``position_changed``, ``duration_changed`` and ``playback_state_changed``.
        """
        self._manager.on(event_name, callback, self.plugin_id)

    def emit(self, event_name: str, **kwargs: Any) -> None:
        """Emit a plugin/application event to registered listeners."""
        self._manager.emit(event_name, **kwargs)

    # ---------------------------- services -----------------------------
    def register_service(self, name: str, value: Any) -> None:
        self._manager.register_service(str(name), value, self.plugin_id)

    def get_service(self, name: str, default: Any = None) -> Any:
        return self._manager.get_service(str(name), default)

    # ------------------------- plugin settings -------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._manager.get_plugin_setting(self.plugin_id, str(key), default)

    def set_setting(self, key: str, value: Any) -> None:
        self._manager.set_plugin_setting(self.plugin_id, str(key), value)

    def get_settings(self) -> dict[str, Any]:
        return self._manager.get_plugin_settings(self.plugin_id)

    # ----------------------------- lyrics ------------------------------
    def register_reading_provider(
        self,
        provider_id: str,
        name: str,
        generator: Callable[[str], list],
        *,
        description: str = "",
        can_handle: Callable[..., bool] | None = None,
        batch_generator: Callable[[list[str]], list[list]] | None = None,
    ) -> None:
        if not callable(generator):
            raise TypeError("generator must be callable")
        if can_handle is not None and not callable(can_handle):
            raise TypeError("can_handle must be callable or None")
        provider_id = str(provider_id).strip()
        if not provider_id:
            raise ValueError("provider_id cannot be empty")
        self._manager.register_reading_provider(
            provider_id, str(name), generator, self.plugin_id,
            description=str(description),
            can_handle=can_handle,
            batch_generator=batch_generator,
        )

    # ------------------------------- UI --------------------------------
    def add_menu_action(
        self,
        menu: str,
        text: str,
        callback: Callable[..., Any],
        *,
        shortcut: str | None = None,
    ) -> None:
        """Add an owned action to a named application menu.

        The action is automatically removed when the plugin unloads. Actions
        registered before ``app_ready`` are queued until the main window exists.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._manager.add_menu_action(
            self.plugin_id, str(menu), str(text), callback, shortcut=shortcut,
        )

    # ----------------------------- player ------------------------------
    def get_current_track(self) -> str | None:
        return self._manager.get_current_track()

    def get_position(self) -> int:
        return self._manager.get_position()

    def get_duration(self) -> int:
        return self._manager.get_duration()

    def play(self) -> None:
        self._manager.play()

    def pause(self) -> None:
        self._manager.pause()

    def toggle_playback(self) -> None:
        self._manager.toggle_playback()

    def seek(self, position_ms: int) -> None:
        self._manager.seek(position_ms)
