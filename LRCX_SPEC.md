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
