# Dialog memory audit

## Recents: reproduced and reduced

The reported change was approximately 78 MB to 127 MB on opening Recents.
Recents already scheduled its widget tree for deletion and used uniform row
heights. Its remaining first-open cost came from native font fallback for
filenames containing multiple scripts and emoji. For example, a Korean title
with Japanese text and a heart emoji cannot use one covering fallback font.
Choosing a font for the entire string still allowed large legacy fonts to load.

The filename lists now shape text with explicit fonts for each grapheme run,
sharing the coverage logic used by lyric search previews. Unsupported characters
still have automatic fallback. Japanese, Chinese, Korean, Vietnamese combining
accents, Arabic, and emoji retain their original text and glyphs. Long labels
are elided for display; item text, paths, selection, and loading are unchanged.
Each delegate keeps at most 128 layouts and bounded font/character caches.

This also applies to Queue filenames and the two folder lists in Settings.
Uniform sizing avoids measuring all hidden rows before they are needed.

A fresh native Windows probe warmed the player UI, opened the actual recent
filenames, painted the dialog, closed it, and processed deferred deletions.
The isolated library was empty; no audio, online lookups, tracing, forced GC,
or working-set trimming ran. Both processes used the same filenames:

| Working set | Before | After |
| --- | ---: | ---: |
| Before opening Recents | 125.01 MiB | 125.30 MiB |
| First painted dialog | 194.61 MiB | 134.89 MiB |
| After first dialog deletion | 194.07 MiB | 134.35 MiB |
| After third dialog deletion | 194.13 MiB | 134.70 MiB |

First-open working-set growth fell from 69.60 to 9.59 MiB. Private committed
growth fell from 37.25 to 6.35 MiB. No dialog children remained after each close.
These process counters are distinct from Task Manager's resident-memory column;
they establish a controlled improvement, not a promised final app total.
The remaining modern font caches can stay loaded after closing. Restart when
comparing against the old implementation's already-loaded legacy fonts.

## Flashcards follow-up: reproduced and reduced

The user subsequently reported another approximately 50 MB increase when
opening Flashcards. The ownership audit had fixed its close/worker lifetime,
but had not measured its text-rendering footprint. Native Windows measurements
separated real MeCab analysis from dialog construction and painting.

The lyric selector, rich-text labels, word table, and hidden all-lyrics list
still used native font fallback. The dialog now uses explicit covering fonts
for its text runs, retaining the main-text/inline-reading font sizes and escaping
source text as before. Its selector uses a bounded minimum content length rather
than sizing itself from every lyric. Its popup uses the shared delegate; Qt
versions that support delegated combo labels also use it for the closed label.
Older Qt versions retain normal fallback for the closed label to preserve glyphs.
The all-lyrics list is populated only when requested and reused on later toggles.
Navigation, word checkboxes, original contexts, and TSV export are unchanged.

The probe warmed the player UI with isolated preferences and used the same
90-line translated remix file. Real local word analysis ran first; the dialog's
analysis worker then received that result so the UI allocation could be measured
separately. No audio, online requests, tracing, forced GC, or trimming ran:

| Working set | Before | After |
| --- | ---: | ---: |
| Analysis completed, before opening dialog | 142.15 MiB | 141.75 MiB |
| Dialog constructed | 211.72 MiB | 143.98 MiB |
| Dialog painted | 216.02 MiB | 147.91 MiB |
| All Lyrics displayed | 216.07 MiB | 148.20 MiB |
| Dialog deleted | 213.84 MiB | 145.98 MiB |

UI growth after analysis fell from 73.87 to 6.16 MiB of working set and from
38.88 to 4.78 MiB of private commitment. A second opening reused the font caches
and returned near the first closed value. Cold MeCab analysis itself still added
approximately 16 MiB of working set in this workload; it is separate from the
removed UI cost. These are controlled process counters, not a promised final
Task Manager reading. Restart before comparing with the old font caches.

The focused regressions passed 36 tests with 6 offscreen/font skips. A separate
native Windows run passed 19 tests, including actual rich-text glyph coverage,
font-size preservation, HTML escaping, mouse-operated checkboxes, navigation,
deferred list population, and exported contexts. A rendered dialog was inspected.

## Dialog ownership and background-work review

The review covered active application/plugin dialog entry points, their close
paths, nested dialogs, image ownership, timers, worker shutdown, and lazy work.
Recents and Flashcards have per-dialog native memory measurements in this audit.
Other rows describe code inspection and lifecycle/behavior regression checks.

| Dialog or workspace | Finding and resulting behavior |
| --- | --- |
| Recent Songs | Deletes on return, cancellation, close, or exception. New mixed-script rendering avoids the measured legacy font cost. |
| Queue / Saved Playlists | Already disconnects queue signals and deletes on return. Filename list now uses the same bounded text renderer. |
| Settings / individual plugin settings | Already delete after modal execution. Folder lists now use the text renderer. Dependency inventory remains deferred until its page is visited; plugin settings factories are called when that plugin's settings are opened. |
| About / Credits | Credits already delete after execution. The game is created only when launched, hides with Settings, and belongs to Settings' lifetime. |
| Lyrics Editor | Existing close handling stops timers/decoders, disconnects media signals, and shuts down translation/card work. Both editor waveform decoders now wait until their actual panel is visible. |
| Share card / cover viewers | Card controls remain lazy. Viewer dialogs already delete their scenes/images after close; card shutdown releases its image and render timer. |
| Search Lyrics | Already cancels/closes background search/preview jobs and deletes the dialog. Its nested overwrite prompt now also schedules deletion. Preview fonts share the new grapheme resolver. |
| Pitch changer | Previously retained under the player after each close. Now deletes after execution, including exceptions. Pending slider changes flush before disposal. |
| Shift Timing / Validate Timing | Previously retained each dialog and its signal closures under the editor. Both now delete after execution, including exceptions. |
| Editor conversion / Clear Karaoke prompts | Explicit message boxes now schedule deletion after execution. Static Qt message helpers keep their existing Qt-managed lifetime. |
| Reading-parser / Cantonese prompts | Explicit message boxes now schedule deletion after execution. Decisions and checkbox state are read before the next event-loop pass. |
| Dependency notice / standalone Dependency Manager | Both now delete after execution. The manager continues to block normal closure while its dependency operation is running. |
| Update available | Previously cleared its Python pointer while leaving a parent-owned dialog. Now deletes on dismissal and clears only its own pointer. |
| Update download progress | Stays alive while its worker uses it, including when hidden. Worker completion now clears its pointer and deletes the progress window. |
| Audio-device recovery | Already closes/deletes the recovery dialog and clears its pointer; no additional change. |
| Anki Flashcards | Deletes after dismissal, waiting for an active analysis worker to finish. The native follow-up also reduces text-rendering allocations and defers the hidden all-lyrics list. |
| Karaoke Screen | Previously dropped its Python pointer while the player retained closed stages, artwork, and child timers. Now schedules stage deletion after stopping screen/audio timers; unload also covers a hidden stage. |
| Standalone Translation / translation quota | One standalone dialog is intentionally cached per plugin. Closing confirms drafts and shuts down local/cloud work. Editor provider panels are created on demand; the quota dialog is one reusable child. No new per-open memory claim. |
| URL audio import | One dialog is intentionally reused. Closing shuts down its subprocess job; completed jobs delete themselves. Its details log already has a 150-block cap and preview thumbnail is display-sized. |
| Native file, directory, input, and color pickers | Use Qt's static APIs; there is no application-held dialog tree to release. Native shell/font/codec caches can still affect first-use measurements. |
| Legacy `ui_tools` dialog exports | Compatibility constructors remain available to plugin callers. Main-player Settings and Editor use the implementations audited above. Caller ownership applies to external uses of the legacy constructors. |

Intentionally cached standalone Translation and URL-import windows retain one
window's UI state until plugin shutdown. This audit does not claim all dialog
memory returns to the startup level, or that every first-use font/DSP cost is a
leak. Additional memory totals for these workspaces require their actual content
and worker workload.

## Validation

Native Windows glyph checks passed for all tested scripts and emoji, including
scrolling to previously hidden rows and loading their original paths. A rendered
Recents image was visually checked. Repeated-open checks cover widget disposal,
pending pitch updates, dependency dialogs, update-worker progress lifetime,
flashcard analysis lifetime, and Karaoke Screen child timers.

The original dialog audit's full suite passed 2,932 tests with 6 skips. Follow-up targeted tests cover
the final editor waveform deferral and text-metric adjustments made during the
audit; native font checks are separate because the offscreen backend lacks the
installed Windows font database.
