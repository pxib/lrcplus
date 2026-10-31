# Changelog
Versions uses dates with optional revisions.

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
