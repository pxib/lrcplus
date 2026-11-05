# Changelog
Versions use dates with optional revisions. / バージョンは日付を使用し、オプションで改訂履歴も付けます。

## [2026-11-05]

### Added

- Add Datpmt's free Google Translate integration to Lyrics Translator, with per-line requests and no API key requirement.
- Add SyncLRC to Search Lyrics with cached previews, karaoke timing imported as LRCX, and line-synced LRC or plain-text fallbacks. No API key is required.
- Add settings search with label, description, and alias matching; Global/Local result paths; navigation to highlighted controls; Ctrl+F and Escape shortcuts; and guidance for settings hidden by another option.
- Choose image-card song details below the title or grouped beside the album cover, with an editable album name and preset support. Apply proportionate stroke and shadow effects to titles, artists, and albums as well as lyrics.
- Create lyric image cards in the editor's Share tab using checked lines, with a live preview, three themes, square/portrait/landscape sizes, font and alignment controls, optional title and credit, clipboard copy, and PNG export.
- Add Spotlight and Record sleeve image-card templates alongside Classic, with the same themes, sizes, and text controls.
- Customize card colors, solid/gradient/image backgrounds, gradient angle, image crop/zoom/blur/dimming, padding, line spacing, and lyric position. Save reusable style presets without replacing each song's title or credit.
- Optionally include an album cover in lyric cards, reusing resolved Discord RPC artwork or embedded audio covers. Load covers in the background, adjust their size, and preserve the option in style presets.
- Add right-aligned lyric cards and bold, italic, underline, and strikethrough text controls, saved with style presets.
- Add configurable lyric-text stroke and shadow effects, including colors, outline width, shadow opacity/blur/offsets, and preset support. Keep effect edges within the card's text area.
- Add an optional LRCPlus logo watermark to lyric cards, with width, opacity, corner placement, and preset support. Reserve space around the watermark to keep lyrics, credits, and album covers readable.
- Preserve LRCX inline emphasis, blockquotes, visual breaks, and smaller translation rows in image cards by default, with a plain-text toggle saved in presets. Combine source and card styling without exporting karaoke timing or highlighting.
- Include authored and available generated furigana in image cards, with an independent toggle and size control saved in presets. Keep readings above their base words through wrapping, alignment, text effects, and quote/translation layout, without karaoke highlighting.

### Changed

- Split the lyrics editor, main player window, and furigana renderer into eight focused modules for document handling, playback, karaoke editing, library management, updates, lyrics search, layout, and painting.
- Use the LRCPlus logo in the main header and About tab, enlarging it in About and removing the duplicate title.
- Show lyric translation preview controls only for providers that support provider translations.
- Organize Settings with a category sidebar, a separate Background page, and scrollable pages. Split Global and Local lyric settings into Display, Readings, Typography, Highlighting, and Scrolling, with aligned file overrides and empty-line placeholders under Display.

### Fixed

- Restore the LRCPlus header logo after dismissing the budget-cuts dialog.
- Keep image-card font sizes, line wrapping, and text placement unchanged when stroke or shadow settings change. Fit text using a fixed paint gutter and contain effects within their text areas.
- Reduce the delay when opening Settings by checking dependencies only when their page is first selected and reusing PATH lookups when locating external tools.
- Remove blank space left by the empty lyric-search status in the Lyrics Editor and tighten the translation quota dialog's height to its content.
- Save scroll animation easing using its internal value instead of its display label.
- Prevent collapsed image-card option checkboxes from clipping with themed indicators. Require an explicit lyric selection for image and text sharing; keep previews empty and copy/export inactive until lines are checked.
- Reorganize image-card settings into Layout, Text, Artwork, and Effects, with collapsible optional controls, wider text fields, a resizable preview, and always-visible copy/export actions.
- Keep image-card underlines and strikethrough continuous across styled text, spaces, and furigana padding, restarting at wrapped rows.
- Bring image-card furigana closer to the base lyrics while preserving alignment and wrapping.
- Prevent processed-audio underruns when opening the lyrics editor, Recent Songs, and other dialogs. Prime a short output reserve and preserve the active sink and pitch history when decoded audio arrives in bursts.
- Avoid decoder-callback lockups during media stop and reconfiguration by receiving audio without emitting Qt wakeup signals from the renderer thread.

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
