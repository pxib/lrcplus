# LyricsPlus Plugin API

> **Current API version:** 3
> **Supported plugin API versions:** 1–3

The LyricsPlus Plugin API lets plugins integrate with the player without importing internal UI or manager code.

Plugins can currently:

- subscribe to application events;
- emit custom events;
- register and consume services;
- store persistent plugin settings;
- register plugin settings widgets in Settings > Plugins;
- register automatic lyric-reading providers;
- add menu actions;
- add buttons to the main playback controls;
- inspect and control playback;
- read a snapshot of current lyrics and analyze its words;
- cleanly participate in the plugin lifecycle.

---

## 1. Quick start

A plugin lives in its own folder:

```text
plugins/
├── core/                    # Official LyricsPlus plugins
│   └── my_plugin/
│       ├── plugin.py
│       └── manifest.json    # optional, recommended
└── community/               # Unofficial community plugins
    └── another_plugin/
        ├── plugin.py
        └── manifest.json
```

Each plugin folder must have a unique folder name, which is used as its plugin
ID. Plugins placed directly in `plugins/` remain supported for compatibility
and are listed as Community plugins.

A minimal plugin:

```python
class MyPlugin:
    name = "My Plugin"
    version = "1.0.0"
    description = "My first LyricsPlus plugin."

    def on_load(self, context):
        context.api.log("Hello from My Plugin!")


PLUGIN_CLASS = MyPlugin
```

The loader requires `plugin.py` and a module-level `PLUGIN_CLASS`.

---

## 2. Lifecycle

### `on_load(context)`

Called after the plugin module is loaded and its plugin class is instantiated.

```python
def on_load(self, context):
    self.context = context
    context.api.log("Plugin loaded")
```

Use this to:

- register event listeners;
- register services;
- register reading providers;
- load optional dependencies;
- register menu actions.

### `on_unload(context)`

Optional. Called when the plugin is unloaded.

```python
def on_unload(self, context):
    context.api.log("Plugin unloaded")
```

Resources registered through the public API are also cleaned up automatically where supported. This includes:

- event listeners;
- registered services;
- registered reading providers;
- plugin-owned menu actions.

You should still clean up resources owned directly by your plugin, such as external connections, timers, or custom objects.

---

## 3. Plugin context

Plugins receive a `context` object.

### Recommended: `context.api`

The public API is available through:

```python
context.api
```

Example:

```python
context.api.log("Hello!")
context.api.on("track_changed", self.on_track_changed)
```

### Compatibility helpers

For older/simple plugins, the context currently also exposes:

```python
context.log(...)
context.on(...)
context.register_service(...)
context.get_service(...)
```

New plugins should prefer `context.api`.

---

# 4. Diagnostics

## `api.log(message)`

Writes a message using the plugin's ID as the log source.

```python
context.api.log("Connected successfully")
```

Example output:

```text
[Plugin:my_plugin] Connected successfully
```

---

# 5. Events

## Subscribe

```python
api.on(event_name, callback)
```

Example:

```python
def on_load(self, context):
    context.api.on("track_changed", self.on_track_changed)

def on_track_changed(self, path):
    context.api.log(f"Now playing: {path}")
```

## Emit

```python
api.emit(event_name, **kwargs)
```

Plugins may emit their own events:

```python
context.api.emit(
    "my_plugin_finished",
    result="success",
)
```

## Built-in events

### `app_ready`

Emitted when the main application window is ready.

Arguments:

```python
def on_app_ready(window):
    ...
```

Subscribe:

```python
context.api.on("app_ready", self.on_app_ready)
```

---

### `track_changed`

Emitted when a new audio file is loaded.

Arguments:

```python
def on_track_changed(path):
    ...
```

---

### `position_changed`

Emitted during playback position updates.

Arguments:

```python
def on_position_changed(position_ms):
    ...
```

`position_ms` is an integer in milliseconds.

---

### `duration_changed`

Emitted when media duration becomes available or changes.

Arguments:

```python
def on_duration_changed(duration_ms):
    ...
```

`duration_ms` is an integer in milliseconds.

---

### `playback_state_changed`

Emitted when the playback state changes.

Arguments:

```python
def on_playback_state_changed(state):
    ...
```

`state` is emitted as a string.

---

## Event errors

If a listener raises an exception, LyricsPlus catches the exception and logs it under the owning plugin instead of letting one plugin crash the event dispatcher.

---

# 6. Services

Services let plugins expose reusable objects or functionality to other plugins.

## Register a service

```python
api.register_service(name, value)
```

Example:

```python
context.api.register_service(
    "my_plugin.converter",
    MyConverter(),
)
```

Service names should be unique and namespaced.

Good:

```text
lyrics.display
ui.settings_dialog
my_plugin.converter
```

Avoid:

```text
converter
tool
service
```

## Get a service

```python
service = context.api.get_service(
    "my_plugin.converter"
)
```

A default can be provided:

```python
service = context.api.get_service(
    "my_plugin.converter",
    default=None,
)
```

Example:

```python
editor_class = context.api.get_service("ui.lrc_editor")

if editor_class is not None:
    dialog = editor_class(...)
```

A service name cannot be registered twice. Attempting to register an already-owned name raises `ValueError`.

Services owned by a plugin are removed automatically when that plugin unloads.

---

# 7. Persistent plugin settings

Each plugin gets its own persistent settings namespace.

## Read one value

```python
value = context.api.get_setting(
    "enabled",
    default=True,
)
```

## Save one value

```python
context.api.set_setting(
    "enabled",
    False,
)
```

## Read all plugin settings

```python
settings = context.api.get_settings()
```

This returns a dictionary containing the current settings for that plugin.

Plugins should keep values reasonably serializable because settings are stored by the application's settings system.

Example:

```python
class MyPlugin:
    def on_load(self, context):
        self.context = context
        self.enabled = context.api.get_setting(
            "enabled",
            True,
        )

    def toggle(self):
        self.enabled = not self.enabled
        self.context.api.set_setting(
            "enabled",
            self.enabled,
        )
```

---


# 7.1 Plugin settings UI

Plugins can register a QWidget to appear inside **Settings > Plugins**.

```python
from PySide6.QtWidgets import QCheckBox

class MyPlugin:
    def on_load(self, context):
        self.context = context
        context.api.register_settings_widget(
            "My Plugin",
            self.create_settings_widget,
            apply=self.apply_settings_widget,
            description="Configure My Plugin.",
        )

    def create_settings_widget(self, parent):
        widget = QCheckBox("Enable feature", parent)
        widget.setChecked(
            self.context.api.get_setting("enabled", True)
        )
        return widget

    def apply_settings_widget(self, widget):
        self.context.api.set_setting(
            "enabled", widget.isChecked()
        )
```

## `api.register_settings_widget(...)`

```python
api.register_settings_widget(
    title,
    widget_factory,
    *,
    apply=None,
    description="",
    order=0,
)
```

- `title`: section heading shown in **Settings > Plugins**.
- `widget_factory(parent)`: returns a `QWidget` owned by the plugin.
- `apply(widget)`: optional callback run only when the main Settings dialog is accepted.
- `description`: optional text displayed below the section heading.
- `order`: lower values appear first.

Settings widgets are owned by the registering plugin and are automatically removed when the plugin unloads.

# 8. Automatic reading providers

Reading providers generate ruby/reading segments for lyrics.

This is the mechanism used for optional systems such as Korean romanization and Cantonese readings.

## Register a provider

```python
api.register_reading_provider(
    provider_id,
    name,
    generator,
    description="",
    can_handle=None,
)
```

Example:

```python
def build(text):
    return [
        {
            "text": text,
            "reading": "example",
        }
    ]

context.api.register_reading_provider(
    "example",
    "Example Readings",
    build,
    description="Example automatic reading provider.",
)
```

### Arguments

| Argument | Description |
|---|---|
| `provider_id` | Unique machine-readable identifier |
| `name` | Human-readable provider name |
| `generator` | Callable that generates reading segments |
| `description` | Optional description |
| `can_handle` | Optional callable used for Auto detection |

A duplicate provider ID owned by another plugin raises `ValueError`.

---

## Generator contract

A generator receives:

```python
text: str
```

and should return a list of dictionaries.

Each dictionary has:

```python
{
    "text": "surface text",
    "reading": "pronunciation",
}
```

Text without a reading should remain explicit:

```python
{
    "text": " ",
    "reading": None,
}
```

For example:

```python
def build(text):
    return [
        {"text": "안녕하세요", "reading": "annyeonghaseyo"},
        {"text": "!", "reading": None},
    ]
```

### Important: readings and karaoke are different things

A reading segment does **not** create karaoke timing.

This:

```python
{"text": "안녕하세요", "reading": "annyeonghaseyo"}
```

means:

> Display `annyeonghaseyo` as the reading for `안녕하세요`.

It does **not** mean the lyric has been split into a separate karaoke chunk.

A provider should only describe text and readings.

---

## Automatic provider detection with `can_handle`

To participate in Auto mode, provide a matcher:

```python
def can_handle(text, *, language=None):
    return True
```

Then register it:

```python
context.api.register_reading_provider(
    "example",
    "Example",
    build,
    can_handle=can_handle,
)
```

The matcher receives the lyric text and may also receive a keyword argument:

```python
language
```

For compatibility, LyricsPlus also supports matchers that only accept:

```python
def can_handle(text):
    ...
```

Example: detecting Hangul without the core application knowing anything about Korean:

```python
def can_handle(text, *, language=None):
    return any(
        "\uac00" <= char <= "\ud7a3"
        for char in str(text or "")
    )
```

In Auto mode, the first registered provider whose `can_handle()` returns `True` is selected.

Provider registration order therefore matters if multiple providers can claim the same text.

---

## Explicit provider mode

A registered provider can also be selected directly instead of using Auto mode.

For example, if a provider ID is:

```text
jyutping
```

the reading system can select that provider explicitly and call its generator for the lyric text.

This allows one plugin to expose multiple reading systems.

---

## Provider cleanup

Reading providers are owned by the plugin that registered them.

When the plugin unloads, its providers are automatically removed.

LyricsPlus also clears cached reading segments when a provider is registered so previously cached fallback results do not continue hiding a newly available provider.

---

# 9. Menu actions

Plugins can add actions to the application's menu bar.

```python
api.add_menu_action(
    menu,
    text,
    callback,
    shortcut=None,
)
```

Example:

```python
def on_load(self, context):
    context.api.add_menu_action(
        "Tools",
        "My Plugin",
        self.open_dialog,
        shortcut="Ctrl+Alt+M",
    )
```

If the main window is not ready yet, the action is queued and added after the host window becomes available.

If the named menu does not already exist, LyricsPlus creates it.

Plugin-owned actions are automatically removed when the plugin unloads.

## Main-window buttons

Plugins can add a button to the main playback controls:

```python
api.add_main_window_button("Flashcard", callback)
```

Buttons registered before the main window is ready are queued. Buttons are
owned by the registering plugin and removed when it unloads.

---

# 10. Playback API

The API exposes basic playback information and controls.

## Current track

```python
path = context.api.get_current_track()
```

Returns a string path or `None`.

## Current lyrics

```python
lines = context.api.get_current_lyrics()
```

Returns a detached list of dictionaries containing each lyric line's `text`
and `start_ms` timestamp. The snapshot can be passed to
`context.api.analyze_lyric_words(lines)`.

## Analyze lyric words

```python
words = context.api.analyze_lyric_words(lines)
```

Returns dictionaries with `text`, `reading`, `context`, `line_index`, and
`start_ms`. Analysis uses the application's configured tokenizer and may be
slow for a full song, so plugins should call it from a worker thread.

## Position

```python
position_ms = context.api.get_position()
```

Returns an integer in milliseconds.

## Duration

```python
duration_ms = context.api.get_duration()
```

Returns an integer in milliseconds.

## Play

```python
context.api.play()
```

## Pause

```python
context.api.pause()
```

## Toggle playback

```python
context.api.toggle_playback()
```

## Seek

```python
context.api.seek(60_000)
```

The position is specified in milliseconds.

---

# 11. Plugin manifest

A `manifest.json` file is optional but recommended.

Example:

```json
{
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "Does something useful.",
    "api_version": 3
}
```

The folder name is used as the plugin ID.

For example:

```text
plugins/my_plugin/
```

loads as:

```text
my_plugin
```

The manifest's `plugin_id` is not used to override the folder name.

## API compatibility

LyricsPlus currently supports:

```text
Plugin API v1 through v3
```

A plugin requesting an unsupported API version is not loaded.

For new plugins, use:

```json
"api_version": 3
```

Older v1 plugins remain supported by the current API.

---

# 12. Complete example: reading provider

```python
from __future__ import annotations


class ExampleReadingsPlugin:
    name = "Example Readings"
    version = "1.0.0"
    description = "An example LyricsPlus reading provider."

    def on_load(self, context):
        def can_handle(text, *, language=None):
            return "★" in str(text or "")

        def build(text):
            result = []

            for char in str(text or ""):
                if char == "★":
                    result.append({
                        "text": char,
                        "reading": "star",
                    })
                else:
                    result.append({
                        "text": char,
                        "reading": None,
                    })

            return result

        context.api.register_reading_provider(
            "example_readings",
            "Example Readings",
            build,
            description="Adds a reading to ★.",
            can_handle=can_handle,
        )

        context.api.log(
            "Example reading provider registered"
        )

    def on_unload(self, context):
        context.api.log("Example plugin unloaded")


PLUGIN_CLASS = ExampleReadingsPlugin
```

Suggested `manifest.json`:

```json
{
  "name": "Example Readings",
  "version": "1.0.0",
  "description": "An example LyricsPlus reading provider.",
    "api_version": 3
}
```

---

# 13. API reference

| Method | Description |
|---|---|
| `log(message)` | Write a plugin-scoped log message |
| `on(event_name, callback)` | Subscribe to an event |
| `emit(event_name, **kwargs)` | Emit an event |
| `register_service(name, value)` | Register a shared service |
| `get_service(name, default=None)` | Get a registered service |
| `get_setting(key, default=None)` | Read a plugin setting |
| `set_setting(key, value)` | Save a plugin setting |
| `get_settings()` | Get all settings for the current plugin |
| `register_settings_widget(...)` | Add a plugin-owned section to Settings > Plugins |
| `register_reading_provider(...)` | Register an automatic reading provider |
| `add_menu_action(...)` | Add a plugin-owned menu action |
| `add_main_window_button(...)` | Add a plugin-owned main-window button |
| `get_current_track()` | Get the current track path |
| `get_current_lyrics()` | Get a snapshot of current lyric lines |
| `analyze_lyric_words(lines)` | Analyze lyric words and readings |
| `get_position()` | Get playback position in milliseconds |
| `get_duration()` | Get track duration in milliseconds |
| `play()` | Start playback |
| `pause()` | Pause playback |
| `toggle_playback()` | Toggle playback |
| `seek(position_ms)` | Seek to a position in milliseconds |

---

# 14. Best practices

### Use the public API

Do this:

```python
context.api.register_service(...)
context.api.on(...)
context.api.seek(...)
```

Avoid importing internals such as:

```python
from core.plugin_manager import PluginManager
from ui.main_window import Player
```

unless you are modifying LyricsPlus itself rather than writing a plugin.

### Namespace your identifiers

Prefer:

```text
my_plugin.converter
my_plugin.service
```

over generic names.

### Make optional dependencies optional

For example:

```python
try:
    import some_library
except Exception as error:
    context.api.log(
        f"Optional dependency unavailable: {error}"
    )
    return
```

This allows the application to continue running even if the plugin's optional dependency is missing.

### Keep generators deterministic

A reading generator should return segments in the same order as the source text.

For mixed-script lyrics, preserve unsupported text with:

```python
{
    "text": original_text,
    "reading": None,
}
```

### Do not use reading generation to create karaoke chunks

Reading providers own:

```text
surface text → reading
```

Karaoke timing is a separate system:

```text
surface text → timing
```

Keeping those systems separate prevents automatic romanization or furigana from unexpectedly splitting lyrics into karaoke segments.

---

# Version history

## API v2

Adds public support for:

- events;
- custom event emission;
- services;
- persistent plugin settings;
- menu actions;
- playback controls;
- optional automatic reading providers with `can_handle`;
- automatic cleanup of plugin-owned resources.

## API v1

The original plugin API capabilities remain supported for backward compatibility.
