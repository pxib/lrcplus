lrcplus

media player

idk

Lyrics search is available from **Lyrics → Search Lyrics**. Choose LRCLIB,
NetEase Cloud Music, QQ Music, KuGou, Kuwo, or Musixmatch. All sources except
Musixmatch require no API key or authentication token.
Each provider keeps its query, results, selected row, and loaded previews while
the search dialog is open. Switching providers restores them without searching
again; closing the dialog clears these cached results.

The Chinese providers search by artist and song title, including Japanese titles.
Select a result to fetch and preview its lyrics. The **Timing** column distinguishes
**Words**, **Lines**, and **Plain**; **Unknown** means the result has not loaded yet.
Recording duration is displayed when supplied by the provider.

NetEase prefers YRC word timing and falls back to LRC when YRC is unavailable.
KuGou downloads KRC word timing. Both preserve millisecond word starts, word ends,
and pauses when saved as `.lrcx`. QQ Music and Kuwo provide line-timed `.lrc`;
untimed lyrics save as `.txt`. Kuwo can mix original and translated lines, so the
app keeps all returned lyric rows and notes this below the preview.

Search and preview requests run in the background. Header tags, title/artist
intro lines, and writing credits are removed from fetched lyrics. Source
information is displayed below the preview rather than added to Chinese-provider
lyric files. KRC decoding has compressed and decompressed size limits and needs
no extra dependencies.

Musixmatch defaults to an authentication
token, using the same app endpoint as OpenLyrics for foobar2000. Click **Get token**
to request one, paste an existing token, or set the `MUSIXMATCH_TOKEN` environment
variable before launching the app. Use **Show** to reveal the masked value.
If a token expires, click **Get token** again. Tokens are remembered in memory
until the app exits.

Musixmatch may refuse automatic token creation with HTTP 403. An all-zero value
is rejected as a placeholder, and failed token requests preserve the existing
token. If automatic retrieval fails, paste a usable existing token or use LRCLIB.

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
