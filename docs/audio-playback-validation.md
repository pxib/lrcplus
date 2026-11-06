# Audio playback validation

Rate conversion uses a per-stream windowed-sinc filter, with a fractional
phase shared across decoder callbacks. The cutoff is 94% of the lower Nyquist
frequency. A Blackman window spans 32 sinc zero crossings on each side; the
filter grows when downsampling. The lookahead is 35–69 input frames over the
supported 0.25–2x range (under 1.6 ms at 44.1 kHz). Unity rate is byte-preserving.
End-of-stream flushes the remaining rate-converted frames once. Seeks, track
changes, format changes, and control changes requiring a restart discard history.

Automated checks cover callback-independent samples and duration, passband
accuracy, stereo phase, integer saturation, alias rejection, bounded history,
and end-of-stream delivery. Threaded playback checks cover rapid setting changes,
in-flight work, stale route notifications, pause/resume, device replacement,
and continued writes during GUI stalls. These checks cannot establish whether
a native/processed handoff sounds seamless: the two devices' latency and the
pitch processor's startup reserve still affect that handoff.

## Listening checks on real hardware

Use a comfortable listening level with speakers or headphones. Repeat with
44.1 kHz and 48 kHz tracks, including a sustained tone, percussion, and music
with vocals. Record the output device, backend, track format, and settings
around any audible problem.

1. Sweep speed through 25%, 73%, 100%, 125%, and 200%, with pitch linked and
   unlinked. Listen for alias tones, changes in level, and clicks at callbacks.
2. While playing, rapidly alternate linked/unlinked pitch and shifts of 0,
   +5, -5, +12, and -12 semitones. Return to normal playback several times.
   Listen for gaps, doubled transients, pitch warmup, and repeated old audio.
3. Repeat the changes while paused, resume, then seek forward and backward.
   Switch tracks during warmup and while a settings dialog is open. Confirm
   that no samples from the old track become audible.
4. Switch between native output at normal speed/pitch and processed output
   at the same volume. Include 80% and 100% volume and a boosted-volume route
   at a comfortable system volume. Listen for latency jumps or overlapping audio.
5. Test short clips and natural track endings at each speed, then repeat with
   a USB or Bluetooth output if available. Note truncated endings or silence
   after a route or device switch.

If a handoff remains audible, capture the output around the switch and measure
the native and processed delays before adding a crossfade. A fade alone cannot
align streams that are playing different points in the track.
