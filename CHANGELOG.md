# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-28]

### Added
- Karaoke boundary nudging with a configurable millisecond step and selected
  segment audio preview that pauses at the end.
- Find lyric lines with Ctrl+F, next/previous matches, wraparound, and F3 shortcuts.
- Save without closing the editor, Ctrl+S, and a separate Save & Close action.
- Recovery of pending Source text, including unfinished syntax.
- Batch shifts for an inclusive range of lyric lines, preserving karaoke timing
  and annotations, with a preview and negative-timestamp protection.
- Timing validation with navigation to affected lyric lines.
- Atomic lyric saves and exports with a backup of the previous file.
- Regression tests and a Windows GitHub Actions test workflow.

### Changed
- Pitch shifting uses continuous Rubber Band R3 processing with shared stereo
  analysis, avoiding repeated block resets and crossfade artifacts.
- Reduced measured pitch-processing startup to about 96 ms at
  48 kHz and 105 ms at 44.1 kHz without changing the processed audio samples.
- Preferences use a per-user application data directory with legacy migration
  and an explicit `LRCPLUS_SETTINGS_PATH` override.
- Lyric indexes refresh when directory contents change and after editor saves.
- Automated tests are included by default instead of individually allowlisted.
- Documented installation, editing tools, preferences, testing, and packaging.

### Fixed
- Pitch and speed changes clear stale queued audio; unchanged controls no longer
  restart pitch processing, and output queues stay bounded when a device stalls.
- Processed playback respects volume changes without also playing native audio.
- Karaoke waveform end markers consistently use seconds and chronological lyric
  boundaries; linked drags support implicit ends and reject invalid intervals.
- Re-separation preserves merged timing spans, trailing pauses, structural
  newlines, and complete Unicode graphemes.
- Authored Ruby edits replace generated readings and persist on save.
- Marking a single karaoke segment retains explicit karaoke status; resetting
  karaoke enables Save and preserves readings.
- Overlap warnings use actual table rows and support simultaneous visual rows.
- Close, Escape, and the title-bar button consistently protect unsaved edits.
- Editing without the karaoke plugin no longer references missing controls.
- Source reloads validate timed input before replacing the model, apply offset
  and generation mode, and preserve artist/title/custom metadata.
- Editing a multiline lyric timestamp no longer flattens its text.
- Karaoke shortcuts respect text cells and keyboard modifiers; duplicate event
  handlers no longer override each other.
- Typing spaces in lyric text no longer triggers the playback shortcut.
- Saving an unchanged TXT document does not rewrite it as timestamped text.
- Invalid karaoke timestamps restore the previous display without changing the
  document or history; single-segment timing retains karaoke status.
- Undo/Redo correctly track the last saved document and reading mode.
- Mark Playback Time advances to the correct source line in a sorted sidebar.
- Undo no longer skips the immediately preceding edit.
- Segment edits are captured before mutation for Undo.
- Restored parsing of manually edited karaoke timestamps and instant-fill controls.
- LRCX reloads preserve a final segment end independently of a later line end,
  retaining trailing pauses after a shifted document is saved.
- Failed lyric saves leave the editor dirty and recovery data intact.
- Regression tests use the current furigana and scrolling widget modules.
