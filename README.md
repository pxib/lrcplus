lrcplus

media player

idk

## User manual

Open [docs/manual/index.html](docs/manual/index.html) in your browser for the local user manual. It includes offline search and needs no server or installation.

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
