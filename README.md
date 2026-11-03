lrcplus

media player

idk

## LRCLIB

LRCPlus integrates with LRCLIB for searching and retrieving synchronized and plain-text lyrics. API integration is authorized by LRCLIB under its API Terms of Use.

[View official API authorization](https://lrclib.net/developers/authorizations/zTywhWzql6kFvrFMcWui-xuV0qYCVO3FzKQKPwZGf7U)

## Inspiration

Thank you to [foobar2000](https://www.foobar2000.org/) and [OpenLyrics](https://github.com/jacquesh/foo_openlyrics) foobar2000 [component](https://www.foobar2000.org/components/view/foo_openlyrics) for making this possible. <3

## Plugin Development

LRCPlus supports plugins that extend the player without modifying its core code.

**Current API:** v4 (supports v1–v4)

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

**Getting started:** Create a plugin folder containing `plugin.py` with a `PLUGIN_CLASS`. A `manifest.json` is recommended.

**Dependencies:** LRCPlus can install and manage plugin dependencies, resolve version conflicts, and recover from failed installations.

**Compatibility:** Older plugins remain supported, and plugin-owned resources are automatically cleaned up when unloaded.

For full documentation, see [PLUGIN_API.md](https://github.com/pxib/lrcplus/blob/release/PLUGIN_API.md).

## LRCX format

LRCX (`.lrcx`) is an enhanced version of LRC that supports karaoke-style word timing, furigana, text formatting, and multiple display rows without breaking compatibility with regular lyrics.

### Main features

- Line timestamps: `[00:12.500]` determines when a lyric line starts.
- Karaoke timing: `<00:12.800>` synchronizes individual words or segments with the music.
- Ruby / Furigana: `{金曜日|きんようび}` displays readings above characters.
- Text formatting: Supports bold, italic, strikethrough, underline, and combinations of styles.
- Blockquotes: `> ` adds an indented lyric with a vertical quote bar.
- Visual newlines: `{\n}` splits lyrics into multiple display rows without creating new timestamps.
- Explicit end times: Empty timestamps can mark when a word ends, preserve pauses, or determine when a line disappears.
- Metadata: Supports traditional LRC tags such as `[ar:]`, `[ti:]`, and `[al:]`.

### Example

```
[00:12.500]<00:12.500>**{君|きみ}**<00:13.000>の
<00:13.500>*声*<00:14.000>{届|とど}かない<00:15.200>
```

### Compatibility

Regular `.lrc` files still work. When both `.lrc` and `.lrcx` exist, LRCPlus automatically prefers `.lrcx`. Existing lyric displays remain compatible.

Please refer to the [documentation](https://github.com/pxib/lrcplus/blob/release/LRCX_SPEC.md) for more details.
