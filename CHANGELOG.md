# Changelog
Versions use dates with optional revisions. / バージョンは日付を使用し、オプションで改訂履歴も付けます。

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


## [2026-11-02.4]

### Added

- **Highly Experimental:** Add a Windows updater that checks GitHub Releases and installs verified updates through a standalone helper with staged replacement and rollback.

### Changed

- Report the Karaoke plugin version as `1.0.0`.

### Fixed

- Detect `-exp` prerelease tags and versioned Windows ZIP roots; reuse the installed updater when a compatible package omits its own updater executable.
- Improve text contrast for selected Settings tabs and mode buttons.

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
