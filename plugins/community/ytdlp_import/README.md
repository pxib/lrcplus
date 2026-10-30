# yt-dlp Audio Import

An optional LRCPlus plugin for importing one online video/audio track at a
time. Downloaded files become regular local library tracks. The downloader UI,
worker, settings, and download history all live in this plugin.

## Setup

1. In **Settings > Dependencies**, install/repair **yt-dlp** and **mutagen**.
   The yt-dlp requirement includes its `default` extras, including matching
   YouTube challenge scripts. Restart LRCPlus after installation or updates.
2. Install **FFmpeg and ffprobe**, and put them on PATH or enter their folder in
   **Settings > Plugins > yt-dlp Audio Import**.
3. For YouTube, install a supported JavaScript runtime. Auto-detection tries
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

Open **Plugins > Import audio from URL…**. Use **Check setup** to inspect the
downloader, FFmpeg, and runtime. Paste a URL and select **Preview** to see the
title, creator, duration, and artwork when supplied by the source. Choose a
download folder and M4A or MP3, then select **Download**.

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

Downloads and conversion run in a separate process. **Cancel**, closing the
window, unloading the plugin, and quitting the app stop its process
tree and clean the plugin's staging directory. Existing destination files are
kept: repeated filenames receive a numeric suffix. Failed downloads can be
retried; details are available under **Details**. A completed file is retained
if adding it to the library fails, so **Add existing** can retry that step.

This version handles single finished tracks. Playlist/channel imports, live
broadcasts, streaming playback, and browser-cookie authentication are outside
its current scope. Site availability and authentication requirements still
depend on yt-dlp and the source site.

## Host interface

Requires Plugin API v4. The plugin registers its menu and settings with the
public API and calls `api.import_audio_files(paths, add_to_queue=True)` and
`api.attach_lyrics(audio_path, lyrics_path)` from the GUI thread after a
successful download. It does not access the player's
queue or library internals.
