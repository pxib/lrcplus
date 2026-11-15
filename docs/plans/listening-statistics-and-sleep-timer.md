# Listening statistics and sleep timer

Status: sleep timer and listening statistics implemented as main-player features.

## First version

### Listening statistics

Add a Statistics workspace alongside Library and Lyrics, using the current theme.

- Date filters: Today, Last 7 days, Last 30 days, and All time.
- Summary: listening time, qualifying plays, and unique songs heard.
- A daily listening-time chart and ranked tables for songs, artists, and albums.
- Song rows show title, artist, play count, listening time, and last played.
- An empty state explains that statistics start when tracking is enabled; old
  Recent entries cannot reconstruct listening time.
- Settings provide a tracking toggle and a separate Clear statistics action.
  Clearing requires confirmation and does not remove library or Recent entries.
- Proposed default: tracking enabled, with all information stored locally.

Counting rules:

1. Listening time means elapsed real time during advancing playback. One minute
   at double speed contributes one minute, even though two minutes of media pass.
2. Pauses, stops, buffering stalls, seeking, system suspension, and failed loads
   contribute no listening time. User-muted playback and zero user volume also
   contribute none; internal muting used to route processed audio is different.
3. Forward seeks never add the skipped duration. Listening to a passage again
   adds the time actually spent replaying it.
4. Proposed play threshold: the smaller of 30 seconds and half the known track
   duration, measured in actual listening time. Unknown-duration tracks use 30
   seconds. Short skips still contribute listening time but do not add a play.
5. Each playback attempt can qualify once. Pause/resume and seeks remain part of
   that attempt. Loading a track again or automatically repeating it begins a
   new attempt, including Repeat Track when no new source is loaded.
6. Gapless handoffs split attribution when the next track becomes audible,
   rather than when its decoder preloads.
7. Split listening time at local midnight. Attribute a qualifying play to the
   local date when its threshold is reached. Preserve the recorded local date
   and UTC timestamp so later timezone changes do not regroup old totals.

Storage and identity:

- Use Python's built-in SQLite support in the application data directory,
  separate from the settings JSON. No new package is needed for storage.
- Store schema version, track metadata snapshots, and per-track daily totals.
  A full timestamp-by-timestamp history is outside the first version.
- Normalize track paths using the existing `path_key` helper. Different files
  remain separate even if their title and artist match. Automatic reconciliation
  after moving or renaming files can be added later.
- Reuse cached library metadata; read uncached metadata away from audio callbacks.
  Missing artist/album tags appear as Unknown artist/Unknown album.
- Queue database operations away from audio callbacks and use a single writer.
  Checkpoint every 15 seconds and flush at pause, track boundaries, disabling
  tracking, and shutdown. A crash can lose at most the uncommitted interval.
- Disabling tracking flushes existing time, then records nothing. Re-enabling
  starts a fresh attempt. Clearing while playing discards pending pre-clear
  writes before starting a fresh attempt, so deleted totals cannot reappear.
- Storage failure must not interrupt playback. Surface an actionable status
  message and keep any retry buffer bounded.

### Sleep timer

Add a compact Sleep button in the player dock. While active it shows remaining
time; clicking opens the timer controls.

- Presets: 15, 30, 45, 60, and 90 minutes; custom duration: 1–720 minutes.
- Expiry action: Pause immediately, or Finish the current song.
- Also offer At the end of this song without starting a countdown.
- Active controls: add 5 minutes, replace duration, and cancel. Show the expected
  finish time where meaningful; once finishing a song, show Finishing current song.
- Expiry pauses playback, preserves the listening position where possible, and
  gives a quiet status message. It does not shut down the computer or close LRCPlus.

Behavior rules:

1. The countdown measures elapsed real time and keeps running during manual
   pauses and track changes. Playback speed does not change its duration.
2. Pause immediately is the proposed default. If expiry finds no actively
   playing track, consume the timer without starting or resuming anything.
3. Finish the current song selects the playing attempt at the deadline, then
   suppresses its successor. At the end of this song selects the current attempt
   immediately and is unavailable without a loaded track.
4. Finishing takes priority over shuffle, repeat, queued Play next requests, and
   gapless handoff. It consumes the timer once without changing those preferences.
   A manual skip after finishing is armed may select the successor, but must
   leave playback paused and consume the timer. Ordinary seeks stay on the target.
5. Arming At the end of this song while paused waits for that song to resume and
   finish. A countdown that expires while paused instead follows rule 2.
6. Manual Stop cancels the timer. Natural queue exhaustion also consumes it.
   Changing tracks before a countdown's deadline otherwise leaves it running.
7. Expiry must silence both native and processed/PCM output. If it coincides
   with a natural track boundary, it must win before another track can start.
8. System sleep must not extend a deadline. If the deadline passed during system
   sleep, pause and consume the timer on resume. Clock/timezone edits during
   ordinary operation must not unexpectedly extend or shorten the countdown.
9. Remember the last selected duration and action, but never restore an armed
   timer after restarting the app. Cancellation/expiry must not alter user volume.

## Integration points

The existing Recent list in `core/recent.py` records file loads and lyric
attachments, and retains only 20 entries. Keep it separate from statistics.

- `core/listening_statistics.py`: attempt lifecycle and time accounting with
  injectable clocks; no dependency on plugin events or UI widgets.
- `core/statistics_store.py`: schema, queued writes, aggregation queries,
  checkpointing, reset, and shutdown.
- `ui/statistics_page.py`: themed summary, date filters, a Qt-drawn daily chart,
  and ranked tables. Query/update when visible rather than on every audio tick.
- `core/sleep_timer.py`: explicit inactive, countdown, and finishing states;
  emit a single expiry decision and expose the current countdown to the UI.
- `ui/sleep_timer_dialog.py`: presets, custom duration, expiry action, and active
  timer controls. Follow existing dialog cleanup and keyboard-access patterns.
- `core/settings.py`: validated statistics preferences and remembered timer
  defaults. Active deadlines and listening history do not belong in settings.
- `core/audio_controller.py`: reconcile actual track/playback boundaries,
  including same-source repeats, and consult the timer before automatic advance.
- `core/gapless.py`: prevent successor buffering/handoff when finishing is armed;
  account for successors already queued to the audio sink. Invalidating only the
  next decoder is insufficient once successor samples have been submitted.
- `ui/main_window.py`: construct the services, connect native playback signals,
  add workspace/dock controls, and flush/dispose services during shutdown.

Tracking should use an elapsed clock, playback state, and media progression
together. Neither position differences alone nor time in PlayingState alone
handles seeks, stalls, speed changes, and suspension correctly. Process track
boundaries explicitly, since gapless playback may remain in PlayingState.

## Implementation order

1. Implement the sleep timer model and playback boundary guard, then add its dock
   control and dialog. Cover native, processed, and gapless playback together.
2. Implement statistics accounting and local storage, including reset, opt-out,
   midnight boundaries, and orderly shutdown.
3. Add the Statistics workspace and preferences, with empty/error states.
4. Run the targeted regression checks and document both features in the offline
   manual. Keep each feature usable independently.

## Acceptance checks

- Fake clocks verify pause/resume, seeks in both directions, short skips,
  playback-rate changes, muted playback, stalls, suspension, and midnight splits
  without waiting in real time.
- Repeat Track creates a fresh attempt; ordinary resume does not. Gapless
  playback attributes each audible interval once, with no overlap or missing song.
- Reopening the app preserves committed statistics. Disabling/resetting tracking
  prevents queued old data from returning. Simulated storage failure leaves
  playback responsive.
- Timer expiry fires once; add-time, replacement, cancellation, manual Stop,
  paused expiry, system resume, and app restart follow the documented rules.
- End-of-song expiry blocks both repeat and next-track progression, including
  buffered gapless successors. The processed audio tail cannot resume afterward.
- Expiry updates statistics at the same playback boundary and does not record
  time after audio pauses.
- UI checks cover keyboard operation, existing themes, narrow layouts, and
  releasing dialog widgets and signal connections after repeated opening.
- Run the relevant existing gapless, PCM playback, queue, settings storage,
  workspace visibility, and player shutdown tests alongside the new checks.

## Possible later additions

Detailed listening history, exports, library play-count columns, moved-file
matching, year-in-review reports, and optional timer fade-out can follow once
the first version's accounting and playback behavior are stable.
