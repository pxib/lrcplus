# Lyrics provider investigation

Checked on 2026-10-31. These are results from direct requests to the providers,
without an account or developer API key. NetEase YRC with LRC fallback, KuGou KRC,
and Kuwo LRC are now implemented, along with the dialog's per-provider cache.

## Live results

| Source | Timing | Verified response | App output |
| --- | --- | --- | --- |
| NetEase YRC | Word or syllable starts and durations | Public lyric v1 endpoint returned YRC for song `1824020871`: 45 timed rows and 392 word markers. YOASOBI's “夜に駆ける”, song `1409311773`, returned LRC with no YRC. | LRCX when YRC exists; LRC fallback otherwise |
| KuGou KRC | Word or syllable offsets and durations within each line | Searching “YOASOBI 夜に駆ける” returned 10 lyric candidates. Downloading the first candidate produced a valid `krc1` file that decoded to 69 timed rows and 733 word markers, before credit filtering. | LRCX |
| Kuwo LRC | Line starts | Search returned five matches. Song `83759543` returned 138 timed lyric rows. | LRC |

These checks establish availability for the sampled tracks and this connection,
not availability for every song or region. No lyrics, access keys, or downloaded
native files are included in this report.

## NetEase YRC

The current song search can supply the same numeric song IDs. The public endpoint
`https://music.163.com/api/song/lyric/v1` accepted `id`, `lv=-1`, `tv=-1`,
`rv=-1`, and `yv=-1`, with the existing app User-Agent and a NetEase Referer.
The response contains `yrc.lyric` when word timing is available. The successful
public request did not need encrypted EAPI requests or a new crypto dependency.
[Example endpoint implementation](https://github.com/hehelp/klyrics/blob/main/scripts/netease-yrc.js).

YRC stores line start and duration in milliseconds as `[start,duration]`. Its word
markers `(start,duration,flag)` contain absolute millisecond start times. JSON
rows can carry title and production credits; those should be skipped, with the
existing credit filter also applied to the decoded text of timed rows.
[Parser reference](https://github.com/chenmozhijin/LDDC/blob/main/LDDC/core/parser/yrc.py).

The implementation prefers YRC and retains the current LRC fallback when
the response has no usable YRC. The test track lacking YRC returned no YRC
through either the public v1 or EAPI endpoint, so changing endpoints did not add
word timing for that recording.

## KuGou KRC

An anonymous candidate search worked at `https://lyrics.kugou.com/search` with
`ver=1`, `man=yes`, `client=pc`, and `keyword`. Candidates provide the lyric ID,
song and singer names, duration, and a per-candidate `accesskey`. That key is
returned by the search service and does not need a user credential field.
Download uses `https://lyrics.kugou.com/download` with `ver=1`, `client=pc`,
`id`, `accesskey`, `fmt=krc`, and `charset=utf8`.
[Search/download reference](https://github.com/ESLyric/scripts/blob/main/searcher/kugou.js).

The downloaded JSON `content` is base64 encoded. A KRC file starts with `krc1`;
its remaining bytes are XOR transformed and zlib compressed. Both the decoding
and decompression use Python's standard library. The decoder validates the
header and limits compressed files to 2 MiB and decompressed lyrics to 4 MiB.
[Decoder reference](https://github.com/chenmozhijin/LDDC/blob/main/LDDC/core/decryptor/__init__.py).

KRC lines use `[start,duration]` in milliseconds. Word markers
`<offset,duration,flag>` are relative to the line start, so conversion must add
the line start. This differs from YRC's absolute word times. KRC can also contain
encoded translation/romanization metadata; it should stay outside the default
original-lyrics output.
[Parser reference](https://github.com/chenmozhijin/LDDC/blob/main/LDDC/core/parser/krc.py).

Candidate duration and recording metadata should remain visible so a user can
choose the right performance. A matching title does not establish that its
timing matches a local cover, edit, or live recording.

## Kuwo LRC

The tested search endpoint was
`https://www.kuwo.cn/search/searchMusicBykeyWord`, using the query as `all`,
`ft=music`, `rformat=json`, `encoding=utf8`, `pn=0`, and `rn=5`, plus the
client parameters used by Listen1. Results are in `abslist`; `DC_TARGETID`
provides the numeric song ID. The search also returned `MUSICRID`.

`https://m.kuwo.cn/newh5/singles/songinfoandlrc?musicId=...` returned
`data.lrclist`, containing `time` and `lineLyric`. Decimal seconds can be
converted to millisecond LRC timestamps without changing the lyric text.
[Search and lyric reference](https://github.com/listen1/listen1_chrome_extension/blob/master/js/provider/kuwo.js).

Kuwo may mix original and translated rows without a reliable language marker.
Listen1 explicitly notes that splitting them by duplicate timestamps can misalign
lyrics. Translation separation needs investigation before offering an
“original only” guarantee. One search timed out during the checks; the retry
succeeded.

## Fit with LRCPlus

LRCX already represents line starts, segment starts, explicit segment ends, and
pauses. YRC/KRC conversion can preserve millisecond word starts, word ends, and
line ends using these existing boundaries; it should not interpolate timing or
flatten the result to ordinary LRC. See [LRCX_SPEC.md](../LRCX_SPEC.md).

The download dialog now carries an explicit format field and saves word-timed
results as `.lrcx`. Its Timing column distinguishes words, lines, and plain text,
and its Duration column helps identify different recordings. Metadata cleanup
inspects the decoded lyric text while retaining surviving word boundaries.

Implemented integrations:

1. The NetEase fetcher prefers YRC, with LRC fallback.
2. KuGou candidate search supplies the key for bounded KRC decoding and LRCX conversion.
3. Kuwo preserves mixed original/translated LRC rows and explains this below the preview.

The implemented dialog cache retains provider-specific queries, result lists,
selection, loaded lyric previews, and scroll positions until the dialog exits.
Switching away cancels pending requests; returning to completed results reuses
the cached data. Changing Musixmatch credentials clears its active results while
preserving other providers' results.
