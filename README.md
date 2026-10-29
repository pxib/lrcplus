lrcplus

media player

idk

## Queue and saved playlists

- Open **Queue / Playlists** to manage the active queue. Drag tracks to reorder,
  or select one or more tracks and use **Move up**, **Move down**, or **Remove**.
- Right-click tracks or album headings in the library for **Play next** or
  **Add to queue**. Use Ctrl/Shift to select multiple entries. **Open > Add to
  Queue** (`Ctrl+Shift+O`) adds audio files without replacing the queue.
- **Play next** takes priority over shuffle. Inside the queue it moves the
  selected entries; from the library it inserts new entries. Repeated tracks
  are supported. Adding to the queue selects Queue playback mode.
- Use **Save / manage > Save queue as…** to create a named playlist. **Load**
  replaces the queue after confirmation; **Append** adds its tracks to the end.
  Editing the queue leaves saved playlists unchanged until you choose **Update
  selected playlist**. Rename and delete are also under **Save / manage**.
- **Import M3U** appends or replaces the queue with local audio entries from an
  `.m3u` or `.m3u8` file. Relative paths resolve beside the playlist file.
  Missing files, unsupported files, and remote URLs are skipped and reported.
  **Export M3U** writes UTF-8 with relative paths where possible, preserving order
  and repeats. Save the imported queue as a named playlist to keep it in the app.
- Named playlists are stored with your application settings independently of
  the startup option to restore the active queue. Deleting a playlist keeps its
  audio files.

## URL audio imports (optional plugin)

The **yt-dlp Audio Import** plugin adds **Plugins > Import audio from URL…** for
single-track previews and M4A/MP3 downloads into the library and queue. It includes
progress, cancellation, retry, and duplicate-source detection. Select uploaded or
automatic subtitles by language, keep VTT/SRT files, and optionally convert them
to synced lyrics. Existing downloads can fetch subtitles without downloading the
audio again. Install its Python
dependencies in **Settings > Dependencies**, then restart. FFmpeg/ffprobe and a
supported JavaScript runtime are configured separately in its plugin settings.
See [plugin setup and usage](plugins/community/ytdlp_import/README.md).
