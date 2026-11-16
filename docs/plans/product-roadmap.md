# LRCPlus product roadmap

Proposed order, 2026-11-16. This document plans future work; implementation
starts with the next feature selected by the user.

## Direction

Make listening to, understanding, and practicing multilingual songs easy.
Keep readings prominent, playback clean, and resource use low. Use the Namida
investigation as inspiration for individual workflows and evaluate each against
LRCPlus's purpose.

The user's Windows observations were approximately 80 MB idle and 100 MB playing
for LRCPlus, versus 270 MB idle, 330 MB playing, and 500 MB after further use for
Namida. Namida crackled during pitch shifting in the user's comparison, while
LRCPlus stayed clean with the same track and shift. These are informal observations
on one setup, not controlled benchmarks or evidence of a memory leak.

## Foundation: repeat controls (implemented)

The dock exposes repeat off, all, current track, and counted track playback.
Counted playback includes the current pass, shows progress, and advances normally
after the total. Stop or loading a track cancels the count; sleep timers take
priority. Existing A-B looping remains separate.

## 1. Remember each song's practice setup

Save optional speed and pitch overrides for a song. Songs without overrides
follow global settings. Make an active override visible in the player, with a
quick reset to global. Apply speed and pitch together when changing tracks.

Acceptance:

- Switching between songs restores each song's chosen setup without an audible
  burst at the previous song's pitch or speed.
- Editing global settings has predictable behavior when an override is active.
- Overrides survive restart; transient repeat counts keep their current
  session-only behavior.
- Playback remains clean during repeated track, seek, speed, and pitch changes.

## 2. Remember A-B markers per song until exit (implemented)

Keep each song's existing A-B markers in memory when switching tracks. Restore
them when returning to that song during the same app session. A complete range
resumes looping when playback runs; an A-only selection remains ready for B.
Use the existing seekbar markers and A-B menu for this workflow.

Acceptance:

- Set A and B on song A, skip to song B, then return: song A's markers and loop
  behavior are restored. Song B retains its own markers independently.
- Key markers by normalized file path, so the library and repeated queue entries
  for the same file share the same range.
- Preserve paused playback when restoring markers; restoration alone never
  starts playback. Stop retains markers, matching the existing A-B controls.
- Clear A-B removes only the current song's remembered markers. Switching songs
  suspends the outgoing loop without forgetting its markers.
- App exit discards all remembered markers. Fresh startup has no active ranges.
- Sleep timers continue to take priority over restored loops.
- Validate restored markers once duration is known; discard invalid boundaries
  if the audio has become shorter. Lyric edits leave audio timestamps unchanged.

## 3. Make readings easier to discover and correct

Put existing reading controls within reach of the lyrics view. Show which
reading mode and source are active. Provide a short path from an incorrect word
to a song-specific correction or a personal dictionary entry. Reuse existing
reading providers and dictionary precedence.

Acceptance:

- Users can find furigana/romaji controls while looking at lyrics.
- Correcting a reading preserves lyric text, timing, formatting, and translation.
- Preview a correction before saving and clearly show its scope.
- Optional language engines load on demand and release disposable workers.

## 4. Improve queue sessions

Add ordered "insert after last insertion," a stopping point on an upcoming
track, and named resumable queue sessions. Store track occurrences, their order,
the active occurrence, and the resume position. Offer clear resume and replace
actions. Keep session history bounded and user-manageable.

Acceptance:

- Adding A, B, then C as upcoming tracks preserves that insertion order.
- Repeated copies of a song stay distinct when saving and resuming a session.
- Resume preserves paused state until the user chooses playback.
- Stop points interact predictably with repeat, shuffle, and sleep timers.

## 5. Rediscover the local library

Start with "Rediscover": frequently played songs absent from recent listening.
Add saved filters for favorites, language, lyric availability, play counts, and
last played, then consider anniversary mixes and a yearly listening recap.

Use the existing per-track daily statistics first. Exact hour-of-day mixes and
co-listening recommendations require event-level history that the current daily
aggregate store cannot reconstruct. Plan that storage and its privacy controls
separately if those features become priorities. Keep recording opt-in and show
useful empty states while history is insufficient.

## Data protection alongside persistent features

Extend backup coverage to per-song playback overrides, dictionaries,
playlists, favorites, and optional statistics. Inventory the existing data files
before designing the archive; provide validation and restore review. Add file
relinking so moved music can retain its associated personal data. Avoid silently
merging different recordings with identical titles. Session-only A-B markers
stay in memory and are excluded from backups.

## Verification for each milestone

- Record idle, normal playback, pitch-shifted playback, and post-workflow memory
  on the same build type, machine, library, settings, and tracks. Record working
  set and private bytes separately, with warm-up and measurement times.
- Compare against the measured baseline; explain retained growth and confirm
  repeated open/use/close cycles stabilize. Establish numeric budgets after a
  repeatable packaged-build baseline exists.
- Check idle CPU/GPU, active workers, artwork caches, and shutdown cleanup.
- Verify audio by listening on the target Windows output device as well as
  automated transport tests. Include seek, repeat, processed tails, gapless,
  crossfade, sleep expiry, and rapid track changes as applicable.
- Review the default window layout, keyboard access, and light/dark themes.

## Larger options for later evaluation

Duet lyric layout, linked local music videos, device synchronization, and shared
listening sessions can be evaluated after the core listening and practice
workflows. Implement adaptations independently; Namida's main repository uses a
source-available license with code reuse restrictions.

References: [Namida playback](https://docs.namida.app/features/playback/),
[library](https://docs.namida.app/pages/library/),
[playlists and history](https://docs.namida.app/features/playlists-history/),
and [license](https://github.com/namidaco/namida/blob/main/LICENSE).
