# Memory investigation

The subsequent [dialog memory audit](dialog-memory-audit.md) covers the Recents
font allocation, repeated dialog disposal, worker lifetime, and hidden editor
waveform decoding. Its native Recents probe reduced first-open working-set
growth from about 70 MiB to 10 MiB while preserving mixed-script filenames.
The Flashcards follow-up reduced its measured UI growth after word analysis
from approximately 74 MiB to 6 MiB, with analysis memory measured separately.

The reported measurements were approximately 150 MB at startup, 165 MB playing
audio, 173 MB in Lyrics, 192 MB in the editor, 198 MB in Translation, and 250 MB
after generating a share card. The editor and Translation also appeared to grow
by 200–500 KB every three seconds.

## Changes

- The editor now imports and constructs the image-card workspace on first use.
  Opening the editor, sharing text, or closing the editor does not allocate its
  font picker and card controls.
- Card previews paint the existing QImage directly instead of converting the
  full-resolution image to a QPixmap and scaling it on every repaint.
- Closing a card or library-cover viewer schedules the dialog and its image
  scene for deletion. Previously Qt's parent ownership kept each viewer alive
  until its parent was destroyed. Its initial fit timer now belongs to the
  dialog, so deletion also cancels that callback.
- Card shutdown stops pending renders and releases the export/preview image.
- Shadow rendering shares its source pixel buffer with Pillow and avoids an
  extra copy of the resulting shadow image. Export dimensions are unchanged.

## Library and artwork follow-up

Library rescans now support cancellation, coalesce newer requests, and delete
completed worker objects. Closing the player waits asynchronously for an active
scanner to finish. Temporary library and queue context menus are also deleted
after dismissal.

Column resizing debounces layout writes for 300 ms and saves only the layout
to `<settings stem>.library-columns.json` beside the active settings file.
Existing embedded column preferences remain the fallback until the first save;
copy the layout file too when moving preferences between installations.
These writes do not reserialize the track cache. Discord artwork resolution
uses one worker with one replaceable pending request instead of a thread per
track.

## Measurements and limits

In a controlled offscreen editor check on Windows/Python 3.14, opening the editor
with karaoke controls used about 143 MiB of working set before these changes
and 135 MiB afterward, before opening the card workspace. These are development
process measurements with tracing enabled, not a forecast for the packaged app.
Lazy loading moves the cost of the card controls to their first use.

An accelerated check exercised 18,000 playback-position/display updates. Native
private memory and Python allocations stabilized after warm-up. A repeat without
forced garbage collection also stabilized. The check used a standalone editor,
stubbed reading generation, and no active audio backend or translation provider.
It does **not** establish the cause of the reported recurring growth, nor does it
reproduce the complete startup-to-250-MB workflow. Native Qt, audio, fonts, and
plugins need measurements from the actual workload.

## Library startup follow-up

Import profiling identified a larger baseline cost: importing `lyrics.furigana`
also imported the optional `pypinyin` package and its full Mandarin pronunciation
dictionaries. In the development environment this added about 49 MiB of working
set before any song was loaded. The import now happens in a disposable worker
only when pinyin is requested for Han characters. Empty text, Latin lyrics, and
kana do not start a worker. Missing pypinyin still preserves the original text; tone marks and
phrase-sensitive Chinese readings are unchanged when it is available.

The header also now retains a logo decoded at its displayed height of 32 pixels,
instead of keeping the original 1098 × 328 image as well as its small display copy.

An untraced startup probe in fresh Python processes, using an empty isolated
library, the available default plugins, and the offscreen Qt platform, measured:

| Stage | Before | After |
| --- | ---: | ---: |
| Main module imported | 132.2 MiB | 83.8 MiB |
| Library window shown | 166.9 MiB | 116.8 MiB |

A second run measured 116.9 MiB at the library. These are working-set measurements
for the development app; packaged builds, installed plugins, and larger libraries
will differ. The saving is approximately 50 MiB in this setup. Requesting Mandarin
readings later loads the dictionaries in a child process that exits afterward;
only the small generated reading results stay cached in the player.
The remaining baseline includes Python, Qt Widgets/Multimedia, and the library
UI; audio processing dependencies are deferred further as described below.
Deferring the settings/editor imports alone measured about 1 MiB each,
so the dictionary import was the material startup improvement.

The follow-up passed 128 focused startup, library, reading, and branding tests.
A fresh-process regression exercises plugin loading and showing the library while
asserting that no pypinyin import is attempted. Reading tests cover the first
request, tone marks, polyphonic phrases, non-Han text, and the missing dependency.

## Disposable pinyin generation

`lyrics/pinyin_client.py` sends complete Han runs to `core/pinyin_worker.py`
through temporary UTF-8 JSON files. The source worker runs with isolated Python;
packaged releases invoke their own executable with `--lrcplus-pinyin-worker`.
That flag dispatches before GUI/audio imports. Bundled packages retain precedence
over optional dependencies from the running app's active dependency environment.
No separate Python installation is needed for the packaged worker.

Whole-song prefetch and the editor's Generate All action collect pinyin requests
into one worker call, preserving whole-phrase pronunciations and deduplicating
repeated Han runs. Translation rows are excluded. Concurrent requests share one
worker lock so multiple dictionary processes do not accumulate. Each process
exits before its results are returned; there is no idle dictionary process.

Timeouts, crashes, invalid responses, or unavailable pypinyin fall back to the
original lyric text. Workers have a 30-second timeout, and temporary files are
cleaned up on success and failure. Prefetch cancellation prevents publishing
stale batch results; an already-running worker may finish or reach its timeout.

A fresh, lightweight parent process generated and cached 100 mixed Mandarin
lines in 0.330 seconds. Its working set went from 32.40 to 33.44 MiB and private
memory from 25.48 to 25.50 MiB. The parent never imported `pypinyin`; a cached
repeat took 0.0002 seconds. These development measurements are not full GUI
memory totals or latency guarantees. The dictionary cost still exists while
the worker is alive, and repeated uncached generations pay process startup again.

Tests exercise real subprocess conversion, parent-process import isolation,
phrase/tone preservation, batching, editor generation, translation exclusion,
failure cleanup, cancellation, serialization, and early executable dispatch.
All 225 focused reading, editor, dependency, and startup tests passed.
The frozen command and runtime activation are covered by tests; a new packaged
executable has not been built as part of this change.

## Audio processing imports

The audio controller now uses standard-library scalar math for pitch controls.
NumPy is imported only inside gain, rate, and pitch processing methods; creating
or resetting idle processors does not allocate NumPy arrays. The pitch engine
factory also loads on demand. The PCM resampling and pitch algorithms remain
the same, and neutral pitch/unity-rate paths continue to preserve the input bytes.

With the same empty isolated library and available default plugins on Windows,
working set after showing the library fell from 117.13 to 104.55 MiB, a saving
of 12.58 MiB beyond the preceding changes. A repeat measured 105.10 MiB.
Native private committed bytes also fell from 456.15 to 92.87 MiB in this setup;
that counter is distinct from resident working-set memory.

Fresh-process checks verify that library startup, neutral audio controls, and
idle audio-thread resets do not import NumPy or the pitch engine. They also
exercise the first actual processed buffer, correct gain/resampling output,
and recovery if a deferred DSP import fails. All 136 focused audio/startup
checks passed, including the existing rate, pitch, gain, and threaded playback
regressions. No new packaged build or hardware listening check was performed.

When processing needs NumPy, its import cost is paid once and it remains loaded
for subsequent audio buffers. Enabled plugins that use NumPy independently can
still load it at startup; the disabled-by-default Karaoke Screen plugin is one
such consumer.

## Networking, library rows, and hidden renderers

The HTTP client stack is now deferred across reading generation, lyric providers,
artwork, and Discord RPC. `core/http_client.py` forwards calls, exception classes,
and attribute patches to the standard `requests` module, importing it on first
use. Importing an HTTP feature's module alone does not load the transport stack.
The actual requests/session behavior and process-wide test patches are shared.

Library rows now derive cell text, numeric sort values, paths, and size hints
directly from the existing LibraryTrack objects. Formatted per-cell strings,
duplicated numeric values, cached search haystacks, and a second per-track row
index are no longer retained. The parsed startup dictionary is temporary rather
than being kept alongside the track model. Lyrics matches are shared centrally
and notify the tree model when updated. Numeric sorting, group expansion,
selection, lyric search, and visible-only cover loading retain their existing
tree interactions. There is still one native tree item per track; this is an
on-demand data improvement, not a completely virtual tree replacement.

The two main lyric renderers are created only when the lyrics workspace, loaded
lyrics, or a plugin needs them. Creation applies the latest settings and shares
manual overrides. Karaoke attaches its visibility filters after creation instead
of forcing allocation at app-ready time. Closing an idle library and entering
the minimal player do not construct the full renderers unnecessarily.

In fresh Windows development processes with the available default plugins,
isolated settings, and the offscreen Qt platform, the same probe measured:

| Working set | Before this follow-up | After |
| --- | ---: | ---: |
| Empty library | 105.45 MiB | 94.86 MiB |
| 1,000 synthetic tracks | 108.23 MiB | 96.52 MiB |
| 10,000 synthetic tracks | 136.86 MiB | 110.43 MiB |

The 10,000-row population call measured approximately 0.212 seconds before and
0.046 seconds after in this probe. These measurements used synthetic Latin
metadata without covers and are not packaged-app or real-library guarantees.
The renderer construction probe included shared Qt/font allocations, so its
earlier 1–2 MiB observation must not be added to the measured combined savings.

Checks cover fresh-process import/renderer isolation, deferred first use,
transport patch compatibility, latest settings on first display, shared metadata
updates, numeric and lyric sorting, selection, and late karaoke attachment.
Existing UI checks also cover Japanese lyric highlights and the minimal player.
The full suite passed 2,902 tests with 3 skipped; the focused lifecycle, shared
metadata, transport, and sorting regression file also passed all 7 checks.
The packaged executable still needs a build and measurements on a real library.

## Stable playback footprint

The subsequently reported 160 MB playback measurement settles rather than
continuing to climb. Startup and steady playback are different measurements:
deferred networking, renderers, and DSP can load on first use, while decoding,
fonts, artwork, and plugins add their own runtime allocations. A plateau alone
does not identify the owner of those allocations or establish a leak.

Two avoidable playback costs were verified and changed:

- The main lyrics background now defers blur/cache construction while hidden
  or disabled and releases the processed background when hidden. The decoded
  source remains available for fullscreen karaoke. Returning to Lyrics renders
  the latest artwork/settings, and identical render requests reuse the cache.
- The main waveform defers its decoder until visible, so a disabled waveform
  does not launch ffmpeg. Showing it later starts the pending track without
  resetting the authoritative playback position. Hiding an active decoder
  cancels it without waiting on the GUI thread.

Pillow blur now shares the input QImage buffer and avoids redundant output
copies, preserving the same blur operation and rendered pixels.

A muted MP3 check with embedded artwork, neutral pitch/rate, no online lookups,
and stubbed reading generation stabilized at approximately 104.12 MiB before
and 102.16 MiB after these changes while Library remained visible. Opening the
lyrics page still constructs its artwork cache as needed. The user's saved
lyric font was unavailable in this environment, so this check does not reproduce
or explain the complete 160 MB observation.

The profiler now records playback state, loaded requests/NumPy/pitch modules,
renderer activation, mounted lyric rows, artwork source/cache dimensions,
waveform activity, window dimensions, selected lyric font, and pitch/rate/volume
settings alongside the memory samples.
It reads existing objects without instantiating deferred components. Capture
the actual playback workload with the command below to attribute the remaining
footprint rather than inferring it from the startup total.
All 109 focused playback, waveform, artwork, workspace, and renderer checks
passed, along with a runtime-profiler smoke check. The complete 160 MB source-run
plateau remains unconfirmed without a capture from that workload.

Regression checks cover lazy creation, avoiding creation during close, repeated
viewer disposal, and releasing card buffers on shutdown. Existing card-rendering,
karaoke, and translation tests cover output and editor behavior.

## Word-highlighting plugin

The user isolated the playback increase to the Karaoke plugin (word
highlighting). Its frame timer called `get_karaoke_settings()` on every sweep
frame, reading and parsing the entire settings JSON, including library data,
60 times per second at the default frame rate.

The plugin now reads its small karaoke settings snapshot when loaded and when
`karaoke_settings_changed` is emitted. Frame updates only advance the lyric
clock; renderer configuration is applied on settings and visibility changes.
Hidden renderers receive current settings when shown, including while paused.
Disabling the plugin also disconnects its media-player callbacks.

A controlled 1,000-frame timer benchmark with a synthetic 1,000-track settings
file and stubbed displays showed:

| Measurement | Before | After |
| --- | ---: | ---: |
| Settings reads during frames | 1,000 | 0 |
| Peak traced Python allocations | 739.1 KiB | 0.5 KiB |
| Frame-loop time with tracing | 7.728 s | 0.016 s |
| Working-set increase within the run | 2.33 MiB | 0.08 MiB |

These timings measure the isolated loop with tracing, not real playback FPS.
A separate real FuriganaWidget rendering check advanced 3,600 frames: its working
set rose about 0.10 MiB during the first 600 frames and remained stable afterward.
These checks establish removed allocation churn but do not establish that this
change accounts for the complete reported 160 MB playback footprint. Retest the
actual song in a fresh process with word highlighting enabled.

All 127 focused visibility, settings, animation, ruby rendering, deferred
renderer, and plugin-discovery checks passed.

## Native rendering follow-up

The screenshots after the settings-loop fix showed 107.3 MB for an English LRC
and 160.3 MB for Japanese LRCX with readings and Chinese translations. The user
confirmed that the first song had no karaoke and the second had karaoke. This
comparison changes both the song content and highlighting workload.

The offscreen Qt backend used by earlier probes exposed **zero font families**
in this environment. Its timing/settings tests remain useful, but its rendering
memory results do not include the real Windows text-rendering workload. They
must not be treated as validation of native glyph rendering or its memory cost.

Follow-up checks used the Windows Qt backend. The selected GenSenRounded2 TC
font file was present but not registered in this test environment; explicitly
registering it for a probe added about 32.4 MiB of private committed memory.
That registration was test setup, not a change to the application. Local MeCab
song prefetch increased working set by roughly 10 MiB; it did not account for
the reported 53 MB difference by itself.

Fresh native-window runs used the same Japanese MP3/LRCX, muted neutral playback,
embedded artwork, isolated settings, and word highlighting enabled or disabled.
Lyric paint-event counts confirmed actual painting. The original local file has
no translations, so a second pair added synthetic Chinese translations and used
the system's font fallback for the unavailable selected font.

| 20-second workload | Private-byte increase since startup, karaoke on | Karaoke off |
| --- | ---: | ---: |
| Original Japanese file, explicitly registered GenSen font | 21.69 MiB | 21.40 MiB |
| Synthetic Chinese translations, font fallback | 53.31 MiB | 52.97 MiB |

These are private committed bytes, not Task Manager's resident memory column.
The native tests reproduced substantial workload-dependent allocations but did
not reproduce a 53 MB difference caused by enabling highlighting alone. A
controlled comparison of the actual translated file and font environment is
still needed; no additional application memory reduction is claimed here.

The profiler now records Qt platform, loaded plugins, karaoke timer state and
interval, timed/translated line counts, reading mode/parser, local tagger state,
and reading-cache entry count. Its comparison flag disables word highlighting
for that run without changing saved plugin preferences. All 37 focused profiler,
playback-memory, karaoke-visibility, and deferred-renderer checks passed.

## Multiline lyrics and hidden Floating Lyrics

The user measured the same Japanese song at approximately 157 MB with word
highlighting and 150 MB without it. The actual profiler captures show the major
early allocation jump in both runs, followed by a plateau. In the karaoke-on
capture, working set rose from 176.69 to 283.32 MiB at roughly 3–6 seconds of
playback; with karaoke off, it rose from 175.80 to 280.24 MiB at roughly 2–5
seconds. These counters differ from Task Manager's resident-memory reading.

The user then isolated visual breaks (`{\n}` and `{\nt}`). The recent-song entry
identified the actual file as the translated **Best Friend Remix** LRCX: 90
timed lines, 85 with translations. Earlier local checks used the original LRCX,
which contains no translations.

The Floating Lyrics plugin was receiving position updates and setting its text
even when its window was hidden and its saved `enabled` setting was false.
Multiline/translation layout in that secondary renderer loaded native fallback
font/layout resources despite the feature not being visible. An isolated
Windows check with the main GenSen font already loaded measured about 68.2 MiB
of additional working set and 34.8 MiB of private commitment when the hidden
window received the first translated line. Subsequent lines reused those
resources. Skipping the hidden updates removed that allocation in the probe.

The plugin now skips lyric processing while hidden. Its visibility callback
refreshes the current lyric and ruby settings immediately when shown, including
when playback is paused. Both visual-break types remain supported.

A full-player native Windows reproduction used the same Japanese MP3, translated
remix LRCX, explicitly registered GenSen font, collapsed lyrics list, embedded
artwork, muted neutral pitch/rate, and both Karaoke and Floating Lyrics plugins.
All settings were isolated from the user's preferences:

| Working set | Before | After |
| --- | ---: | ---: |
| Startup | 146.78 MiB | 146.66 MiB |
| Playback at 3 seconds | 175.66 MiB | 175.51 MiB |
| Playback at 5 seconds | 243.59 MiB | 175.52 MiB |
| Playback at 8 seconds | 245.91 MiB | 177.95 MiB |

Private committed bytes at 8 seconds fell from 182.74 to 147.94 MiB in that
controlled run. Savings depend on whether other visible components have already
loaded the same fallback fonts; an expanded lyrics list can load them earlier.
These are development-process measurements, not a promised Task Manager total.
Restart the app when retesting so fonts loaded by the old hidden path are not
already cached in the process.

All 48 focused floating-window, profiler, playback-memory, karaoke-visibility,
deferred-renderer, and newline-layout checks passed. The two new hidden-window
cases also passed separately with the native Windows Qt backend.

## Idle startup and closed image cards

Settings, the lyrics editor, and translation panels now import on first use.
The Floating Lyrics plugin registers its actions and settings without creating
a hidden window or renderer. Its first display applies the latest settings,
restores the saved position, and immediately shows the current lyric. Unloading
the plugin deletes its window and cancels its owned layout timer.

The image-card renderer's global caches previously retained two full-resolution
backgrounds and the watermark after the editor closed. Card shutdown now clears
those caches, the rendered preview, and the album-art loader's image/provider
references. Other open previews retain their own images and rebuild the shared
caches on their next render.

Fresh Windows/Python 3.14 processes using the native Windows Qt platform and
isolated settings measured the following working sets:

| Workload | Before | After |
| --- | ---: | ---: |
| Empty-library player shown, available default plugins | 127.18 MiB | 125.18 MiB |
| Standalone editor disposed after two image-background renders | 153.84 MiB | 90.69 MiB |

The second probe rendered a 1920 × 1080 card at 2× quality twice, changing image
zoom to populate both cached 3840 × 2160 backgrounds. After editor disposal,
private committed memory also fell from 123.98 to 60.79 MiB. A repeat measured
90.73 MiB of working set after disposal. The approximately 63 MiB saving applies
to this card workflow; it is separate from the approximately 2 MiB startup
saving. Card size, quality, prior font use, plugins, and library content affect
the totals. These are development-process measurements, not packaged-app totals.

Regression checks cover fresh-process import isolation, disabled/saved-enabled
floating-window startup, first-use settings and lyrics, pause/resume, disposal
before queued callbacks, releasing cached images, and preserving another open
card's pixels after shared-cache eviction.
The two focused suites passed 1,358 tests; the four new floating-window lifecycle
checks also passed separately with the native Windows Qt backend.

## Capture the actual workload

From the repository root, launch the development app with:

```powershell
.\Scripts\python.exe tools/profile_memory.py --output memory.jsonl
```

Repeat the reported sequence. Stay in the editor and Translation for at least
60 seconds each; generate cards, change styles, and open/close several viewers.
Close the player normally to finish the recording. Samples include elapsed time,
active window/tab, working-set bytes, and native private bytes every three seconds.
The launcher runs the normal app and uses its normal settings and plugins.

To isolate word highlighting, play the **same Japanese song and lyrics file**
for at least 30 seconds in each fresh run, using the same window size and
settings. Close the app between runs:

```powershell
.\Scripts\python.exe tools/profile_memory.py --output karaoke-on.jsonl
.\Scripts\python.exe tools/profile_memory.py --disable-karaoke --output karaoke-off.jsonl
```

For Python allocation details, repeat with tracing:

```powershell
.\Scripts\python.exe tools/profile_memory.py --trace --output memory-traced.jsonl
```

Tracing adds overhead, so compare growth within a run rather than absolute memory
against an untraced process. The additional `memory-traced.allocations.txt` file
lists Python allocation growth since player construction. Stable Python usage
with increasing native private memory points toward native allocations; rising
Python usage provides source locations to inspect. Working-set changes alone can
also reflect Windows paging or allocator reuse. The profiler neither forces
garbage collection nor trims the working set.
