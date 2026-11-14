# yt-dlp Audio Import

An optional LRCPlus plugin for importing online video/audio tracks, batches,
and playlists. Downloaded files become regular local library tracks. The downloader UI,
worker, settings, and download history all live in this plugin.

## Setup

1. In **Settings > Dependencies**, install/repair **yt-dlp** and **mutagen**.
   The yt-dlp requirement includes its `default` extras, including matching
   YouTube challenge scripts. Restart LRCPlus after installation or updates.
2. In **Settings > Dependencies**, select **FFmpeg + ffprobe** and **Install / Repair**.
   On supported Windows systems, this downloads a verified private installation
   for LRCPlus without administrator access or system PATH changes. Both tools
   are installed together. Existing installations can also be found on PATH or
   selected in **Settings > Plugins > yt-dlp Audio Import**.
3. For YouTube, install **Deno (JavaScript runtime)** from the same manager.
   New imports detect managed tool installations immediately; **Check** verifies
   their executables, and **Remove** only removes LRCPlus-managed copies.
   Auto-detection tries
   Deno, Node, then QuickJS. The plugin settings also accept an explicit runtime
   type and executable path. See the official
   [yt-dlp dependency guide](https://github.com/yt-dlp/yt-dlp#dependencies) and
   [YouTube runtime guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
4. Ensure **yt-dlp Audio Import** is enabled under **Settings > Plugins**.

The plugin uses the dependency manager's private Python runtime in packaged
Windows builds. It does not add yt-dlp to the application's mandatory libraries.
Source installations can alternatively install `"yt-dlp[default]>=2026.3.3"`
and `"mutagen>=1,<2"` in their Python environment.

## Usage

Open **Plugins > Search and import audio…**. Use **Check setup** to inspect the
downloader, FFmpeg, and runtime. Enter a title or artist and select **Search**
to find up to ten YouTube results with titles, channels, durations, and thumbnails
when available. Live and upcoming broadcasts are excluded. Double-click a result (or
use the arrow keys and Enter) to preview it. Results remain available to choose
another track; editing the input clears them. Search runs in the cancellable
worker and requires no additional dependency or API key.

The input remembers the 50 most recent searches and previewed URLs across
sessions. Use its history icon to recall an entry or clear history; typing
also offers matching recent entries.

You can also paste a URL and select **Preview** to see the
title, creator, duration, and artwork when supplied by the source. Choose a
download folder and M4A or MP3, then select **Download**.

### Download queue, batches, and playlists

Single-track downloads, **Paste batch** (one track URL per line, up to 1000),
and **Add selected to downloads** all use the same persistent queue. Use Ctrl
or Shift to select multiple search results. Batch settings are captured when
you add tracks; changing the format, destination, artwork, lyrics, or playback
option later affects future additions.

For a playlist URL, choose **Load playlist**, select the tracks you want, and
choose **Add selected to downloads**. Playlists load 100 entries at a time;
**Load more playlist tracks** fetches the next page. Private, unavailable,
live, and upcoming entries are disabled when the listing identifies them.
Other extraction failures are reported per track and do not stop the batch.
Playlist order is preserved when adding completed tracks to playback.

The **Downloads** tab defaults to two concurrent tracks, adjustable from one
to four. Each slot includes metadata, downloading, conversion, artwork, and
lyrics processing. Select rows to cancel, retry, or remove them. **Stop starting
new downloads** lets active tracks finish; **Start / Resume** starts waiting
tracks. Removing a queue row does not delete downloaded files. Completed rows
waiting for an earlier track to enter playback are retained until imported.

Closing the import window leaves downloads running. Exiting LRCPlus or
disabling the plugin stops workers and saves unfinished items. Reopen
**Downloads** and select **Start / Resume** after restarting; interrupted tracks
restart rather than resuming partial files. Enqueuing new tracks also starts
waiting items. Existing audio is reused when possible; **Download again**
explicitly creates another copy. Duplicate queued tracks are skipped.

A selected subtitle language is applied to each track when available; missing
subtitles are reported without failing the audio download. Artwork and lyric
provider requests are paced across workers. Finished tracks enter the library
and playback queue in selection order, so a later track may wait for an earlier
track to finish or fail before import.

The preview also shows the album and full release date when available, falling
back to a clearly labeled upload date. Dates follow the operating system's
regional short-date format. Source audio codec, bitrate, and sample rate appear
when supplied; they update when switching between M4A and MP3. These describe
the source stream before conversion. Missing metadata stays hidden.
An estimated source download size appears beside **Download**, using the
reported stream size or its bitrate and duration. It updates with the audio
format and stays hidden when unknown or reusing an existing file. Conversion,
artwork, and subtitles can change the final size.

The completed audio includes metadata and artwork when available. Files always
enter the library; **Also add to the playback queue** controls enqueueing.
Existing playback continues. Adding to the queue selects Queue playback mode.
Downloaded sources are remembered per media identity and audio format, including
their source URL. If the file is still present, **Add existing** imports it again
without another download. **Download again** creates a separate file.

## Album covers from MusicBrainz

Enable **Use MusicBrainz for album cover** before selecting **Preview** to look
up a release by track title and artist (and album, when the source supplies it).
Matching front covers come from the [Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API).
When a source lists several artists, the primary artist is used if the uploader
confirms that name; auto-generated YouTube credits can also list composers.
The preview shows the cover and release name; that same image is embedded in
new M4A/MP3 downloads. Official albums are preferred when no album is supplied.
Lookup failures, missing covers, and uncertain matches fall back to source
artwork. The option is off by default and is remembered when downloading; its
default can also be changed in the plugin settings. No additional dependency
or account is required.

Changing the option requires another **Preview**. **Add existing** and **Get
subtitles** preserve the existing audio file and its cover; choose **Download
again** to create a new file with different artwork. Matching depends on the
source metadata, so some video titles and channel names will not match.

## Subtitles and synced lyrics

Enable **Automatically find lyrics** to search [LRCLIB](https://lrclib.net/)
when importing audio. The plugin matches the title and artist, checks the
duration when available (within three seconds), and prefers synced `.lrc`
lyrics over plain `.txt` lyrics. Matching lyrics are saved beside the audio
and attached automatically, independently of **Use subtitles as synced lyrics**.
Selected subtitle lyrics take precedence. No match or a lookup failure still
imports the audio and displays the lookup result.

The option is off by default, is remembered when downloading, and is also
available in the plugin settings. For previously downloaded audio without
remembered lyrics, **Find lyrics** searches without downloading the audio
again. Existing remembered lyrics and `.lrcx`, `.lrc`, or `.txt` files beside
that audio are reused. Files are never overwritten. The lookup runs in the
cancellable worker and requires no additional dependency or account.

After **Preview**, the **Subtitles** selector lists available languages with
separate **Uploaded** and **Automatic** sources. Choose one to keep its original
VTT/SRT file beside the audio. The default is audio only; your last subtitle
language/source is remembered for future previews when it is available.

Enable **Use subtitles as synced lyrics (.lrc)** to create and attach line-timed
lyrics. This preserves Unicode, multiline cues, and silent gaps, strips caption
formatting, and removes overlapping rolling repetitions in automatic captions.
Word-level caption highlighting is converted to line timing. Original subtitles
are kept unchanged, and existing authored lyric files are preserved.
Repeated outline/color redraws in styled uploaded captions are merged into a
single visible lyric interval. Vertical CJK character columns are joined into
phrases; distinct multiline captions and later refrains remain separate.
An inline warning appears when the source lists ASS/SSA formats. Downloaded VTT
files with ASS-style color/effect markers also show a warning, qualified as a
possible conversion from ASS/SSA. The warning explains that lyric conversion
simplifies positioning and karaoke effects and recommends reviewing the result.
It does not interrupt downloads or playback.

If the audio was already downloaded, selecting a missing subtitle track changes
the action to **Get subtitles**. This downloads only captions and optionally
creates lyrics, keeping the audio file unchanged. If the selected subtitle file
and converted lyrics already exist, **Add existing** reuses them. Attaching
lyrics to the current song refreshes the display without interrupting playback.

Files include language and source in their names, such as
`Song.en.manual.vtt` and `Song.en.manual.lrc`; automatic captions use `.auto`.
Collisions receive a numeric suffix. Conversion failures keep the native
subtitles and audio and report why lyrics could not be created. Subtitle
availability and transcript accuracy depend on the source; converted captions
can be reviewed and edited in the lyrics editor.
The current player renders one widget per scrolling row, so conversions and
player imports have a 5,000-row limit. Oversized conversions retain the native
subtitles and report an error instead of loading thousands of redraws into the UI.

Each active track runs in a separate process. Cancelling a track, unloading
the plugin, or quitting the app stops its process tree and cleans its staging
directory. Closing the import window leaves queued downloads running.
Existing destination files are
kept: repeated filenames receive a numeric suffix. Failed downloads can be
retried; select a row in **Downloads** to inspect its details. A completed file
is retained if adding it to the library fails, so **Retry selected** can reuse
the file and retry the import.

This version handles finished tracks and playlist selections. Live
broadcasts, streaming playback, and browser-cookie authentication are outside
its current scope. Site availability and authentication requirements still
depend on yt-dlp and the source site.

## Host interface

Requires Plugin API v4. The plugin registers its menu and settings with the
public API and calls `api.import_audio_files(paths, add_to_queue=True)` and
`api.attach_lyrics(audio_path, lyrics_path)` from the GUI thread after a
successful download. It does not access the player's
queue or library internals.
