# Lyrics provider investigation

Checked on 2026-10-31. These are results from direct requests to the providers,
without an account or developer API key. NetEase YRC with LRC fallback, KuGou KRC,
and Kuwo LRC are now implemented, along with the dialog's per-provider cache.

## Translation import (2026-11-04)

Search Lyrics now offers an optional **Include translations** checkbox. Original
lyrics remain the default. Selecting the checkbox saves the combined lyrics as
LRCX, with each translation introduced by `{\nt}`. Adjacent **Original | Translated**
tabs independently choose which text is previewed. The same cached provider
response serves both previews, so switching tabs does not make more requests.
In Original, Include translations also shows the combined `{\nt}` output.
In Translated, the checkbox is greyed out while retaining its selection.

A **Chinese** selector converts translation text to Simplified or Traditional
Chinese using local [OpenCC dictionaries](https://github.com/yichen0831/opencc-python).
It defaults to **As provided** and affects both the translated preview and bilingual
import. Conversion always starts from the cached provider translation, so returning
to As provided restores the exact source text. Originals and timing markup are
never sent through conversion. The runtime and test requirements include
`opencc-python-reimplemented`, and the executable specification bundles its
configuration and dictionary files.

- NetEase retains timed `ytlrc` and `tlyric` branches and chooses the branch with
  the most exact timestamp matches. See this
  [NetEase provider implementation](https://github.com/1-Dot/ivlyrics-netease-cloud-music).
- QQ Music decodes the separate base64 `trans` field, including HTML entities.
  See [Listen1's QQ lyric implementation](https://github.com/listen1/listen1_chrome_extension/blob/master/js/provider/qq.js).
- KuGou decodes translation entries (`type: 1`) in KRC's base64 JSON `language`
  tag. Rows are paired before credit removal to retain their original indices;
  romanization entries are excluded. See the
  [KRC parser reference](https://github.com/chenmozhijin/LDDC/blob/main/LDDC/core/parser/krc.py).

Only exact timestamp matches are attached. Ambiguous duplicate timestamps and
inconsistent KRC row counts are skipped. Malformed optional translations do not
prevent importing usable original lyrics. Original word starts, word ends, and
line ends are retained; translated word-timed rows use the original line start
and an instant display marker, without interpolated karaoke timing.

Live read-only checks returned 56 aligned translated rows for NetEase song
`1409311773` (LRC), 30 for `1824020871` (YRC), and 65 for the first KuGou
candidate for YOASOBI's 夜に駆ける (KRC). Sampled QQ results returned usable
original lyrics without aligned translations. Availability and translation
language vary by recording. No downloaded lyrics are included in this report.

Kuwo still preserves its mixed rows: duplicate timestamps alone cannot establish
which row is a translation. LRCLIB and Musixmatch do not expose translation
imports through this implementation. This feature does not translate text itself.

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
Keep the artist/title separator as a compact hyphen (`Artist-Title`). Live checks
returned no candidates for `Rick Astley Never Gonna Give You Up` or
`Bo Burnham Welcome to the Internet`, but 10 and 2 respectively with the hyphen.
The app normalizes its default `Artist - Title` query to this format for KuGou.
This lyric endpoint is a recording matcher rather than a general keyword search:
`あ`, `世`, and `笑` all returned empty candidate lists. When that happens, the
app searches `https://mobileservice.kugou.com/api/v3/search/song` instead, with
`plat=0`, `version=9108`, and a maximum of 30 results. This endpoint uses
`status=1` for success and `data.info` for song records. Each record supplies
title, singer, album, duration in seconds, and a recording hash. Previewing a
record resolves its lyric candidates with that hash and duration in milliseconds.
The song search returned 30 records for each of the three single-character
queries during live verification.
Download uses `https://lyrics.kugou.com/download` with `ver=1`, `client=pc`,
`id`, `accesskey`, `fmt=krc`, and `charset=utf8`.
[Search/download reference](https://github.com/ESLyric/scripts/blob/main/searcher/kugou.js).

The downloaded JSON `content` is base64 encoded. A KRC file starts with `krc1`;
its remaining bytes are XOR transformed and zlib compressed. Both the decoding
and decompression use Python's standard library. The decoder validates the
header and limits compressed files to 2 MiB and decompressed lyrics to 4 MiB.
[Decoder reference](https://github.com/chenmozhijin/LDDC/blob/main/LDDC/core/decryptor/__init__.py).

Some live candidates for KOKIA's `ありがとう…` contain backwards word offsets.
If decoded KRC cannot produce usable word lyrics, the app downloads `fmt=lrc`
for the same lyric ID and access key, decodes the base64 UTF-8 text, and keeps
line timing instead. This produced a usable preview for the first `あ` result.

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

The mobile endpoint can also return API status `301` inside an HTTP 200 response.
The implementation then tries
`https://www.kuwo.cn/openapi/v1/www/lyric/getlyric` for the same `musicId`.
That endpoint uses a `code: 200` success envelope instead of `status: 200`.
Both endpoints returned 132 lyric rows for “少女A”, song `411629241`, in a later
live check; the reported 301 could not be reproduced consistently from this
connection. If both endpoints fail, the result displays an error rather than
claiming that usable lyrics were found.

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

Timing checks now run automatically for the active provider's result list. A
two-worker queue spaces request starts by at least 250 milliseconds, prioritizes
the selected row, and pauses pending work on server retry delays. Completed
lyrics and errors are cached with their records. Rows show Checking, Words,
Lines, Plain, Unavailable, or Error as appropriate; errors on unselected rows do
not replace the selected preview. Switching sources or closing the dialog
cancels queued work and discards late responses.
