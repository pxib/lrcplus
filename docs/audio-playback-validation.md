# Audio playback validation

## Gapless playback and ReplayGain

The optional gapless transport decodes the active track and one successor with
FFmpeg, using pipe backpressure and a 256 KiB PCM queue per decoder. A single
floating-point output sink stays open across automatic track changes. Successor
samples enter the device buffer before the boundary; track/lyric state advances
when the sink's processed-sample clock reaches that boundary. The device buffer
is requested at 250 ms. Track positions are broadcast at most every 40 ms.
Queue edits discard the successor; an edit after successor samples were submitted
restarts at the current audible position to avoid playing removed audio.

Gapless playback defaults off, requires FFmpeg, and applies on the next track
load. Speed or pitch processing and unsupported output formats retain Qt native
playback. Device replacement also returns to Qt for the current track. The fixed
device format can resample input; this feature does not provide bit-perfect
output or remove silence encoded into a track. FFmpeg decoder processes have
additional working memory beyond the bounded PCM queues.

ReplayGain reads Vorbis/APE-style tags, ID3 TXXX frames, and MP4 iTunes freeform
tags using the existing Mutagen dependency. Album gain uses album peak, falling
back to the track gain/peak pair if album gain is absent. Missing or invalid gain
tags mean unity gain, including when a preamp is set. Optional peak limiting
caps the ReplayGain multiplier before manual volume. It is not a true-peak
limiter, and manual volume boosts can still clip. On native Qt playback tag
reads run asynchronously, so gain can take effect shortly after playback starts.

Automated checks verify byte-for-byte concatenation without sink replacement,
audible-boundary UI timing, partial writes, per-track gains, pause/resume/seek,
queue edits, shuffle, priority entries, duplicate occurrences, repeat modes,
tag format parsing, bounded real FFmpeg decoding, and process cancellation.

Real hardware listening remains necessary. Play two halves of a continuous
lossless recording and a gapless-tagged MP3/AAC album. Check the boundary for
silence, repeated or missing samples, and lyric timing. Repeat with ReplayGain,
mixed sample rates, a paused/seeked track, output-device replacement, and queue
edits near the boundary. Verify final-track tails finish before playback stops.
Compare track/album modes using tagged and untagged files at a comfortable level.

Rate conversion uses a per-stream windowed-sinc filter, with a fractional
phase shared across decoder callbacks. The cutoff is 94% of the lower Nyquist
frequency. A Blackman window spans 32 sinc zero crossings on each side; the
filter grows when downsampling. The lookahead is 35–69 input frames over the
supported 0.25–2x range (under 1.6 ms at 44.1 kHz). Unity rate is byte-preserving.
End-of-stream flushes the remaining rate-converted frames once. Seeks, track
changes, format changes, and control changes requiring a restart discard history.

Pitch processing also drains its lookahead and partial input block once at EOF.
Automatic track changes wait until the processed sink's sample clock reaches
the end of the written audio. Manual stops, seeks and track changes cancel that
wait and invalidate queued completion notifications. Pedalboard fallback builds
are checked once for working short-block streaming; unsupported builds report
an audio-processing error and retain native playback instead of silent output.

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
6. With boosted volume, pitch processing, and gapless playback, pause a track
   and close the app. Confirm that buffered audio does not resume during exit.
   Repeat while playing and while a library scan is running. On Windows, run
   the optional native reset check by setting `LRCPLUS_TEST_AUDIO_DEVICE` to
   the output device description and running `pytest tests/test_audio_sink.py`.

If a handoff remains audible, capture the output around the switch and measure
the native and processed delays before adding a crossfade. A fade alone cannot
align streams that are playing different points in the track.
