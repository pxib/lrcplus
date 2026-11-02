# Changelog
Versions uses dates with optional revisions.

## [2026-11-02.4]

### Added
- **Highly Experimental:** Add a Windows updater that checks GitHub Releases and installs verified updates through a standalone helper with staged replacement and rollback.

### Changed
- Report the Karaoke plugin version as `1.0.0`.

### Fixed
- Detect `-exp` prerelease tags and versioned Windows ZIP roots; reuse the installed updater when a compatible package omits its own updater executable.
- Improve text contrast for selected Settings tabs and mode buttons.

## [2026-11-02.2]

### Changes
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
