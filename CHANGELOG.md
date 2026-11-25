# Changelog
Versions use dates with optional revisions. / バージョンは日付を使用し、オプションで改訂履歴も付けます。
ALLLLLLLLLLLLLLLLL changes here ARE **FINAL.** Okay? If I made a mistake, it's STAYING, okay?
Okay?
Okay?
Okay?
Thamk yo

## [2026-11-25]

### Added

- Add **Audio signal path** under **Settings > Advanced**. See the live playback route, output device, codec, available sample rates, ReplayGain, and DSP, plus reasons why gapless playback or crossfade is unavailable.
- Pressing or holding scroll wheel now lets you navigate the library.
- Choose **Stable** or **Nightly** for yt-dlp in **Settings > Dependencies**. New installs default to Stable. Restart LRCPlus after changing builds.
- Offer **Try Nightly** when Stable encounters certain YouTube extractor errors. The prompt opens the Dependency Manager with Nightly selected.
- Add prototype YouTube session support to **yt-dlp Audio Import**. Use a Firefox or LibreWolf profile or a `cookies.txt` file for one download, including retries from the Downloads tab. Compatibility varies by profile and video. Use **Test session** to check access before downloading.

### Changed

- Reduce RAM use for lyrics backgrounds, especially in large windows. Render stronger Gaussian blur at half resolution and draw backgrounds with blur off directly into the cache.
- Open Library album covers at display resolution and load more detail as you zoom. **100%** loads the original resolution, and **Fit** returns to a smaller preview.
- Reduce startup RAM use for **Rhythm Games** by deferring NumPy loading until Mania renders a results screen.
- Reduce lyrics stutter during playback by capping **Audio Visualizer** artwork repaints at about 31 FPS while keeping audio analysis at its original rate.

## [2026-11-24]

### Added

- Add a prototype **Lyrics Type** mode to **Rhythm Games**. Type each displayed lyric phrase and submit it to score accuracy and timing.
- Add 60 FPS Mania result clips for clear, fail, all-perfect, and full-combo results when matching media is available.

## [2026-11-23]

### Added

- Add eight artwork styles to **Audio Visualizer** in Lyrics. Choose Aurora, Particle Sphere, Radial, Mirror, Spectrogram, Stereo Oscilloscope, Waveform Ribbon, or Waterfall.
- The yt-dlp plugin now supports Spotify playlists and individual songs, it relies on the SpotAPI dependency. If anything goes wrong, do not contact me /srs
- Expand **Rhythm Games** Mania with chart-generation improvements, configurable Neon and Classic rules, Project Sekai-style judgment windows, directional flicks, trace/hold behavior, and matching hit sounds and result animations.

### Changed

- Simplify the yt-dlp Audio Import window. Start with a compact search view and show preview and download controls as needed. Put folder, format, lyrics, and artwork settings under **Download options**, and move batch paste and setup checks under **More**.
- Improve rendering performance for Aurora, Stereo Oscilloscope, Waveform Ribbon, and Waterfall, especially in large windows.
- Flatten the artwork and seekbar visualizers when playback pauses or stops. The flat line starts fading after five seconds, and the live display returns when playback resumes.

### Fixed

- Use なに in furigana and romaji for common phrases such as 何を, 何が, and 何も. Keep なん in counter phrases such as 何か月.
- Correct the ruby for 磊々落々 and recover missing readings for kanji iteration marks (々) when the analyzer can confirm them.
- Make single Up and Down Arrow taps adjust volume immediately while keeping the Konami code available.

## [2026-11-22]

### Added

- Add lrc.red, Unison, and lyrics.ovh to **LRC > Search Lyrics**. Preserve available timing from lrc.red and Unison, show linked source credits, and save lyrics.ovh results as plain text. lrc.red requires a contact email.
- Add AMLL TTML DB, Better Lyrics, LiriQo, and VocaDB to **Search Lyrics**. Preserve available karaoke timing and offer separate VocaDB results for original lyrics, romanization, and translations.
- Add Lyrica and Fetch-Lyrics with configurable server URLs. Fetch-Lyrics defaults to a local server and preserves UtaTen furigana in untimed imports when available.
- Add LRCLIB Lyricsfile 1.0 support to **Search Lyrics**. Preserve line and word timing in `.lrcx` downloads and use LRCLIB's existing lyric fields when the Lyricsfile is missing or unreadable.
- Edit lyrics directly in the **Search Lyrics** preview before saving with **Use Selected Lyrics**. Keep drafts while browsing results, and apply edits from the **Translated** tab with **Apply Only Translation**.
- Add **Open > Network Playback…** for direct HTTP(S) audio streams and a searchable Radio Browser directory. **Station History** remembers the last 20 stations that played successfully. Save stations from the directory or history and tune in again from **Favorites**. The **Stations** view in Statistics shows radio listening when recording is enabled.
- Add optional automatic lyrics for live radio. Use the artist and title from station metadata to find a match on LRCLIB and display it as scrollable plain lyrics. **Download audio from metadata…** passes the current song to the yt-dlp Audio Import plugin for search and preview.
- Add a core **Equalizer** under **Plugins > Equalizer…** with Graphic and Parametric modes, 10 or 31 bands, preamp, a themed frequency-response graph, and built-in and named custom presets. Preview edits during playback and use **Bypass** to compare the sound. **Cancel** restores the saved EQ.
- Add FFmpeg-powered ReplayGain scans to the Library context menu. Scan selected tracks individually or measure all library tracks in each selected album. LRCPlus stores gain and peak results in its local database for Track or Album playback mode.
- Add the **Audio Visualizer** community plugin under **Plugins > Audio Visualizer**. Choose edge-to-edge glowing curves over the artwork in Lyrics or a full-width strip with peak markers below the seekbar. The spectrum adapts to quiet audio and responds quickly to short hits.

### Changed

- Give live streams a fixed 1× speed, a red seekbar with no handle, and a read-only elapsed-time label. Resume at the live edge by default or enable **Keep buffered audio on resume** to continue from the paused position. Choose **Go Live** to resume automatic lyrics and metadata search after buffered playback.

### Fixed

- Render plain lyrics at full contrast for easier reading.
- Clear the previous track’s artwork and MIDI selection in Karaoke Screen when switching to a network stream.

## [2026-11-21]

### Added

- Add TTML import and export in the lyrics editor's **Source** tab. **Import…** accepts TTML and ASS/SSA, and imports can be undone. **Export…** writes a TTML copy of the current draft with line and word timing, translations, and authored furigana.
- Add TTML to the player's **LRC > Save As…** menu. It saves a copy with the lyrics file's timing offset applied and keeps the current lyrics active.

### Changed

- Split Lyrics Search into **Title** and optional **Artist** fields. Prefill both from the loaded track's tags, support pasting `Artist - Title`, and restore both fields from search history.
- Make **Try another source** carry both fields over and search the selected source. Reuse cached results when both fields match.

## [2026-11-20]

### Added

- Add live connection and activity status to Discord Rich Presence settings. Show when Discord is unavailable, when activity was sent, and when playback settings hide it. **Check connection** tests Discord without publishing an activity, and playback retries after a connection failure.
- Add per audio device delay.

### Changed

- Show the loaded track's title, artist, and duration in Lyrics Search. Prefer results with the same title and artist and a duration within three seconds. Label results **Word-synced**, **Line-synced**, or **Plain text**, and offer **Search title only**, **Try another source**, or **Retry** when appropriate.
- Keep yt-dlp Audio Import on **Search / Import** after adding tracks. Confirm how many were added, offer **View downloads**, and show the active and waiting count on the Downloads tab. Tracks added to a paused queue wait for **Start / Resume**.

### Fixed

- Make double-clicking a Lyrics Search result focus the preview. Show the destination filename before applying lyrics, and distinguish overwriting an existing file from saving a new one.

## [2026-11-18]

### Added

- Allow opening the lyrics editor without a loaded song or lyrics file. Show a compact, themed **No songs found** welcome screen with a small lyric-sheet icon and **Create New LRCX** and **Open LRCX** buttons inside the editor. Create the chosen lyric file on Save.
- Add a **Live preview** beneath the lyrics editor's compact text box, using the player's renderer for readings, formatting, translation rows, and playback highlighting. Keep the preview on the selected line, update it after edits and Undo/Redo, and offer **Expand text** for longer passages. Reuse the editor's reading-generation results instead of starting duplicate requests.
- Replace separate shortcut popups with a searchable **Shortcuts** reference, available with **F1** from every editor tab. Document waveform navigation, Start/End editing, segment previews, snapping, and linked boundaries alongside keyboard shortcuts.
- Add visible pan, zoom, **Fit line**, and **All audio** controls to both editor waveforms. Support **Ctrl+left click** to seek without changing timing or starting playback, and waveform-focused **+ / −**, **Left / Right**, **Home**, and **Ctrl+Home** navigation.
- Add line and karaoke-segment preview controls with optional looping, playback speed, and adjustable lead-in/out. Keep preview ranges within the audio duration and stop looping when pausing, seeking, or closing the editor.
- Show live timing-warning badges beside affected lyrics and a clickable issue list. Update warnings after edits and Undo/Redo, including when the sidebar is sorted.
- Add collapsible **Find & Replace** options that appear only after entering a search. Support match highlighting, case sensitivity, Unicode matching, and selected-line or whole-document scope. Preview replacements before applying them, preserve timing and unaffected readings and formatting, and undo each replacement batch in one step.
- Remember the editor's window size and sidebar and Karaoke Preview widths across sessions, retaining panel widths when tabs temporarily hide them. Fit restored window dimensions to the available screen.
- Upgrade the **Source** tab with line numbers, syntax highlighting, current-line highlighting, line/column tracking, error underlines, and a clickable issue list. Share validation between live feedback and applying source edits, with colors that adapt to light and dark themes.
- Add **Ctrl+mouse wheel** zoom to Source, scaling text, syntax fonts, and line numbers together from 6 to 40 points without changing lyric content or Undo history.

### Changed

- Compact the Lyrics formatting toolbar, make **Readings** collapsible, group timing and movement actions into menus, and combine line-editing actions into one row.
- Build the editor's Source document when opened and release its clean text/layout after leaving. Omit regenerable source text from Undo checkpoints, stop hidden Lyrics/Karaoke preview updates, reuse editor-generated readings in Karaoke, and release hidden waveform images while preserving decoded peaks and drafts.
- Reduce library, lyrics, and Undo memory with compact model storage. Bound reading and lyric-search cache payloads, expire bounded Discord lookup caches, and stream settings saves to avoid keeping a second serialized library in memory.
- Release cached readings, library covers, track metadata, and lyric-search data after ten minutes without use, checking once a minute on the GUI thread. Preserve readings for the loaded song and visible editor, and covers currently visible in the library.
- Speed up opening Settings, especially on less powerful hardware, by applying the dialog theme once and loading font and audio-device choices only when their controls are used. Share font enumeration between Global and Local scopes and preserve saved and imported selections.
- Reduce the lyrics editor's default size from 1180 × 820 to 1100 × 740 while retaining window resizing. Use consistent **Waveform** toggles, playback labels, tooltips, and inline gesture hints in Lyrics and Karaoke.

### Fixed

- Recover unsaved LRCX drafts from the editor welcome screen, including unfinished Source edits. Save recovery copies separately from the chosen lyric file.
- Route editor playback-speed changes through the main player's audio controller so speed, pitch compensation, processed audio, and queued buffers stay synchronized.
- Keep Japanese verb endings and contractions together in Romaji, including `泣いてん → naiten`, `しまったんだ → shimattanda`, `見たって → mitatte`, and `欲しいんでしょ → hoshiindesho`. Handle different parser token splits and grammar formats while preserving authored pauses, source lyrics, and karaoke timing.
- Suppress duplicate Romaji rows for English and other Latin-script lyrics, including English rows within mixed-language lyrics. Preserve source text, translation rows, timing, and the saved Romaji preference.
- Stop waveform segment previews with a precise deadline instead of waiting for coarse playback-position notifications. Respect playback speed and pause/resume, restore the exact segment endpoint after pausing, and cancel the preview limit when seeking or starting continuous playback.
- Exclude lyrics-editor playback from listening statistics on native and processed audio paths. Preserve listening recorded before opening the editor and resume tracking afterward, including when the editor fails to open.
- Keep the Readings panel visible when automatic readings arrive on first open. Measure newly created reading controls before updating the panel height so switching lines is no longer required to restore it.
- Avoid large Windows memory spikes when a saved lyric font is unavailable. Resolve missing fonts to the bundled face before measuring them, preserving the saved preference and installed font selections.
- Fixed a timing bug that could silence very short karaoke waveform previews. The editor now waits for the player to reach the segment end before pausing or looping. The saved karaoke timestamps stay the same.

## [2026-11-17]

### Added

- Add **Get more plugins** to **Settings > Plugins**, opening the wiki's Plugin directory. Link the community plugin repository and document **Code > Download ZIP**, extraction, individual plugin folder placement in `plugins/community`, dependency setup, and restarting LRCPlus in all four wiki editions.
- Add dependency checkboxes with **Select all** and **Clear selection** for installing, checking, or removing several packages and tools in one batch. Skip items that do not support the selected action, continue after individual failures, and report the results.
- Add **Cancel download** to stop the active dependency download or installer and skip remaining batch items. Keep completed installations and preserve the previous active installation when cancelling an unfinished replacement.
- Add a saved **Don't show again** option to startup dependency notices, with a control in the Dependency Manager to re-enable notices.
- Add **Show verbose logs** with live pip output, commands, exit codes, timings, download checksums, version and import checks, activation details, and error traces. Retain the latest 5,000 lines and open the logs automatically after an error.
- Add optional offline **English → Katakana (alkana)** readings to the lyrics editor's **Generate as** selector, with installation through **Settings > Dependencies**. Keep Auto unchanged, preserve English source text, and support contractions and custom **English katakana** dictionary entries. Warn after generation when words remain unconverted, listing up to 20 distinct words and excluding translation rows and existing manual readings.
- Expand all four manual editions with repeat/practice, sleep timer, listening statistics, and settings-transfer guides. Document library imports and removal, Favorites, weighted shuffle, independent lyric layers, personal dictionaries, and visual editor operations; refresh factory defaults and navigation.
- Update onboarding with sortable Lyrics, Romaji, Translation, and Furigana layers, independent toggles, and a live scrollable preview. Explain Romaji sizing and Floating Lyrics, keep one layer enabled, and preserve Apply/Skip behavior and unrelated saved preferences.
- Add sortable lyric layers in **Lyrics > Readings**: independently toggle Lyrics, Romaji, Translation, and Furigana, and drag or move them to change their rendered order. Apply the layout to compact and scrolling lyrics, with saved global and per-file preferences.
- Apply lyric layer order and visibility to Floating Lyrics immediately, including while paused. Keep Romaji smaller alongside Lyrics and full-size on its own, fit the window to enabled layers, and preserve horizontal panning for long lines.
- Show destination previews while dragging files: add to Library or append to the end of the queue. Select and scroll to dropped tracks after importing, open the Library workspace, and clear active library filters when needed to reveal the import.
- Add **Remove from library** to track and album context menus, preserving disk files, playback queues, favorites, and saved playlists. Keep removed tracks excluded after rescans and restarts; dropping a removed audio file restores it. Support dropping audio files and folders on the main player to import in the background, and dropping audio files on the queue to append while retaining internal drag reordering.
- Group General settings into **Startup**, **Listening history**, and **Updates**, and Playback settings into **Behavior**, **Shuffle**, **Transitions**, **Audio**, and **Display** tabs. Keep each section scrollable and let settings search open its matching subcategory.
- Add **Weighted tracks** under **Settings > Playback > Shuffle**, with separate preferences for favorites and songs not played recently. Favorites receive twice the weight; recently heard tracks recover from 20% to full weight over 14 days. Load local history in the background only while statistics recording is enabled, retain favorites-only behavior without history, and preserve queue priority and gapless handoffs. (thanks to [gmusicbrowser](https://github.com/squentin/gmusicbrowser) for the inspiration!)
- Add simple typo tolerance to library track-detail searches when ordinary search finds no results. Match one missing, extra, wrong, or swapped letter in title, artist, or album words of at least four characters, show **Close matches**, and respect Favorites. Keep short words, numbers, and lyric searches literal. (thanks to [namida](https://github.com/namidaco/namida) for the inspiration!)

### Changed

- Use Qt's native system style for the standalone Dependency Manager window, independently of the player's custom theme. Label packages by the features they enable, with technical package names underneath for troubleshooting.
- Load alkana's dictionary only in a temporary helper process during conversion, then release it when the helper exits. Batch **Generate All** requests into one helper run so the player retains only generated lyric readings.
- Reduce Windows release ZIP size by keeping GUI libraries out of the standalone updater and using maximum Deflate compression.
- Compress the offline manual's fonts as WOFF2 while preserving all glyphs and remove unused Qt PDF and virtual-keyboard plugins from Windows releases.
- Smooth live pitch adjustments with short audio-timed ramps, faster slider updates, and the latest target applied to queued audio. Halve processed output and startup buffer targets to reduce delay while preserving decoder-burst handling and final audio samples.

### Fixed

- Reduce Dependency Manager lag by batching live log notifications and text updates, deferring log rendering while the panel is hidden, disabling log line wrapping, and avoiding repeated package scans while rebuilding the list.
- Continue karaoke highlighting through wrapped horizontal rows in reading order instead of starting the same timed chunk's wipe on every row at once. Keep furigana and text decorations aligned with the continuous sweep.
- Keep kana next to authored furigana on the same karaoke sweep when automatic readings are cached, preventing characters such as の and が from highlighting before the preceding word and reading finish. Preserve generated readings that span multiple timestamps.
- Stabilize onboarding pause tests on busy CI runners by waiting for dialogue completion with a bounded timeout and triggering the timer callback directly when checking the typing continuation.
- Keep Furigana attached to its lyric words instead of rendering a separate kana line. Place it before or after Lyrics to show readings above or below, and preserve word alignment with Romaji, wrapping, scrolling, Floating Lyrics, and the onboarding preview.
- Keep authored music-note symbols in Lyrics while suppressing duplicate copies and empty note-only rows in Romaji and Furigana. Preserve notes when Romaji is shown on its own, including in Floating Lyrics.
- Keep at least one lyric layer enabled in Settings, and restore Lyrics when saved global or per-file preferences disable every layer.
- Attach displayed Romaji commas to the preceding word's karaoke span, including commas generated from phrase spaces, so they highlight with the word in the player and Floating Lyrics without changing source lyrics or timestamps.
- Reduce Korean lyrics-editor memory spikes by choosing modern covering fonts for lyric and source fields, the sidebar, karaoke controls and previews, and song-association labels. Prevent hidden labels from loading the large Windows Gulim fallback for Korean song or lyric paths, including filenames with emoji. Preserve text selection, editing, and Undo/Redo while retaining bundled Noto Sans JP.
- Remove library tracks in place, preserving column order and widths, sorting, collapsed albums, scroll position, and surviving selections. Keep pending lyric-search results consistent when a track is removed.

### Developer

- Add a standalone **Renderer Playground** using the player's lyric renderer and LRCX parser, with editable source, six presets, row selection, audio-free playback and time scrubbing, rendering controls, viewport bounds, paint timing, JSON case save/load, and PNG export. Run `Scripts/python.exe tools/renderer_playground.py` from the source checkout; see `tools/RENDERER_PLAYGROUND.md`. Explicitly exclude the playground from both PyInstaller builds.
- Add rendering regressions for continuous wrapped karaoke wipes and consistent authored-ruby timing with and without cached automatic readings, including horizontal and vertical layouts.

## [2026-11-16]

### Added

- Add **Use Qt Theme** under **Settings > Appearance > Theme**, alongside Light and Dark. Use Qt's default widget style and system palette, retain the choice across restarts, and preserve custom colors when switching back to Light or Dark.
- Add **Split reading...** for one selected word in the visual reading editor. Choose a base-text boundary and edit the two suggested readings, preserving Unicode graphemes, lyric text, and karaoke timing. Save both word boundaries in existing LRCX grouping metadata, retain custom reading allocations and internal spaces across save/reload, and support Undo/Redo for automatic and previously combined words.
- Add **Combine reading** to the visual reading editor: click a base word, Shift-click an adjacent piece, then choose the action from the right-click menu. Review the combined reading in one field, preserve karaoke timing and other lyric metadata, and save editor word boundaries in optional LRCX metadata. Support Undo/Redo, keep groups with unchanged text during corrections, and prevent selection across spaces or line breaks.
- Replace the Lyrics tab's reading table and separate reading preview with compact visual controls: readings sit above their words, and a subtle plus offers adding a reading. Click to edit, press Enter to apply or Escape to cancel, and keep lyric text editing separate. Scroll long rows horizontally, preserve Undo/Redo and saved readings, and defer background reading refreshes while typing. Support karaoke lines by changing only the selected annotation within existing timed chunks, preserving unrelated readings, timing, formatting, and translation rows. Keep visual controls synchronized with edits in the Karaoke table, and reset authored readings without rebuilding timed chunks.
- Add a **Repeat** menu beside Shuffle in the player dock: Off, All, Current Track, and **Play current track N times...**. Count the current play toward the chosen total, show progress in the dock, and advance normally afterward. Cancel counted repeat on Stop or a track change; keep it session-only and let sleep timers take priority. Support standalone files as well as library and queue playback. (thanks to [namida](https://github.com/namidaco/namida) for the inspiration!)
- Add an enabled **Japanese song readings** starter dictionary for fresh installations with whole-word readings `私 → わたし`, `君 → きみ`, and `時 → とき`, preserving existing saved dictionaries. Add **Manage > Restore factory dictionaries…** to the reading-dictionary manager; review the replacement before saving with **OK** in both the manager and Settings, or discard it with **Cancel**.
- Add karaoke highlighting to Floating Lyrics for timed LRCX songs, including synchronized furigana and existing sweep effects. Follow playback speed and lyric offsets, update when seeking while paused, and keep highlighting active when the main lyrics view is hidden.
- Add **Settings > Advanced** with JSON import and export for application preferences, saved themes, lyric search folders, and plugin settings. Include unsaved edits in exports, validate imported files, and let users review settings before applying with **OK** or discarding with **Cancel**. Preserve library data, playlists, history, per-song lyric overrides, and reading dictionaries. Imported plugin settings take effect after restarting LRCPlus.
- Add **Restore factory defaults…** under **Settings > Advanced**, with review before **OK** and cancellation through **Cancel**. Restore application preferences while keeping saved themes, folders, plugin settings, library data, playlists, history, per-song lyric overrides, and reading dictionaries.
- Add a **Debug** menu in Settings to record preference edits while choosing factory defaults. View before/after changes, stop or clear recording, copy final changed values as JSON, or copy the complete factory setup. Keep recordings in memory across Settings windows until cleared or the app closes. Include unsaved edits while excluding personal paths, output-device IDs, plugin data, and session state; copying does not save preferences or replace the factory baseline.

### Changed

- Add a horizontal scrollbar to the visual readings editor when needed. Keep words on one row until an explicit line break, reveal active reading fields while editing, and preserve the scroll position when refreshing readings for the same lyric.
- Show whitespace as noninteractive gaps in the visual readings editor, with explicit line breaks starting new rows. Preserve repeated and full-width spaces, blank rows, lyric text, and spaces inside readings. Keep authored phrase readings together and apply individual edits without rebuilding unrelated annotations or karaoke timing.
- Keep A-B markers per song until LRCPlus closes. Restore complete loops and A-only selections when returning through the library or queue, share markers between repeated entries of the same file, and clear only the current song's range. Stop retains markers; clearing the queue suspends the active loop while keeping its remembered range. Validate restored boundaries against the loaded song's duration and preserve sleep-timer priority.
- Use the recorded factory setup for fresh installations and **Restore factory defaults…**: dark **Nordic Frost** with accent **#88c0d0**, the chosen light/dark color overrides, no background blur, and playlist restoration enabled. Disable listening-statistics recording and automatic update checks by default. Retain Yomi readings, full reading rendering, and no lyric highlight animation; preserve explicit saved preferences.
- Generate complete Japanese furigana readings for numeric minute counters, including `5分` (ごふん) and `10分` (じゅっぷん), while keeping clock notation numeric.

### Fixed

- Keep the visual readings editor's scroll area transparent so it blends into the surrounding panel without a separate dark background.
- Correct **All romaji** word boundaries and particle pronunciation in `手はずっと`, producing `te wa zutto` while preserving ordinary `手はず` readings and karaoke timing.
- Reduce Statistics workspace memory use when displaying multilingual song, artist, and album names by reusing the library's font rendering.
- Fix severe lag when opening or refreshing the Statistics tab by avoiding repeated measurements of every ranking cell. Use resizable columns, skip unchanged rankings, and preserve the chosen sort order.
- Reduce image card editing memory use with previews capped at 1080 pixels on the longest edge and a smaller background cache. Render full resolution only when saving, copying, or opening the image viewer, preserving the selected export quality.
- Release image card previews, album artwork, and image caches when leaving the card tab or hiding the editor. Discard late artwork results and rebuild the preview on return while preserving card settings.

## [2026-11-15]

### Added

- Add a built-in Sleep timer to the player dock, with preset and custom durations, a live countdown, five-minute extensions, replacement, and cancellation. Pause immediately when time expires or finish the current song; also offer stopping at the end of the song without a countdown. Respect repeat, shuffle, gapless transitions, and processed audio tails. Remember timer defaults while clearing active timers on app exit.
- Add a Statistics workspace (Ctrl+3) with listening time, play counts, unique songs, daily activity, and ranked songs, artists, and albums. Filter by Today, Last 7 days, Last 30 days, or All time. Record actual listening time across speed changes and repeats while excluding pauses, seeks, buffering, system sleep, and user-muted playback. Store statistics locally, with recording enabled by default and controls to disable recording or clear statistics without removing music, playlists, or Recent entries.
- Add an **A–B repeat** menu to the player dock, styled to match the other player buttons. Mark A and B at the current playback position, repeat the range, view its timestamps, or clear it. Show large seekbar flags in two distinct theme-dependent colors, with a shaded gradient range and bracket; keep the seekbar knob above the markers and separate nearby labels for short loops. Keep ranges for the current track only, preserve paused playback, suppress successor preloading during practice, and let sleep timers take priority.
- Add optional lightweight crossfade under Settings > Playback (Off or 1–10 seconds). Reuse the bounded two-track audio engine and one output, preload near transitions, shorten overlaps for short tracks, and retain queue, ReplayGain, and sleep-timer behavior. Requires FFmpeg and normal speed and pitch; track details and lyrics switch at the end of the overlap.
- Add timing-preserving text corrections to the lyrics editor. Keep existing karaoke timestamps and readings on unchanged text, preserve the correct timestamps when editing repeated words, and highlight changed chunks for timing review.
- Add Shift/Ctrl multi-line selection and a **Selected Lines…** menu for batch timing shifts, formatting, movement, and deletion. Preview affected lines before shifting timing, applying formatting, or deleting, and restore selections with Undo/Redo.
- Add **Split at Cursor…**, **Merge with Next…**, and **Merge Selected…** to the lyrics editor, with previews and Undo/Redo. Preserve word timing, readings, formatting, and translation rows; offer space, newline, or no separator when merging. Estimate new timing boundaries when splitting inside a chunk and flag those chunks for review.
- Add a live karaoke reference beneath the pitch readout, showing the vocal shift when singing one octave lower (for example, +4 semitones gives −8 semitones from the original).
- Add persistent Favorites with a heart control for the playing song, clickable library hearts, multi-track actions in library and queue context menus, and a Favorites filter that combines with metadata or lyric searches.
- Add a personal reading-dictionary manager under Settings > Lyrics > Global > Readings, with named, enabled dictionaries and editable Reading, Word, Reading type, and Comment columns. Support Japanese, Mandarin, Korean, and Cantonese entries, longest-word matching, dictionary priority, and JSON/TSV import/export including Google Japanese Input exports. Apply saved entries over generated readings while preserving song-specific ruby, translations, and karaoke timing.
- Expand library and queue track context menus with saving selections to new or existing playlists, opening file locations, copying file paths or names, and viewing track properties. Add immediate playback of library selections and queue movement controls, preserving multi-selection and repeated tracks.

### Fixed

- Use consistent themed dropdown indicators and numeric step buttons in the Sleep timer and Statistics filter, including light, dark, and disabled states.
- Update the library performance-test fixture with the Favorites filter and current column layout, resolving five test setup errors.
- Disable Floating Lyrics when its window is closed, preserving the disabled state across playback and app restarts.
- Size the reading-dictionary table's dropdown columns immediately after adding entries or switching dictionaries, so Reading type and Match labels display fully without resizing the window.
- Enable Save and show unsaved changes immediately after committing lyric waveform edits. Refresh playback timing and previews after each commit.
- Move the next line's karaoke starts, ends, and line end together when committing the Lyrics waveform's B boundary, preserving word synchronization and annotations.
- Keep Source-tab Undo/Redo consistent between toolbar buttons and keyboard shortcuts, including after reloading, saving, or applying source edits. Update button availability while typing and discard obsolete redo history after new edits.
- Include positive and negative lyric offsets in timing validation, reporting lyrics that play before the audio starts or beyond its duration without rejecting timestamps that the offset moves into range.
- Improve Japanese readings and word boundaries in lyrics that use katakana for grammatical endings, such as `夜ニ紛レ` and `見下ロシテ`. Analyze recognized rows with hiragana grammar while preserving the original lyric text, decorative symbols, and karaoke timing.
- Default personal reading entries to whole-word matching using tokenizer boundaries, keeping `私 → わたし` from changing the reading of `私立`. Preserve boundaries across ruby splitting, number readings and batch generation, and offer an explicit **Inside words** match option for intentional partial replacements. Keep matching conservative when word boundaries are unavailable.
- Prevent player freezes during rapid song and pitch changes by queuing decoded audio on the audio thread instead of calling Python from Qt's renderer. Discard outdated queued buffers and preserve final samples at track endings.

## [2026-11-14.4]

### Added

- Add shortcuts to the lyrics editor's **Lyrics** tab: Q/E seek by 100 ms, A/D seek by one second, F or Ctrl+Enter marks playback time and advances, Ctrl+Shift+Enter commits timing, Alt+Up/Down selects lines, Alt+Shift+Up/Down moves lines, and Ctrl+N adds a line. Include a **Shortcuts** reference and button tooltips. Preserve normal typing in text, reading, search, and timestamp fields.
- Add **Shift + left-click** on the Lyrics waveform to start continuous playback from the clicked position without moving timing markers. Respect waveform zoom and pan, and clear any previous segment-preview playback limit.

### Fixed

- Preserve normal text proportions and alignment for wrapped lyrics in **All romaji** mode by skipping extra horizontal compression after wrapping.

## [2026-11-14.3]

### Changed

- Reduce idle startup memory by loading Settings, the lyrics editor, and translation panels on first use.
- Create the Floating Lyrics window only when shown, while preserving saved position, current lyrics, and settings on first display.

### Fixed

- Release cached full-resolution card backgrounds, watermark images, and album artwork when the lyrics editor closes, reducing memory retained after creating image cards.
- Delete the Floating Lyrics window when its plugin unloads and cancel pending layout updates, preventing callbacks from accessing deleted controls.

## [2026-11-14.2]

### Changed

- Debounce library column resizing saves and write only the column layout to a small preferences file, avoiding repeated serialization of the full track library. Preserve existing column preferences and save pending changes when closing.
- Limit Discord Rich Presence artwork resolution to one worker and one replaceable pending track. Discard outdated queued requests during rapid track changes and stop queued work when the plugin unloads.

### Added

- Add **Open Folder** to the lyrics editor's Source tab. Reveal and select the lyrics file in Windows Explorer or macOS Finder when possible; otherwise open its containing folder.

### Fixed

- Prevent paused audio from briefly resuming when closing the app on Windows by discarding buffered sound before stopping the audio output.
- Preserve pitch-shifted playback when changing the visual theme by skipping redundant audio device reconfiguration.
- Prevent FFmpeg command windows from popping up while generating audio waveforms in the Windows EXE.
- Prevent crashes when closing during a library scan by requesting cancellation and waiting asynchronously for the scanner to finish before destroying the player.
- Use the latest folder selection after repeated library scan requests, preventing outdated scan results from being saved under a newer folder configuration.
- Preserve the final samples during pitch processing and wait for the audio device to finish playing them before advancing tracks. Manual stops, seeks, and track changes discard pending audio and completion notifications.
- Restore normal audio playback when the installed Pedalboard fallback cannot stream pitch-shifted audio, instead of producing silence.
- Release completed library scan workers and temporary library and queue context menus, preventing repeated scans and menu openings from accumulating these objects in memory.

## [2026-11-14]

### Added

- Add the official community wiki to **Help** and the F1 shortcut, while keeping the bundled offline manual available from **Help > Offline manual**.
- Expand the Lyrics Translator language list with Indonesian, Turkish, Arabic, Hindi, Hebrew, Nordic, Central and Eastern European, and Baltic languages.
- Add persistent yt-dlp download queues with one to four concurrent tracks, batch URL input, multiple search selections, and paged playlist selection. Downloads continue when the import window closes; unfinished tracks can restart after an app restart. Include per-track cancellation, retries, existing-file reuse, and ordered playback imports.
- Remember recent lyrics searches (15), library searches (20), and yt-dlp searches/URLs (50), with autocomplete and clearable history menus.

### Fixed

- Prevent FFmpeg command windows from popping up while generating audio waveforms in the Windows EXE.
- Prevent Korean library metadata from falling back to Gulim or Batang when Malgun Gothic is available.
- Keep audio playing when resetting pitch shift to its default value, including when volume boost is active. Reuse the decoder's audio buffer when switching back to native playback or enabling pitch shift again.
- Recognize date-only release versions such as `2026-11-14` in the updater, treating them as revision zero when comparing available updates.

## [2026-11-13.2]

### Changed

- Give the basic Green, Blue, Purple, Rose, and Orange theme presets coordinated backgrounds, surfaces, controls, borders, and player colors in both light and dark mode. Selecting a basic preset replaces colors left over from the previous theme.
- Bundle theme presets with PyInstaller releases and check that the preset file is present before creating the release archive.

thank you to my sweet bff tutel on discord for finding bugs!! <333

## [2026-11-13]

### Added

#### Setup wizard and dialogue

- Add a first-run setup wizard with Ayami guiding users through Welcome, Music library, Appearance, Reading preferences, Lyrics, and Ready. Show it before the dependency notice on fresh installations, and preserve the normal startup flow for users with an existing configuration.
- Add **Settings > General > Relaunch Wizard** for returning users. Open Ayami's companion view from this control, with **Run setup again** available to return to the original setup steps.
- Add a live Reading preferences preview for lyric font size, furigana, romaji readings, and replacing lyrics with romaji. Use supplied sample readings without loading a song, pronunciation dictionary, or network provider. **Apply and Next** saves edited global preferences and refreshes the player; **Do this later** discards the draft while preserving saved preferences and per-song overrides.
- Add remembered text-speed, **Auto-read**, **Reduce motion**, and **Sound** controls. Allow manual dialogue advancement by clicking the text, pressing Space, or using the advance arrow; keep explicit pauses, choices, and application-event waits independent of text speed and Auto-read.
- Add bundled reaction sounds and expression labels, using a separate audio player so guide reactions do not change music playback or its volume. Muting stops the current cue immediately while retaining expression labels, and missing clips do not block dialogue.
- Add interactive lyric-search guidance that responds to searches starting, finding results, returning no results, failing, or closing. Keep Search Lyrics nonmodal during the tutorial so the guide can remain usable alongside it.
- Add branching conversations, optional farewell scenes, and a DDLC-inspired detour with early exits, a developer confrontation, recorded dialogue, and a closing poem. Scene cleanup stops audio, closes auxiliary windows, and restores the guide's position when returning to setup.
- Add a documented Ren'Py-inspired dialogue engine in `assets/onboarding/onboarding.rpy`, with labels, inline actions, reusable calls and returns, session variables, scalar conditions, conditional menu choices, event waits, fractional pauses, and randomized dialogue bags that exhaust their variants before repeating. Include a Debug panel for navigating steps and inspecting dialogue state; scripts do not execute Python.
- Add interrupt-and-resume reactions to moving the guide and choosing appearance presets. Restore the interrupted line, typing position, actions, and remaining timers after a reaction; defer reactions during menus and scripted motion, and retain only the newest queued theme reaction during rapid preset browsing.

#### Optional AI companion prototype

- Add a remembered **AI features** toggle to the returning-user window. Turning it off stops pending chat and memory requests and returns to the original scripted setup VN; turning it back on restores the chat without sending a request. Preserve saved conversations, memories, and provider settings across mode changes.
- Add optional AI chat to Ayami's returning-user companion view using a user-configured OpenAI-compatible Chat Completions endpoint, model ID, and API key where required. Support HTTPS endpoints and local HTTP servers without choosing a provider or model automatically. First-run setup remains scripted and makes no AI requests.
- Add **AI settings** with session-only credentials by default and optional Windows account-bound encrypted storage for the API key and custom headers. Explain when encryption is unavailable, keep credentials session-only in that case, and remove stored credentials when their fields are cleared and saved.
- Add advanced JSON settings for extra request-body parameters, excluded parameter names, and custom request headers. Validate their types, finite numeric values, and header contents before saving or sending, while protecting the model, messages, streaming mode, and managed authentication/transport headers.
- Add an API-key setup guide with provider links, navigation, and usage/cost guidance. Opening the guide makes no AI requests, and closing it returns to the settings form with unsaved fields intact.
- Add locally saved conversations with a conversation picker, **New conversation**, **Rename**, and **Delete**. Autosave sent messages and replies, restore unfinished requests as stopped, and browse exchanges with **Previous**, **Next**, and a page indicator. Reopening history does not resend requests or replay reaction sounds.
- Add **Stop** for pending replies and **Regenerate** for the latest answer. Preserve the original answer until regeneration succeeds, replace it on the same history page, and ignore stale completions after cancellation or closing. Requests are asynchronous and do not automatically retry; stopping local waiting cannot guarantee cancellation at the provider.
- Add user-edited memories, with up to 20 facts of 200 characters each and a toggle to exclude them from future requests without deleting them. Include enabled facts and up to ten recent completed exchanges in chat context; do not automatically attach songs, lyrics, paths, player state, or application settings.
- Add memory extraction modes for **Off**, **Suggest for approval** (default), and **Automatically save ordinary preferences**. With inclusion enabled, check new user messages every three completed exchanges or through **Check now**, using a separate provider request. Review suggestions with supporting quotes and source pages, edit or dismiss candidates, and require approval for conflicts and facts outside ordinary preferences. Retain suggestions, dismissals, provenance, and extraction checkpoints locally.
- Add provider-reported input, output, and total token counts, cumulative usage for the current window, and cached/reasoning details when supplied. Count memory checks separately in the display while including their usage in the window total; mark missing usage as unreported rather than presenting an incomplete total as complete.
- Add optional bundled expression reactions to AI replies, respecting the Sound toggle. Display replies as plain text, accept only known reaction names, and keep model output unable to execute dialogue commands or application actions. This prototype displays completed replies without streaming.
- Save conversations and memories atomically in the app-data directory's `companion.json`, separately from provider credentials. Preserve invalid existing data and disable saving instead of overwriting it; report save failures in the companion window.

#### Audio import

- Add title/artist search to **Plugins > Search and import audio…** in the yt-dlp Audio Import plugin. Return up to ten YouTube results with titles, channels, durations, and thumbnails when available, excluding live and upcoming broadcasts. Search runs in the cancellable worker and needs no additional dependency or API key.
- Preview a search result by clicking it or using the arrow keys and Enter, then use the existing audio download workflow. Keep results available for choosing another track, clear them when the query changes, and retain direct-URL preview/import support.

#### Offline manual and wiki

- Add **Help > User manual** and the F1 shortcut to open the bundled offline manual in the default browser after confirmation. Report missing manual files or browser-launch failures, and include the manual directory and its assets in packaged builds.
- Add an offline wiki-style manual with 28 articles covering setup, playback, lyrics, settings, editing, sharing, plugins, and reference material. Include real UI screenshots with numbered callouts, local article search, generated contents, section permalinks, breadcrumbs, related articles, an A–Z index, print styling, and layouts for narrow screens.
- Add English, Japanese, Korean, and Cantonese (Traditional Chinese) manual editions. Preserve article, section, search, or source-view routes when switching languages with JavaScript enabled. Bundle Cantonese serif and sans-serif fonts and their license so those pages do not require online font downloads.
- Add a remembered dark-mode toggle and read-only **View source** tabs for article HTML. Keep articles readable when JavaScript is disabled, with interactive search and navigation enhancements available when it is enabled.
- Add read-only **Talk** pages with explicitly fictional discussions, nested replies, topic navigation, comment permalinks, copy-link controls, and deterministic fictional timestamps capped at the current time. Add localized fictional login, registration, and password-reset pages; these do not create accounts, post comments, or transmit credentials.
- Add an offline zero-gravity Easter egg activated by **↑ ↑ ↓ ↓ ← → ← → B A** outside text fields. Let readers drag and throw article words, respect reduced motion, and restore the article with Escape, **Restore gravity**, navigation, resizing, or printing.
- Add a GitHub Pages deployment workflow for the manual from the `release` branch, including its referenced assets and Markdown specifications. Rewrite repository-relative links in the staged website while retaining the manual's direct-from-disk layout in the source tree.

### Changed

- Share theme-preset application between Settings and onboarding so both apply the same mode, accent, palette, and advanced color overrides.
- Style the main player's horizontal volume slider with the active theme's track, accent, and handle colors.
- Reduce Lyrics Editor document-state overhead by recording immutable field snapshots instead of deep-copying lyric model objects for dirty-state comparisons. Capture the initial saved-state baseline once after opening while retaining separate mutable copies for undo history.
- Checkboxes are no longer invisible.

### Fixed

- Reduce the risk of Qt crashes caused by Python cycle collection destroying GUI objects on a worker thread. Schedule young-generation and periodic full cycle collection on the GUI thread between Qt events. The previously reported rare playback crashes still require reproduction to confirm whether they are resolved.
- Refresh the local lyric index when an existing lyric file changes in place, including edits to attachment tags. Check file timestamps and size as well as directory changes so changing an attachment does not leave an obsolete cached audio-to-lyrics match.
- Keep saved-state comparisons sensitive to all lyric-line and segment fields, including nested formatting, readings, translation-row indices, and timing. Undoing back to the opened document restores the clean state, and redoing an edit marks it dirty again.
- Keep automatically generated furigana attached to whole tokenizer words even when authored karaoke timing divides those words into multiple chunks. Project each chunk's timing onto the displayed word, including timing gaps and chunks spanning several words, without modifying the source timings. Preserve authored ruby priority and replace old generated chunk readings when whole-word cached readings become available.
- Align the furigana and romaji highlight sweep with the highlighted fraction of the base text. Short readings no longer wait for the base-text sweep to reach their screen position, and long readings no longer finish late merely because their displayed width differs.
- Preserve small-kana combinations and combining marks when splitting readings in the karaoke separation editor. Use visible kana as alignment anchors where possible, keep authored readings through save and undo, and avoid turning automatically generated readings into manual ruby when changing visual separation.
- Ask before leaving setup through Close, Escape, or **Skip setup**, defaulting to **Keep setting up**. Pause dialogue timers, motion, and queued actions during confirmation and buffer application events; resume the scene when staying, or discard unapplied reading drafts while retaining saved preferences when leaving.
- Keep Instant text and manual advancement from bypassing scripted pauses or replaying inline actions. Prevent Space on pacing controls from also advancing dialogue, and continue past conditional menus with no enabled choices instead of leaving setup stuck.
- Fall back to Uguu when Discord Rich Presence cannot upload embedded album artwork to Litterbox. Validate upload responses, retain existing cached cover URLs, and track each host's expiry separately: 12 hours for Litterbox and 3 hours for Uguu.
- Fixed ReplayGain preamp using a regular QSpinBox.

## [2026-11-06]

### Added

- Add Core and User theme presets, including automatic mode and palette changes. Save, import, export, and delete user themes; remember the selected theme across restarts; and customize surface, background, cell selection, hover, and other colors per mode.
- Add optional gapless playback with bounded two-track FFmpeg prebuffering and a continuous output sink at normal speed and pitch.
- Add ReplayGain track/album tag playback, album-to-track fallback, preamp, and peak-based clipping prevention in Settings > Playback.
- Add persistent crash traces in `%LOCALAPPDATA%\LRCPlus\logs\crash.log` to help diagnose rare crashes.
- Add a Windows memory profiler with runtime feature logging, optional Python allocation tracing, and a temporary switch for comparing the same song with word highlighting disabled.

### Changed

- Improve audio fidelity at nonstandard playback speeds with band-limited resampling that preserves phase and sample history across decoded buffers.
- Reduce startup allocations by deferring networking and DSP imports, creating lyric renderers on demand, and generating Mandarin readings in a disposable worker instead of keeping pronunciation dictionaries in the player.
- Derive library cells from shared track metadata instead of retaining duplicate formatted values.
- Defer hidden waveform decoding and background blur processing. Create image-card controls only when their editor page is opened, and reduce redundant preview and image-buffer copies.
- Downscale large playback background images to a maximum of 1024 pixels per side and retain a compact encoded copy, preserving aspect ratio and transparency without modifying source files. In a controlled cover-art check, the retained encoded image shrank from 8.2 MiB to 252 KiB, and its full decoded size fell from 29.8 to 4 MiB.
- Defer background pixel decoding until visible and release decoded artwork when hidden or disabled. Decode scaled layouts at the screen's actual pixel density and refresh the source resolution when the viewport changes.
- Use glyph-checked font runs in Recents, Queue, Settings folder lists, and Flashcards to avoid loading large legacy fallback fonts while preserving mixed scripts and emoji.
- Populate Flashcards' All Lyrics list on demand. In controlled Windows checks, Recents' first-open working-set growth fell from about 70 to 10 MiB, and Flashcards' UI growth after word analysis fell from about 74 to 6 MiB.

### Fixed

- Discard stale audio and route-start notifications during rapid speed, pitch-mode, and track changes, preventing an obsolete playback route from muting native audio.
- Flush remaining resampled audio once at the end of a stream, preserving short clips and track endings without duplicating samples.
- Address a potential cause of rare playback crashes by delivering background lyric readings on the UI thread and safely discarding pending results when lyric rows are removed. The reported crashes, observed twice with Korean lyrics, have not been reproduced; this change is not yet confirmed to resolve them.
- Stop hidden Floating Lyrics from processing multiline and translated text and loading additional font/layout resources. Refresh the current lyric when the window is shown, including while paused. Both `{\n}` and `{\nt}` remain supported.
- Cache karaoke settings and refresh them when changed instead of parsing the entire settings file on every highlighting frame. Disconnect media callbacks when the plugin unloads.
- Release closed Pitch, Timing, Dependency, Update, Flashcard, and Karaoke Screen dialogs and their owned resources. Preserve active worker lifetimes until completion and flush pending pitch changes before disposal.
- Release explicit confirmation dialogs after use; retain the existing cleanup for Recents, Settings, the lyrics editor, lyric search, and image viewers.

### Known Issues

- Previously reported gradual memory growth in the Lyrics Editor and Translation workspace has not been reliably reproduced or confirmed resolved. Remaining idle-to-playback increases and large image-card generation peaks are not fully attributed across real workloads.
- Font, decoder, artwork, reading-engine, and DSP caches can remain allocated after first use. Standalone Translation and URL Import intentionally retain one window's UI state until plugin shutdown. Closing a dialog does not guarantee a return to startup memory usage.
- Quoted savings are controlled Windows development-process measurements, not guaranteed packaged-app or Task Manager totals. Flashcards measurements separate UI allocations from word-analysis memory. Earlier offscreen probes lacked the installed Windows font database and do not establish native text-rendering costs.
- See the [memory investigation](docs/memory-usage.md) and [dialog audit](docs/dialog-memory-audit.md) for reproduction details, measured scope, and areas that still need profiling. Further optimization should follow reproducible growth or unusually large allocations.

## [2026-11-05]

### Added

- Add Datpmt's free Google Translate integration to Lyrics Translator, with per-line requests and no API key requirement.
- Add SyncLRC to Search Lyrics with cached previews, karaoke timing imported as LRCX, and line-synced LRC or plain-text fallbacks. No API key is required.
- Add settings search with label, description, and alias matching; Global/Local result paths; navigation to highlighted controls; Ctrl+F and Escape shortcuts; and guidance for settings hidden by another option.

#### Lyric image cards

- Preserve album-cover size when enabling its shadow. Paint shadows outside the image bounds and reserve spacing between artwork and text.

- Add album-art corner radius, border width and color, an optional soft shadow with opacity, blur, and offsets, and placement to the left or right of song details. Keep template placement as the default, preserve artwork aspect ratio, keep shadows clear of text, and save styling with presets at every export quality.

- Add **Quality** choices for Standard (1×), High (2×), and Ultra (3×) rendering. Repaint text, decorations, and effects at the chosen resolution while preserving layout, show the resulting pixel dimensions, and retain quality in presets, PNG exports, and clipboard images.

- Add **Story (1080 × 1920)** canvas size and independent title and artist/album typography in the **Text** tab. Customize each font, maximum size, and color, or turn overrides off to restore template typography. Save the new settings with card presets while preserving older preset behavior.

- Add image viewer. Click on the outputted image. click on it... click on it :)... click it. clcik. ckcc
- Add an integrated **Share > Image card** page to the lyrics editor, alongside text sharing. Build an excerpt from explicitly checked lines in document order, including when the sidebar is sorted differently; use **Select All** to share the full song.
- Preview the finished card while editing its style, copy the full-resolution image to the clipboard, or export it as PNG. Choose square (1080 x 1080), portrait (1080 x 1350), or landscape (1920 x 1080) output; the preview scales to the available space without changing export resolution.
- Automatically fit long excerpts inside the available lyric area. Report excerpts or readings that cannot fit instead of clipping them or exporting an older preview.
- Choose **Classic** for a rounded frame and restrained accent, **Spotlight** for a large quotation mark and footer details, or **Record sleeve** for a record-inspired sidebar and separate lyric panel.
- Add **Polaroid**, with a paper frame, photo area, and lyrics below the artwork. Place song details below the photo or beside the cover; use a themed photo treatment when artwork is unavailable.
- Add **Editorial**, with an oversized song heading, divider rules, and offset lyric text. Keep the composition usable with or without an album cover.
- Add **Cinema**, with letterbox bars, lower-positioned lyrics, and a shaded, blurred artwork backdrop. With Gradient fill, reuse available album artwork for the backdrop; Solid and Image fill retain the chosen background.
- Add **Notebook**, with ruled lines and binding marks; **Ticket**, with perforations and song details below the lyrics; and **Art Deco**, with a double frame and stepped corner ornaments. All three support custom backgrounds, artwork, and saved presets. Notebook and Ticket start in the **Paper** theme when selected manually.
- Align Notebook's horizontal rules to the final lyric baselines, following wrapped lines, fitted font size, line spacing, translations, furigana, and vertical lyric position.
- Add **Streaming**, inspired by rounded music-sharing cards, with lyrics above a waveform and song details; **Receipt**, with cut-paper edges and print rules; and **Mixtape**, with a cassette illustration above the song heading. Receipt and Mixtape start in **Paper**. Preserve editable themes, artwork, backgrounds, text effects, and preset round trips for all three.
- Start Polaroid and Editorial in the **Paper** theme when selected manually. Allow theme changes afterward and restore the exact saved theme when loading a preset.
- Offer **Midnight**, **Paper**, and **Rose** themes across all six templates. Override background, gradient end, lyrics, title, artist/album, and accent colors individually, or return to the theme colors.
- Customize Solid, Gradient, and Image backgrounds. Adjust gradient angle and background-image crop position, zoom, blur, and dimming; preserve image proportions while filling the canvas.
- Set the lyric font, maximum font size, and left, center, or right alignment. Add card-wide bold, italic, underline, or strikethrough, and adjust padding, line spacing, and the lyric block's vertical position.
- Edit the song title, artist/credit, and album independently, initially populated from `[ti:]`, `[ar:]`, and `[al:]` metadata. Put artist and album on a second row below the title, or group all details beside the album cover. Fall back to the below-title layout when artwork is unavailable.
- Include an optional, adjustable album cover from artwork already resolved by Discord RPC or embedded in the audio file. Load artwork in the background for the attached/editing song, expose refresh/status controls, and avoid starting a new artwork search or upload just to create a card.
- Configure stroke color and width, plus shadow color, opacity, blur, and horizontal/vertical offsets. Apply these effects to lyrics and song details, scaling them for the metadata font sizes.
- Add an optional **LRCPlus watermark** with width, opacity, and any-corner placement controls. Reserve room for the logo so it stays separate from lyrics, song details, and artwork.
- Preserve LRCX inline emphasis, blockquote indentation and bars, visual line breaks, and smaller translation rows by default. Combine source formatting with card-wide styling, or disable **Preserve LRCX formatting** for plain lyric styling. Omit karaoke timing and highlighting from the image.
- Include authored readings, saved generated readings, and readings already cached by the editor. Give authored readings priority, keep annotated words together when wrapping, and avoid starting a reading provider during preview or export. Translation rows receive no automatic readings.
- Add a dedicated **Text > Furigana** section with an independent enable toggle and reading-size percentage. Choose **Auto**, **Above**, or **Below** for horizontal lyrics, and **Auto**, **Right**, or **Left** for vertical lyrics; Auto uses above/right respectively.
- Adjust **Extra gap** between ruby and its base text while retaining the existing tight placement at zero. Inherit the lyric color by default or choose a custom reading color. Independently enable or disable **Inherit stroke** and **Inherit shadow** for the readings.
- Add **Text > Typography > Writing direction** choices for horizontal text and top-to-bottom vertical columns flowing either right to left or left to right. Keep CJK characters upright, use available font-provided vertical punctuation/kana forms, and shape Latin words as sideways runs.
- Preserve grapheme clusters and combining marks in vertical lyrics. Start a new column at each explicit newline, wrap long columns, and keep common closing punctuation with the preceding text. Render furigana on its selected side and preserve source emphasis, smaller translations, quote markers, and continuous vertical decoration rules. Song details remain horizontal.
- Use alignment to position the vertical column block, line spacing to separate columns, and vertical position to move the block up or down. Automatically reduce text size when necessary; changing ruby size, side, or spacing participates in fitting.
- Save reusable style presets, including template, theme, background settings, typography, writing direction, details placement, artwork preference, watermark, effects, and furigana customization. Keep the current song's title, artist, album, lyric selection, and actual cover image separate from presets. Background-image presets remember the local file path.
- Validate saved style values and retain compatible defaults for older presets, including centered-only alignment, horizontal writing, automatic ruby placement, inherited reading color, and inherited reading effects.

### Changed

- Match the library search and playback-source dropdown arrows to the themed chevron style, and style Pitch like the Shuffle button.
- Split the lyrics editor, main player window, and furigana renderer into eight focused modules for document handling, playback, karaoke editing, library management, updates, lyrics search, layout, and painting.
- Use the LRCPlus logo in the main header and About tab, enlarging it in About and removing the duplicate title.
- Show lyric translation preview controls only for providers that support provider translations.
- Organize Settings with a category sidebar, a separate Background page, and scrollable pages. Split Global and Local lyric settings into Display, Readings, Typography, Highlighting, and Scrolling, with aligned file overrides and empty-line placeholders under Display.
- Reorganize image-card controls into **Layout**, **Text**, **Artwork**, and **Effects** tabs, each with its own scrolling settings page. Group related controls and expand optional sections only when enabled.
- Give font, title, artist, and album fields full-width rows. Add tooltips for long metadata and keep the start of prefilled text visible rather than scrolling immediately to the end.
- Put saved-style selection, saving, and deletion in a compact toolbar. Add a resizable settings/preview split, show output dimensions and selected-line count above the preview, and keep preview feedback plus **Copy Image** and **Save PNG** visible while settings scroll.

### Fixed

- Restore the LRCPlus header logo after dismissing the budget-cuts dialog.
- Reduce the delay when opening Settings by checking dependencies only when their page is first selected and reusing PATH lookups when locating external tools.
- Remove blank space left by the empty lyric-search status in the Lyrics Editor and tighten the translation quota dialog's height to its content.
- Save scroll animation easing using its internal value instead of its display label.
- Prevent processed-audio underruns when opening the lyrics editor, Recent Songs, and other dialogs. Prime a short output reserve and preserve the active sink and pitch history when decoded audio arrives in bursts.
- Avoid decoder-callback lockups during media stop and reconfiguration by receiving audio without emitting Qt wakeup signals from the renderer thread.

#### Lyric image cards and sharing

- Remove the implicit "nothing checked means all lyrics" fallback. With no selection, leave the image-card preview empty, explain how to select lines, and disable image copy/save. Text sharing also requires checked lines and leaves the clipboard and export dialog untouched when none are selected.
- Replace height-constrained optional-section headings with native checkbox rows, preventing themed or enlarged checkbox indicators from being cropped. Collapse their controls without discarding the configured values.
- Keep fitted font sizes, wrapping, and text positions stable when stroke, shadow, reading color, or reading-effect inheritance changes. Use a fixed paint gutter and contain effects within their assigned text areas instead of subtracting changing effect bounds from the fitting space.
- Continue underlines and strikethrough across styled text, spaces, and ruby expansion, restarting at wrapped rows and stopping at unstyled runs. Keep vertical decoration rules continuous through padding introduced by long readings.
- Bring above-line ruby closer to its base text. Preserve the surrounding text baseline when readings are above or below the line, including mixed annotated/unannotated text and wrapped rows; keep custom reading colors and decoration rules on the selected side.
- Keep ruby that opts out of stroke or shadow visually unchanged when the corresponding lyric effect is enabled, including avoiding an extra fill pass that could thicken unstroked reading edges.
- Update available ruby-position choices when the writing direction changes, and fall back to Auto if the previous side does not apply. Normalize incompatible or malformed position values when loading presets.
- Clear stale previews and prevent exporting them after invalid source edits, missing background images, or text/reading overflow. Write PNG output atomically so an unsuccessful encode or write does not replace an existing destination file.
- Keep artwork, song details, lyrics, and watermark areas separate while fitting the excaerpt, and preserve cover aspect ratio. Keep appearance-only effect changes independent of these layout reservations.

## [2026-11-04]

### Added

- Added ascending and descending sorting to the music library columns.
- LRCX translation rows using `{\nt}`, with smaller text and an Insert Translation button in the lyrics editor.
- Import aligned translations from NetEase, QQ Music, and KuGou. Search Lyrics now includes Original / Translated preview tabs and Simplified / Traditional Chinese conversion for translations.
- **Lyrics Translator:** a Translations tab in the lyrics editor with optional offline Argos Translate (poor quality), Azure AI Translator, Google Cloud Translation, DeepL API, and MyMemory.
- Translate missing rows or Ctrl/Shift-selected rows, review and edit the results, then apply them as one undoable edit. Repeated lyrics are translated once per run. (Lyrics Translator)
- A Quota button showing provider-specific rough usage estimates, selected-row estimates, and characters sent from the current tab. (Lyrics Translator)
- Save translation API keys with Windows account-bound encryption and restore them on the next launch. Clear a key's field to remove its saved value. (Lyrics Translator)
- Plugin API v6 translation-provider registration and optional dependency declarations that allow plugins to load without every engine installed.
- The lyrics overwrite warning dialog now has a "Save as..." button.
- Added an Advanced > Danger section in the lyrics editor with Clear Karaoke Tags, Clear Furigana, Clear Translations, and Clear Formatting actions, each with confirmation and Undo/Redo support.

### Changed

- Merge the cloud translation plugin into Lyrics Translator, preserving existing settings and encrypted keys. Argos remains optional, so cloud providers work without its dependencies. (Lyrics Translator)
- Make the language arrow a swap button, and preserve selected languages when refreshing installed models. (Lyrics Translator)

### Fixed

- Clear inline furigana spans when clearing all readings. Clearing translations preserves original lyrics, timing, furigana, and formatting.
- Keep the pitch changer responsive while dragging controls by coalescing live updates and settings saves, and reusing the pitch processor for fine adjustments.
- Fix Enter in the pitch fine-tune field activating the first preset and overwriting the typed value. Preserve entries in the selected units and show the correct unit tooltip.
- Prevent processed audio from stuttering during rapid window switching by moving audio scheduling and device writes onto a dedicated audio thread, with bounded queues to limit delay.
- Prevent song switches from blocking the interface while the previous waveform decoder exits. Cancel obsolete waveform calculations and ignore results from earlier tracks.
- Keep translation rows out of automatic reading generation, including the karaoke screen and Floating Lyrics. Preserve their smaller font and remove the extra empty reading gap. (Lyrics Translator)
- Preserve original lyric formatting, readings, offsets, and karaoke word/line endings when adding translations or saving bilingual LRCX files again. (Lyrics Translator)
- Warn before closing with unapplied translations or running work, including Save & Close and drafts from other providers. Reloading lyrics and closing the standalone translator also warn before discarding drafts. (Lyrics Translator)
- Preserve unselected drafts during partial translation, keep drafts associated with their original language pair when edited, and recognize successfully saved standalone results as saved. (Lyrics Translator)
- Prevent deferred translation workers from starting after shutdown, while allowing the standalone translator to reopen normally. (Lyrics Translator)

## [2026-11-03.3]

### Added

- Custom background folders, select any folder containing images to show it behind the lyrics.

### Changed

- Booru tags are now hidden for Custom and Album cover only, and remain visible when the selected source may use Booru artwork, including as a fallback.
- No longer recursively searches every configured folder whenever it needs a new background for a track.

### Fixed

- Updated outdated notes to use the theme’s muted color. (yes, its from early development shut up)

## [2026-11-03.2]

### Fixed

- Fixed mock not having _clear_shuffle_history(), so the callback crashes before it reaches load_audio(). (oh the github actions gods pls forgive)
- Fixed zero-wdith columns on srtttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt
Fixed zero-width columns on startup.

### Added

- Ctrl + 1 and 2 now switches tabs from Library to Lyrics.

## [2026-11-03]

### Added

- Show loading and outcome indicators for local lyric searches, Lyrics search providers, and music library scans.
- Mark lyrics search results with unknown timing using a question-mark icon.
- Added Ctrl+1 to switch to the Library view and Ctrl+2 to switch to the Lyrics view.

### Changed

- Reactivated scroll wheel on seekbar and volume bar. Made up and down arrow keys affect the volume.
- Now remembers previous tracks when proceeding to next track.

### Fixed

- Keep karaoke segment starts and ends in sync when a lyric timestamp is changed.
- Lyrics text now wraps around.


## [2026-11-02.2>4]

### Added

- **Highly Experimental:** Add a Windows updater that checks GitHub Releases and installs verified updates through a standalone helper with staged replacement and rollback.

### Changed

- Report the Karaoke plugin version as `1.0.0`.

### Fixed

- Detect `-exp` prerelease tags and versioned Windows ZIP roots; reuse the installed updater when a compatible package omits its own updater executable.
- Improve text contrast for selected Settings tabs and mode buttons.

## [2026-11-02]



(Yes, you're seeing this right. It's blank. What the fuck was I on? I don't know, ask PAST me. Not CURRENT me. I'm innocent, I swear.
Check changes on "https://github.com/pxib/lrcplus/commit/8a8dec46d0f9d23f226910ab77cb1bfb7e349c9c".)

## [2026-11-02.2]

### Changed

- Unit-specific quick presets for semitone, octave, and percentage pitch shifts.

## [2026-11-01.4]

### Added

- Remember whether Shuffle is on or off across app restarts. Save the choice immediately and preserve it when applying other settings.
- Generate Japanese number and counter readings in both Furigana and All Romaji, including `3回` (さんかい / sankai), `1人` (ひとり / hitori), and `20歳` (はたち / hatachi). Keep ambiguous dates, clock times, decimals, identifiers, and leading-zero codes numeric, and respect manual readings and the selected reading language.

### Changed

- Use Hepburn consonant conventions in romaji: `tch` before `ch`, and `m` for ん before `b`, `p`, or `m`. Preserve all vowel kana for singing, including `inoue`, `kuuki`, `ookii`, and `houki`; keep authored Latin text unchanged.
- Display authored ASCII and full-width spaces between Japanese phrases as comma pauses in All Romaji, while keeping generated word spaces, original lyrics, and karaoke timestamps intact.

### Fixed

- Ignore decorative symbols during Japanese analysis without removing them from the lyrics, allowing forms such as `日～々` to retain the reading ひび.
- Correct romaji quote spacing, keeping text inside opening/closing quotes together and separating quoted phrases from surrounding words.
- Join inflected forms such as `furimuite`, `hoshikute`, and `naritakute` instead of inserting spaces at every tokenizer boundary.
- Use `nani` for generated romaji in `何も`, while preserving explicitly edited readings.
- Preserve numeric punctuation in clock times, decimals, dates, and identifiers instead of inserting tokenizer spaces inside them.
- Convert small kana, doubled consonants, long-vowel marks, and voicing marks correctly across karaoke and formatting boundaries, including half-width and decomposed kana.
- Keep romaji annotations consistent across pieces of one reading and measure their displayed width correctly. Preserve explicit ruby edits, cache number readings during song prefetch, and retain locally resolvable number furigana when the parser is unavailable.
- Fix karaoke sweep rendering on opaque backgrounds so plain and highlighted glyphs use the same antialiasing path when normal and highlight colors are identical. (hope it passes the github tests lmao なななななななななななな)

## [2026-11-01.3]

### Added
- Added an option to play from Library and Queue.

### Fixed
- Furigana generation now no longer freezes the entire program when you open the editor while it's generating.
- Preserve karaoke chunks with identical start times when saving, reloading Source, or recovering unsaved edits.
- Apply and save karaoke timing edits on plain LRC files, including playback stamping and boundary nudging.
- Keep implicit karaoke endings tied to the next lyric when retiming, while preserving explicitly authored endings.
- Apply the lyrics offset consistently to editor previews, waveform markers, segment playback, and playback stamping.
- Warn before discarding karaoke timing or other LRCX features when converting TXT lyrics to LRC.
- Prevent saving or closing the lyrics editor from replacing the current song's lyrics after playback advances to another song.

## [2026-11-01.2]

### Changes
- Dropdowns, numeric spinners, sliders, and dials now ignore the scroll wheel, avoiding accidental changes when you're scrolling.

## [2026-11-01]

### Changes
- Temporarily disabled the Karaoke Screen plugin ahead of its move to another project, preserving its source code.
- Moved Focus transition to Lyrics > Rendering, with global and per-file settings and migration of the previous Karaoke preference.
- Added Focus transition under Settings > Karaoke > Animations, gently blurring and dimming surrounding lines as the current line comes into focus.
- Added line entrance and word lift animations, enabled by default with independent toggles in Settings > Karaoke.
- Added Rainbow, Pulse, and Spotlight karaoke sweep effects.
- Moved karaoke rendering properties and sweep FPS into Settings > Karaoke.
- Added an overwrite confirmation for all lyrics providers when the song already has lyrics.
- Added an option to automatically retrieve lyrics. (yt-dlp)

## Fixes
- Reduce memory growth when reopening Recent Songs by releasing closed dialogs and avoiding unnecessary layout of hidden filenames.
- Reduce memory spikes in KuGou, Kuwo, and QQ Music search results and lyric previews by correcting Unicode font coverage checks and avoiding unnecessary system font loads while preserving multilingual text.
- Avoid repainting static karaoke lines on every sweep tick and keep focus blur damage within each row, reducing unnecessary neighboring redraws.
- Karaoke softness and effect widths now scale with font size rather than segment length, with consistent glyph smoothing during playback.
- Karaoke effects now color each glyph once, preventing layered text and duplicated ruby/romaji rendering.
- Lyrics offset now also shifts karaoke highlighting in the main, scrolling, and fullscreen displays, keeping them synchronized.
- Floating lyrics window can now be moved if other dialogs are open. (Floating Lyrics)

merry christmas noobs

## [2026-10-31.6]

### Fixes
- **Experimental:** Fix KuGou artist/title searches by preserving the hyphen separator. Add song-search fallback for partial titles and single-character queries such as `あ`, `世`, and `笑`, then resolve lyrics using the selected recording's hash and duration.
- **Experimental:** Fall back to line-timed lyrics when KuGou word timing is malformed so affected results can still be previewed and saved.

## [2026-10-31.5]

### Added
- Added a search filter (Lyrics). You can now search for lyrics in songs.
- Add Normal and Lunatic modes to Scarlet Refrain, with separate high scores.
- Add fairy entrance animations, expanding death rings with feathered edges, and larger translucent red hit particles with a wider spread and a half-second fade.

### Changed
- Overhaul Scarlet Refrain's Flandre encounter with ten distinct spell mechanics, eight intervening non-spells, phase-owned hazards, spell capture tracking, and developer practice controls. Original timing and choreography approximations are documented in `assets/scarlet/flandre-encounter.md`.
- Keep the extra-stage theme playing through the warm-up, boss entrance, and opening dialogue until the boss theme starts at its dialogue cue. Keep the boss theme playing through the post-fight conversation until it finishes or is skipped.
- like i said bro dont click... oh no... is that reimu begging for donations GET THE FUCK OUT I DONT HAVE MONEY

### Fixes
- Remove Reimu's projectiles when they leave the playfield so homing shots cannot return from off screen.
- Prepare MIDI playback before the warm-up to reduce music startup delay.

## [2026-10-31.4]

## Changed
- **Experimental:** Remove the automatic Musixmatch token button while retaining manual token entry. Suggest searching with just the song title after provider errors, empty searches, or failed lyric lookups.
- **Experimental:** Remove NetEase's JSON credit rows from LRC results and normalize colon-separated fractional timestamps so previews, timing detection, and playback recognize them.
- **Experimental:** Check result timing automatically in the background, prioritize selected results, and cache loaded lyrics and lookup errors. Show unavailable results directly in the Timing column and try Kuwo's alternate lyric endpoint when its mobile service returns code 301.

## [2026-10-31.3]

### Added
- Install, check, update, and remove private FFmpeg/ffprobe and Deno tools from the Dependency Manager. Downloads are SHA-256 verified and detected automatically by new importer workers.
- **Experimental:** Add Musixmatch, NetEase Cloud Music, QQ Music, KuGou, and Kuwo to Search Lyrics. Musixmatch supports authentication tokens and developer API keys; automatic token retrieval may be refused by the service.
- **Experimental:** Prefer NetEase YRC with LRC fallback and convert KuGou KRC to LRCX, preserving millisecond word timing, word ends, and pauses. Kuwo provides line-timed LRC and may include translated lines alongside the original lyrics.
- **Experimental:** Keep each provider's query, results, selection, and loaded previews until the search dialog closes. Show timing type and recording duration, and remove provider headers, title/artist intros, and writing credits from Chinese-provider lyrics.

## [2026-10-31.2]

### Fixes
- Keep Floating Lyrics off on first launch while preserving saved visibility preferences. Enable it from Plugins > Floating Lyrics.
- Recover seekbar updates after missed duration or mouse-release events, and reliably seek when clicking or dragging to either end.
- Include Requests and Mutagen version metadata in Windows builds. Allow dependency checks for incompatible or unverified versions and explain repair restrictions for bundled libraries.
- Add optional Fugashi / MeCab and UniDic-lite dictionary installation to the Dependency Manager.
- Retry temporary LRCLIB failures, honor Retry-After, and show a clearer message when the service remains unavailable.

also happy halloween
(p.s thank you to Anti on discord for the feedback!)

## [2026-10-31]

### What changed again?
- dont click icon in about 7 times.

also happy halloween

## [2026-10-30.5]

### Added
- Added lyric formatting: `**bold**`, `*italic*`, `~~strikethrough~~`, and `<u>underline</u>`.
- Added single-level blockquotes using `> `, with an indent and a vertical bar spanning all rows of a lyric.
- Added formatting buttons and keyboard shortcuts, plus an **Insert Newline** control above the lyric field.

### Changed
- Moved **Shift Timing** and **Validate Timing** next to the lyric offset controls.

### Fixed
- Fixed `{\n}` rendering and multiline row spacing, preserving furigana and karaoke timing.
- Fixed batched reading generation losing text after a visual newline.
- Made underline and strikethrough continuous across spaces and furigana padding.
- Fixed LRCX furigana prefetch skipping lines with content segments, which caused MeCab to run as playback reached each line.
- Corrected logs that labeled ordinary LRCX content and reading tokens as karaoke.
- Files declaring `[format:LRCX]` now load correctly even with a `.lrc` extension.

## [2026-10-30.4]

### Added
- Now warns you if an audio device was disconnected. No more jumpscares, ig.
- Added Vietnamese placeholder album cover.
- Can now identify Vietnamese lyrics.
- (A little secret has been added.)

## [2026-10-30.3]

### So, what changed?
- FAFO. (http://fafo.urbanup.com/10894485)
- Here's what changed: https://www.youtube.com/watch?v=FtutLA63Cp8

## [2026-10-30.2]

### Added

- Add MusicBrainz as a provider for yt-dlp album covers. (yt-dlp)
- Added download speed in Mbps, displayed alongside the remaining time. (yt-dlp)
- Added album information, full release dates in the user's regional date format, and source audio codec, bitrate, and sample rate to track previews. Upload dates are labeled separately when release dates are unavailable; missing metadata stays hidden. (yt-dlp)
- Added an estimated download size before the Download button, updated for the selected audio format and hidden when unknown or reusing an existing file. (yt-dlp)
- Added playing, paused, and idle album placeholders in Japanese, Korean, Chinese, and Cantonese when album artwork is unavailable. Language follows the lyrics, defaults to Japanese, and can be selected manually. (Discord Rich Presence)

### Changed

- Restore last volume percentage when unmuting.
- Hid empty hints, so those areas only appear when they have content.
- Limited embedded artwork uploads to 512 pixels on the longest edge and 1 MB, preserving aspect ratio and the original artwork. Invalid images and inputs over 20 MB or 25 megapixels are skipped. (Discord Rich Presence)

### Fixed

- Reduced renderer lag on long lyrics with many pinyin or furigana annotations by caching text layout and karaoke timing lookups and skipping offscreen text drawing.
- Fixed MusicBrainz cover matching when YouTube credits include a composer alongside the primary artist confirmed by the uploader. (yt-dlp)
- Fixed placeholder images by using direct CDN image URLs instead of share pages. (Discord Rich Presence)
- Prevented repeated failed artwork uploads within a session and refreshed placeholders when lyrics or real album artwork become available. (Discord Rich Presence)
- Fixed converter mishandled YouTube’s rolling captions, causing each line to appear three times.

---

# Historical changes (that I removed)

## [2026-10-29.2]

### Added
- Inline ASS/SSA subtitle warnings, including qualified warnings for VTT captions with ASS-style effect markers.
- Subtitle language/source selection in yt-dlp Audio Import, original VTT/SRT downloads, optional LRC conversion, and subtitle-only imports for existing audio.
- Plugin API lyric attachments that refresh the current track without restarting playback or replacing authored lyric files.
- Optional yt-dlp Audio Import plugin with URL previews, M4A/MP3 downloads, metadata/artwork, cancellation, retries, and remembered source URLs.
- Plugin API v4 local audio import method for library/queue integration, and optional dependency extras support for matching yt-dlp components.
- Queue manager with drag-and-drop and multi-track reordering, Play next, Add to queue, and library context-menu actions.
- Named saved playlists with load, append, update, rename, and delete controls, plus local M3U/M3U8 import and export.

### Fixed
- Styled subtitle conversion now collapses repeated outline/color redraws, joins vertical CJK character columns, and handles consecutive cues without blank separators.
- Oversized lyric imports are rejected before creating scrolling widgets, preventing malformed subtitle conversions from freezing playback.
- Repeated queue entries retain their own playback position, including when restoring a session with missing files.

## [2026-10-29]

### Added
- Dependency Manager in Settings to install, repair, check, and remove optional plugin libraries.
- Setup notices for enabled plugins with missing or incompatible dependencies.
- Automatic private Python runtime setup for Windows builds, with libraries stored in the user's application data folder (Windows: %LOCALAPPDATA%\LyricsPlus)
- Dependency changes take effect after restarting LyricsPlus; failed installations preserve the previous working environment.

### Fixed
- Cantonese readings in packaged Windows builds now use the private Python runtime for their worker processes.

## [2026-10-28]

### Added
- Karaoke boundary nudging with a configurable millisecond step and selected segment audio preview that pauses at the end.
- Find lyric lines with Ctrl+F, next/previous matches, wraparound, and F3 shortcuts.
- Save without closing the editor, Ctrl+S, and a separate Save & Close action.
- Recovery of pending Source text, including unfinished syntax.
- Batch shifts for an inclusive range of lyric lines, preserving karaoke timing and annotations, with a preview and negative-timestamp protection.
- Timing validation with navigation to affected lyric lines.
- Atomic lyric saves and exports with a backup of the previous file.
- Regression tests and a Windows GitHub Actions test workflow.

### Changed
- Pitch shifting uses continuous Rubber Band R3 processing with shared stereo analysis, avoiding repeated block resets and crossfade artifacts.
- Reduced measured pitch-processing startup to about 96 ms at 48 kHz and 105 ms at 44.1 kHz without changing the processed audio samples.
- Preferences use a per-user application data directory with legacy migration and an explicit `LRCPLUS_SETTINGS_PATH` override.
- Lyric indexes refresh when directory contents change and after editor saves.
- Automated tests are included by default instead of individually allowlisted.
- Documented installation, editing tools, preferences, testing, and packaging.

### Fixed
- Pitch and speed changes clear stale queued audio; unchanged controls no longer restart pitch processing, and output queues stay bounded when a device stalls.
- Processed playback respects volume changes without also playing native audio.
- Karaoke waveform end markers consistently use seconds and chronological lyric boundaries; linked drags support implicit ends and reject invalid intervals.
- Re-separation preserves merged timing spans, trailing pauses, structural newlines, and complete Unicode graphemes.
- Authored Ruby edits replace generated readings and persist on save.
- Marking a single karaoke segment retains explicit karaoke status; resetting karaoke enables Save and preserves readings.
- Overlap warnings use actual table rows and support simultaneous visual rows.
- Close, Escape, and the title-bar button consistently protect unsaved edits.
- Editing without the karaoke plugin no longer references missing controls.
- Source reloads validate timed input before replacing the model, apply offset and generation mode, and preserve artist/title/custom metadata.
- Editing a multiline lyric timestamp no longer flattens its text.
- Karaoke shortcuts respect text cells and keyboard modifiers; duplicate event handlers no longer override each other.
- Typing spaces in lyric text no longer triggers the playback shortcut.
- Saving an unchanged TXT document does not rewrite it as timestamped text.
- Invalid karaoke timestamps restore the previous display without changing the document or history; single-segment timing retains karaoke status.
- Undo/Redo correctly track the last saved document and reading mode.
- Mark Playback Time advances to the correct source line in a sorted sidebar.
- Undo no longer skips the immediately preceding edit.
- Segment edits are captured before mutation for Undo.
- Restored parsing of manually edited karaoke timestamps and instant-fill controls.
- LRCX reloads preserve a final segment end independently of a later line end, retaining trailing pauses after a shifted document is saved.
- Failed lyric saves leave the editor dirty and recovery data intact.
- Regression tests use the current furigana and scrolling widget modules.
