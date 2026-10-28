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
