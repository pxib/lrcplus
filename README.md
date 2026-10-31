lrcplus

media player

idk

In the library, choose **Lyrics** beside the search box to find songs by words
or phrases in local `.lrc`, `.lrcx`, and `.txt` lyrics. Matching ignores case,
timestamps, and lyric formatting. It uses configured lyric folders and explicit
attachments, and searches in the background. TXT files must be beside the audio
with the same filename stem, explicitly attached, or currently loaded for that
song. Choose **Track details** to search
titles, artists, albums, and filenames again. **Rescan** refreshes an active
lyric search after files are added or edited.
The **Matching lyrics** column shows the first matching line with the matched
words in bold. It appears beside the title when searching lyrics.

Lyrics search is available from **Lyrics → Search Lyrics**. Choose LRCLIB,
NetEase Cloud Music, QQ Music, KuGou, Kuwo, or Musixmatch. All sources except
Musixmatch require no API key or authentication token.
Each provider keeps its query, results, selected row, and loaded previews while
the search dialog is open. Switching providers restores them without searching
again; closing the dialog clears these cached results.

The Chinese providers search by artist and song title, including Japanese titles.
Timing is checked automatically in the background for all results. The **Timing**
column progresses from **Checking** to **Words**, **Lines**, or **Plain**. Selecting
a result prioritizes its lookup and shows its cached lyrics when ready. Failed
lookups show **Unavailable** or **Error**; hover over the cell for the reason.
Recording duration is displayed when supplied by the provider.
If a search fails, finds no results, or cannot load lyrics, the dialog suggests
removing the artist name and searching with just the song title.

NetEase prefers YRC word timing and falls back to LRC when YRC is unavailable.
KuGou downloads KRC word timing. Both preserve millisecond word starts, word ends,
and pauses when saved as `.lrcx`. QQ Music and Kuwo provide line-timed `.lrc`;
untimed lyrics save as `.txt`. Kuwo can mix original and translated lines, so the
app keeps all returned lyric rows and notes this below the preview.
If Kuwo's mobile lyric endpoint returns service code `301`, the app tries its
alternate web lyric endpoint for the same recording.

Search and preview requests run in the background. Header tags, title/artist
intro lines, and writing credits are removed from fetched lyrics. Source
information is displayed below the preview rather than added to Chinese-provider
lyric files. KRC decoding has compressed and decompressed size limits and needs
no extra dependencies.

Musixmatch defaults to an authentication
token, using the same app endpoint as OpenLyrics for foobar2000. Paste an existing
token or set the `MUSIXMATCH_TOKEN` environment variable before launching the app.
Use **Show** to reveal the masked value. If a token expires, paste a replacement.
Tokens are remembered in memory until the app exits.

All-zero token values are rejected as placeholders.

The **Developer API key** authentication option also accepts your own
[developer API key](https://developer.musixmatch.com/) or the `MUSIXMATCH_API_KEY`
environment variable. Developer keys and app tokens use separate endpoints
and cannot be substituted for one another.

Musixmatch results load their lyrics when selected. Synced lyrics are preferred;
plain lyrics are used when subtitles are unavailable or your credentials cannot
access them. Availability, previews, and quotas depend on your Musixmatch access.
The preview preserves the supplied text and copyright information. **Use Selected
Lyrics** saves an `.lrc` (synced) or `.txt` (plain) file next to the current audio
file, including Musixmatch attribution, and loads it into the player.
