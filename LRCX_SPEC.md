# LRCX v1

LRCX is an extension of LRC for storing line timing, segment timing, and optional ruby text.

## File extension

`.lrcx`

## Metadata

Classic LRC-style metadata is allowed and ignored by the lyric timing parser for now:

```text
[ar:Artist]
[ti:Title]
[al:Album]
```

## Timed line

```text
[00:12.500]<00:12.500>君<00:12.800>の<00:13.000>ことを
```

The square-bracket timestamp is the line start. Each angle-bracket timestamp is the start of a karaoke segment.

## Ruby

A segment can contain embedded ruby:

```text
[00:12.500]<00:12.500>{君|きみ}<00:12.800>の<00:13.000>{忘|わす}れ
```

`{base|ruby}` means the visible lyric text is `base` and the ruby reading is `ruby`.

## Text formatting

Use `**bold**`, `*italic*`, `~~strikethrough~~`, or `<u>underline</u>` in LRCX
lyric text. Styles can be nested, span visual newlines, and continue across
karaoke timestamps. `***bold italic***` combines bold and italic. Unmatched
markers stay visible; backslashes escape literal `*`, `~`, `<`, `>`, and `\`.
Only these inline styles are interpreted.

```text
[00:12.500]**強い** *気持ち*{\n}***最高！***
[00:15.000]**{君|きみ}<00:16.000>の声**
[00:18.000]~~昨日~~ <u>今日</u>
```

The Lyrics tab has formatting buttons above the text field. Shortcuts are
Ctrl+B (bold), Ctrl+I (italic), Ctrl+Shift+X (strikethrough), and Ctrl+U
(underline). Select text to wrap it in markers, or insert an empty pair and type
between them. Previews and playback style the text and its readings; the
markers do not count as lyric characters or karaoke boundaries.
Underline and strikethrough remain continuous across spaces and furigana
padding within a styled run, and restart on each visual row.
Save as `.lrcx` to preserve formatting.

In **Share > Image card > Text**, **Preserve LRCX formatting** keeps inline
emphasis, blockquote indentation/bars, visual line breaks, and the smaller
translation rows. Card-wide emphasis combines with source emphasis; card fonts,
colors, stroke, and shadow remain available. Turn the option off for plain lyric
text. Source formatting applies to the checked excerpt in document order.
Line/segment timestamps and karaoke highlighting do not appear in image cards.
**Furigana** independently shows saved readings and already-generated
editor readings beside their base words. Authored ruby takes priority over
automatic readings. Translation rows receive no automatic readings. Adjust the
reading-size percentage in the Furigana section; annotated words wrap together.
Image cards consume available readings without starting reading providers.

Under **Text > Furigana**, choose **Auto**, **Above**, or **Below** for horizontal
lyrics, or **Auto**, **Right**, or **Left** for vertical lyrics. Auto uses above
or right respectively. **Extra gap** adds space to the default tight placement.
Readings inherit lyric color by default; enable **Custom color** to choose their
own. **Inherit stroke** and **Inherit shadow** independently apply the lyric
effects to readings. These appearance settings preserve fitting and placement;
reading size, position, and spacing participate in layout. The settings are
stored with style presets.

**Layout > Template** includes Classic, Spotlight, Record sleeve, Polaroid,
Editorial, and Cinema. Polaroid frames artwork above a lyric caption; Editorial
uses a larger title and offset lyrics; Cinema places lyrics lower on a shaded
artwork backdrop. Polaroid and Editorial initially select the Paper theme,
which can be changed. Saved presets retain their chosen theme. Cinema uses
available album artwork as a blurred backdrop with Gradient fill; Solid and
Image fill keep the selected background. Artwork is optional in every template.

**Text > Typography > Writing direction** switches lyrics between horizontal
text and top-to-bottom columns flowing right to left or left to right. CJK
characters stay upright, supported fonts supply vertical punctuation forms,
and Latin words run sideways. Newlines start new columns. Furigana follows its
base text on the selected side (right by default); source emphasis, translation sizing, and quote markers
are preserved. Alignment positions the column block, line spacing controls the
space between columns, and vertical position moves the block up or down.
The selected font must contain the desired characters. Song details remain
horizontal, and the writing direction is saved with style presets.

Under **Text > Song details**, edit the title, artist/credit, and album (prefilled
from `[ti:]`, `[ar:]`, and `[al:]` metadata). **Details placement** puts artist and
album below the title or groups all details beside the enabled album cover.
Unavailable artwork falls back to the below-title layout. Stroke and shadow
also apply to song details, scaled to their smaller text size. Presets remember
the placement and effects, while the title, artist, and album stay with the song.
Changing stroke or shadow settings does not change automatic font sizing,
line wrapping, or text placement. Effects paint within the text area's bounds.

## Blockquotes

Start the lyric after its timestamp with `> ` to indent it and add a subtle
vertical bar. One quote level applies to the entire timestamp, including visual
newlines, readings, inline styles, and any karaoke chunks:

```text
[00:12.500]> **Quoted lyric**{\n}Second row
[00:15.000]> {君|きみ}<00:16.000>の声
```

Use the **Quote** button above the Lyrics field to toggle the prefix for the
selected timestamp. It preserves text selection and existing readings/timing.
Use `\> ` for a literal leading greater-than sign. Nested quote levels are
not interpreted. Save as `.lrcx` to preserve the quote style.

## Visual newlines

Use the literal marker `{\n}` to split one timed lyric across display rows:

```text
[00:12.500]First row{\n}Second row
```

Both rows belong to the same lyric timestamp. The marker also works between
karaoke chunks without adding a timing boundary:

```text
[00:12.500]First row{\n}<00:14.000>Second row
```

In the Lyrics tab, **Insert Newline {\n}** above the lyric field inserts the
marker at the cursor. You can also type it or press Enter. The editor displays
saved line breaks as markers; previews and playback display actual line breaks.
Save as `.lrcx` to preserve them.

### Translation rows

Use `{\nt}` (newline translation) to introduce a translation row:

```text
[00:12.500]Original lyric{\nt}Translated lyric
```

It shares the lyric timestamp and displays at 75% of the main lyric font size.
Translation rows use compact spacing without an empty automatic-reading band.
Automatic reading generation skips translation rows, and All Romaji preserves
their original text. The translation marker is preserved when editing and saving `.lrcx`.
It can be mixed with ordinary `{\n}` breaks and karaoke timestamps. Enter the
marker with **Insert Translation {\nt}** beside Insert Newline, or type it
directly in the Lyrics field or source view; it does not generate or
download a translation.

In **Search Lyrics**, enable **Include translations** to import aligned
translations supplied by NetEase Cloud Music, QQ Music, or KuGou. The option
starts unchecked and becomes available when a result has matching translated
rows. Use the adjacent **Original | Translated** tabs to inspect either text;
in Original, checking Include translations previews the combined lyrics using
`{\nt}`. In Translated, only the translation is shown and the checkbox is disabled.
Its selection is retained when switching tabs and controls whether
**Use Selected Lyrics** saves both as `.lrcx`.
The **Chinese** selector offers **As provided**, **Simplified**, and **Traditional**.
Conversion applies to translation text in the preview and saved lyrics, leaving
original lyrics and timing unchanged. As provided restores the provider's exact text.
The translation language is supplied by the provider.
Unmatched rows remain untranslated; this does not perform machine translation.

## Segment end times

A segment ends when the next segment begins. The final segment's end can later be resolved from the next lyric line by the karaoke renderer.

An angle-bracket timestamp with no following text is an explicit end boundary.
Two timestamps between chunks can preserve a pause:

```text
[00:01.000]First<00:02.000><00:03.000>second<00:04.000>
```

Here, `First` ends at 2 seconds, `second` starts at 3 seconds, and the line ends
at 4 seconds. Two terminal boundaries preserve a trailing pause independently
of the final segment's end:

```text
[00:01.000]Word<00:02.000><00:03.000>
```

The word ends at 2 seconds; the line remains active until 3 seconds.

## Compatibility

- `.lrc` continues to work unchanged.
- `.lrcx` is preferred automatically when both `song.lrcx` and `song.lrc` exist.
- Existing lyric widgets still receive `(timestamp, text)` pairs, so LRCX does not break normal or scrolling display.
- Rich LRCX timing remains available through `self.lyric_lines` for future plugins.
