lrcplus

media player

idk

## Community wiki

I have a wiki now! To get started, please refer to the [wiki](https://pxib.github.io/lrcpluswiki/) to get detailed explanations on specific topics!

## User manual

Choose **Help → Community wiki** in the app, or press **F1**, to open the [official community wiki](https://pxib.github.io/lrcpluswiki/). For offline help, choose **Help → Offline manual** or open [docs/manual/index.html](docs/manual/index.html) directly. The bundled manual includes offline search and needs no server or installation.

## Settings backups

Open **Settings → Advanced** to export preferences as JSON or import a saved settings file. Exports include unsaved edits, saved themes, and plugin preferences. Review imported values in Settings, then click **OK** to apply or **Cancel** to discard them. Restart LRCPlus to apply imported plugin settings.

Music libraries, playlists, history, per-song lyric overrides, and reading dictionaries are excluded. Folder paths may need updating when moving preferences to another computer.

Use **Restore factory defaults…** in the same tab to load the app's default preferences for review. Click **OK** to apply or **Cancel** to discard. Saved themes, folders, plugin settings, and personal data are kept.

The factory baseline uses dark **Nordic Frost**, accent **#88c0d0**, the recorded light/dark colors, zero background blur, and playlist restoration. Listening-statistics recording and automatic update checks start disabled. These defaults apply to fresh installations and the restore action; explicitly saved preferences take precedence. The selected overrides are maintained in [core/factory_defaults.py](core/factory_defaults.py).

To choose a new factory baseline, open the **Debug** menu at the bottom of Settings and select **Record settings changes**. Adjust preferences across the tabs, then select **Stop recording**. **View recording…** shows the before/after history; **Copy changes as JSON** copies final changed values, and **Copy factory defaults** copies the complete chosen setup in the settings-export format. Recordings stay in memory across Settings windows, until cleared or the app closes, so you can apply changes and test the player while choosing defaults. The recorder includes unsaved edits, excludes personal paths and plugin data, and does not save preferences or replace the factory baseline.

## LRCLIB

LRCPlus integrates with LRCLIB for searching and retrieving synchronized and plain-text lyrics. API integration is authorized by LRCLIB under its API Terms of Use.

[View official API authorization](https://lrclib.net/developers/authorizations/zTywhWzql6kFvrFMcWui-xuV0qYCVO3FzKQKPwZGf7U)

## Inspiration (やる気)

Thank you to [foobar2000](https://www.foobar2000.org/) and [OpenLyrics](https://github.com/jacquesh/foo_openlyrics) foobar2000 [component](https://www.foobar2000.org/components/view/foo_openlyrics) for making this possible. <3

## Plugin Development (プラグインの開発)

LRCPlus supports plugins that extend the player without modifying its core code.

**Current API (今のAPI):** v6 (supports v1–v6)

Plugins can:
- Listen to and emit application events.
- Control playback (play, pause, seek).
- Access and analyze lyrics.
- Add custom reading/romanization providers.
- Add menu actions and playback buttons.
- Create custom settings interfaces.
- Store persistent settings.
- Share services with other plugins.
- Import audio files and attach lyrics (v4).
- Declare Python dependencies managed by LRCPlus.

**Getting started (始めるよ):** Create a plugin folder containing `plugin.py` with a `PLUGIN_CLASS`. A `manifest.json` is recommended.

**Dependencies (依存関係):** LRCPlus can install and manage plugin dependencies, resolve version conflicts, and recover from failed installations.

**Compatibility (互換性):** Older plugins remain supported, and plugin-owned resources are automatically cleaned up when unloaded.

For full documentation, see [PLUGIN_API.md](https://github.com/pxib/lrcplus/blob/release/PLUGIN_API.md).

## LRCX format (LRCXフォーマット)

LRCX (`.lrcx`) is an enhanced version of LRC that supports karaoke-style word timing, furigana, text formatting, and multiple display rows without breaking compatibility with regular lyrics.

### Main features (主な機能)

- Line timestamps: `[00:12.500]` determines when a lyric line starts.
- Karaoke timing: `<00:12.800>` synchronizes individual words or segments with the music.
- Ruby / Furigana: `{金曜日|きんようび}` displays readings above characters.
- Text formatting: Supports bold, italic, strikethrough, underline, and combinations of styles.
- Blockquotes: `> ` adds an indented lyric with a vertical quote bar.
- Visual newlines: `{\n}` splits lyrics into multiple display rows without creating new timestamps.
- Explicit end times: Empty timestamps can mark when a word ends, preserve pauses, or determine when a line disappears.
- Metadata: Supports traditional LRC tags such as `[ar:]`, `[ti:]`, and `[al:]`.

### Example (例)

```
[00:12.500]<00:12.500>**{君|きみ}**<00:13.000>の<00:13.500>*声*<00:14.000>{届|とど}かない<00:15.200>
```

### Compatibility (互換性)

Regular `.lrc` files still work. When both `.lrc` and `.lrcx` exist, LRCPlus automatically prefers `.lrcx`. Existing lyric displays remain compatible.

Please refer to the [documentation](https://github.com/pxib/lrcplus/blob/release/LRCX_SPEC.md) for more details.

## Finally (結局)
I've been working on this project for around 3 months now. When I first had an idea to add Japanese furigana onto OpenLyrics, I went ahead to install the Visual Studio IDE and the [foobar2000 SDK](https://www.foobar2000.org/SDK). But, *it didn't work in a way I expected.* Version mismatches, missing dependencies and it was such a hassle. (I didn't know C++ either.)
So I uninstalled it and thought: *"What if I made my own in Python?"* and made "Furigana Player." It was the first prototype and you can visit that by [clicking on the LRCPlus text 7 times in the main screen!](https://github.com/pxib/lrcplus/blob/release/assets/apps/furiganaplayer.py) I continued to work on it until we got here. :3

I will probably work on this until I'm satisfied with it, but for now... enjoy the updates! :D
