"""Public LRCPlus Plugin API v4.

The API deliberately exposes stable capabilities instead of requiring plugins
to import internal LRCPlus modules or poke at the main window directly.
Version 3 keeps earlier capabilities and adds main-window buttons and lyric
word analysis alongside events, services, settings, menu actions and playback.
Version 4 adds local audio imports and lyric attachments without restarting playback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

PLUGIN_API_VERSION = 4
MIN_PLUGIN_API_VERSION = 1


@dataclass(frozen=True)
class ReadingProviderInfo:
    provider_id: str
    name: str
    description: str = ""


class LRCPlusAPI:
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

    def register_settings_widget(
        self,
        title: str,
        widget_factory: Callable[..., Any],
        *,
        apply: Callable[[Any], Any] | None = None,
        description: str = "",
        order: int = 0,
    ) -> None:
        """Register a plugin-owned widget for the plugin's standalone Settings window.

        ``widget_factory(parent)`` should return a QWidget. ``apply(widget)``
        is called only when the main Settings dialog is accepted. The callback
        can persist values with ``get_setting`` / ``set_setting`` or update
        live plugin state.

        Registrations are owned by the plugin and are removed automatically
        when the plugin unloads.
        """
        if not callable(widget_factory):
            raise TypeError("widget_factory must be callable")
        if apply is not None and not callable(apply):
            raise TypeError("apply must be callable or None")
        title = str(title).strip()
        if not title:
            raise ValueError("title cannot be empty")
        self._manager.register_settings_widget(
            self.plugin_id,
            title,
            widget_factory,
            apply=apply,
            description=str(description),
            order=int(order),
        )

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
        checkable: bool = False,
    ) -> None:
        """Add an owned action to a named application menu.

        The action is automatically removed when the plugin unloads. Actions
        registered before ``app_ready`` are queued until the main window exists.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._manager.add_menu_action(
            self.plugin_id, str(menu), str(text), callback,
            shortcut=shortcut, checkable=checkable,
        )

    def set_menu_action_checked(self, text: str, checked: bool) -> None:
        """Set the checked state of this plugin's named menu action."""
        self._manager.set_menu_action_checked(
            self.plugin_id, str(text), bool(checked),
        )

    def add_main_window_button(
        self,
        text: str,
        callback: Callable[..., Any],
    ) -> None:
        """Add a plugin-owned button to the main playback controls."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._manager.add_main_window_button(
            self.plugin_id, str(text), callback,
        )

    # ----------------------------- player ------------------------------
    def get_current_track(self) -> str | None:
        return self._manager.get_current_track()

    def import_audio_files(self, paths: list[str], *, add_to_queue: bool = True) -> list[str]:
        """Import existing local audio into the library, optionally enqueueing.

        Call on the GUI thread. Playback continues uninterrupted. Missing or
        unsupported files raise ValueError before the library/queue changes.
        """
        return self._manager.import_audio_files(paths, add_to_queue=add_to_queue)

    def attach_lyrics(self, audio_path: str, lyrics_path: str) -> None:
        """Associate local lyrics with audio, refreshing the current track's
        lyrics without restarting playback. Call on the GUI thread.
        """
        self._manager.attach_lyrics(audio_path, lyrics_path)

    def get_current_lyrics(self) -> list[dict[str, Any]]:
        """Return a detached snapshot of the current lyric lines."""
        return self._manager.get_current_lyrics()

    def analyze_lyric_words(
        self,
        lyrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Tokenize a lyric snapshot into words and readings."""
        return self._manager.analyze_lyric_words(lyrics)

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
