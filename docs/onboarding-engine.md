# Onboarding dialogue

Edit `assets/onboarding/onboarding.rpy`. The script uses a small Ren'Py-inspired
language. It never executes Python.

## Setup steps

The wizard visits Welcome, Music library, Appearance, Reading preferences,
Lyrics, and Ready. `STEPS` in `ui/onboarding.py` defines the order and the Debug
step selector; the reading introduction uses the `readingpreferences` label.

Reading preferences previews lyric font size, furigana, romaji readings, and
replacing the lyric with romaji. Its sample uses the player's lyric renderer
with supplied readings, without a song, a dictionary, or a network request.
Changes remain a draft until **Apply and Next** saves them as global lyric
preferences and refreshes the player. Unedited preferences and song-specific
overrides are preserved. **Do this later** skips the step without saving the
draft, including during its introduction; returning to the step reloads the
current saved preferences.

Closing the wizard, pressing Escape, or choosing **Skip setup** asks whether to
leave. **Keep setting up** is the default and Escape action. The confirmation
pauses dialogue timers, scripted motion, and queued actions, and buffers incoming
application events. Keeping setup resumes the current scene; leaving retains
saved preferences and discards any unapplied reading preview. **Finish** completes
setup directly, and **Do this later** skips only the reading step without an exit
confirmation.

## Reading and pauses

The lyrics farewell always offers a short goodbye or the optional DDLC route
through **You'll always be my dearest friend.** The detour has an early exit,
a developer confrontation with two responses and another exit, and a closing
poem. `ddlc_defended` selects the closing response for the current session.
The `ddlc_*` helpers return to the farewell after closing scene windows,
stopping scene audio, and restoring the guide's position. Retained developer
recordings keep their authored word pauses; terminal dialogue is display only.

The wizard's **Auto-read** checkbox starts enabled and is remembered alongside
text speed and reduced motion when setup closes. Turning it off stops automatic
dialogue advancement; clicking the text, pressing Space, or using the arrow still
advances dialogue. Turning it on while a line is ready starts a fresh reading delay.

- `$ auto(3)` waits three seconds after the preceding text finishes, then advances
  if Auto-read is enabled. Its arrow stays visible for manual advancement.
  Fractional seconds, such as `$ auto(0.4)`, are supported too.
- `$ next` ends the current line. Auto-read uses a delay based on the displayed
  text length (1.2–10 seconds); otherwise the line waits for manual advancement.
  Lines without a final command use the same behavior.
- `$ pause(2)` always pauses the script for two seconds. The arrow disappears
  during the pause and returns when it expires, unless the continuation presents
  a choice or ends dialogue. Auto-read does not bypass or disable this pause.
- Choices and `wait_event(...)` continue to wait for the required interaction.
  `$ end` returns control to the setup page without automatically changing pages.

Instant text changes only text rendering. Scripted pauses, actions, choices, and
event waits keep their execution order. Space operates a focused pacing control
instead of advancing dialogue.

## Reaction sounds

Put local audio files in `assets/onboarding/sound`. Sound cues are nonblocking
and use their own audio player, leaving music playback and its volume alone.
They support the supplied OGG and MP3 files, plus WAV files.

```renpy
label reaction:
    before sound("playful")
    wizard "I have an idea."
    $ pause(1)
    $ sound("idea.ogg")
    extend " There it is."
    $ next
    play sound "interesting.ogg"
    wizard "Hey!"
    stop sound
    $ end
```

`before sound("name")` starts a reaction before the following `wizard` line or
`extend` continuation types. Before an `extend`, the cue runs after preceding
inline actions such as `pause(...)`, immediately before the appended text.
`$ sound("name")` starts it when the script reaches that action. A name can be
a filename or its stem, such as `sigh.ogg` or `sigh`. `play sound "file.ogg"`
is the Ren'Py-style action form; `$ stop_sound` and `stop sound` stop playback.
Names refer only to files directly in the sound folder, not paths or URLs.

Only one reaction plays at a time. Another sound replaces it; changing scenes,
skipping dialogue in Debug, muting, or closing setup stops the active clip.
Missing files or playback errors are logged without blocking the dialogue.
The **Sound** checkbox mutes reactions immediately and is remembered when setup
closes. Unmuting does not replay an earlier cue. Reduce motion and Auto-read
remain independent of sound.

A small text expression appears beside the speaker's name when a sound cue runs,
such as **Thinking...**, **Confused**, or **Playful**. It remains for that dialogue
line, including pauses, and clears when the next line has no reaction cue.
Another cue in an `extend` continuation updates it at that point. The expression
still appears when Sound is muted; explicit `stop sound` clears it. Scene changes
and returning from a helper also clear stale expressions.

The bundled names are `adore`, `confused`, `idea`, `interested`,
`interesting`, `love`, `offended`, `playful`, `responsereceived`, `sad`, `shy`,
`sigh`, `sleeping`, `speechless`, `steaming`, `surprised`, `sweating`, and
`thinking`. The tour uses selected reactions rather than playing a clip for
every line.

## Variables and flags

Variables belong to one wizard session. They survive route changes, setup page
changes, and checkpoint jumps, but reset when a new wizard opens. Checkpoints do
not roll variable values back. Values can be strings, finite numbers, booleans,
or `null` (`None` also works). Names are case-sensitive; `event` is read-only and
refers to the last consumed application event.

Declare defaults before the first label. Defaults initialize missing values when
a label runs and do not overwrite existing values:

```renpy
default tried_search = false
default retries = 0
default source = "online"

label offer_search:
    wizard "Want to search for lyrics?"
    menu:
        "Search":
            jump search
        "Skip":
            jump skipped

label search:
    $ tried_search = true
    $ retries += 1
    wizard "Try a lyrics search."
    $ wait_event(lyrics_search_results,lyrics_search_failed)
    if event == "lyrics_search_failed":
        jump failed
    wizard "Found some lyrics!"
    $ end

label failed:
    wizard "The search failed."
    if retries >= 2:
        wizard "You can try again later."
    $ end

label skipped:
    wizard "You can search later."
    $ end
```

Assignments also support `$ set(source, "local")`, `$ default(retries, 0)`,
`$ toggle(tried_search)`, and numeric `+=` / `-=`. A missing counter starts at
zero; a missing flag is false. State commands can precede the first spoken line
of a label, or use `before set(...)` to run before the following spoken line.

Conditions support `if flag:`, `if not flag:`, and comparisons with a literal
using `==`, `!=`, `<`, `<=`, `>`, or `>=`. `elif` and `else` select exactly one
branch. Bodies can contain multiple statements, nested conditions, menus, calls,
and the existing dialogue/action commands. Use four spaces for each indentation
level. Comments and blank lines may appear inside blocks.

```renpy
label search_summary:
    if lyrics_found:
        wizard "Found some lyrics!"
        $ auto(3)
    elif retries >= 2:
        wizard "We've tried a few times."
        call search_advice
    else:
        wizard "You can try searching later."
    wizard "Let's continue setup."
    $ end
```

The first matching branch is chosen once; changing a variable in that branch
does not cause a later `elif` to run. There are no arbitrary Python expressions
or function evaluations. Unlike-type ordering comparisons are false; booleans
are distinct from numbers. The Debug panel displays current variables.

## Random dialogue

`random:` picks one unused variant each time execution reaches the block. Each
block has its own session bag: all variants run once before the bag refills.
Each remaining entry has an equal chance. A refill avoids the previous bag's
last entry when a distinct alternative exists. Bags survive route and setup-page changes and reset when a
new wizard opens. The selected
dialogue follows the usual text speed and Auto-read settings, then execution
continues after the block. The player does not need to make a choice.

For a list of individual lines, use `wizard` statements or quoted strings:

```renpy
label greeting:
    before sound("playful")
    random:
        wizard "Hey! Ready for the tour?"
        wizard "Welcome back. I've been practicing my introduction."
        "There you are. Let's get started."
    $ auto(3)
    wizard "First up: your music library."
    $ end
```

The `before` cue applies to the selected variant's first line. Common inline
actions after the block, such as `$ auto(3)` or `$ pause(1)`, apply to its last
line. A common `extend` appends text to that selected line as well.

Use `choice:` for a variant with several lines or its own actions:

```renpy
label reaction:
    random:
        choice:
            before sound("playful")
            wizard "Aha. There you are."
            $ pause(0.5)
            extend " Ready?"
        choice:
            before sound("thinking")
            wizard "Where were we?"
            $ auto(2)
            wizard "Right. Music."
    wizard "Let's continue."
    $ end
```

Only the selected variant runs its actions or changes variables. A choice body
can include conditions, calls, event waits, or another `random:` block. Keep
variants indented by four spaces and their `choice:` bodies by another four.
An empty random block or choice is reported with its script line number.

## Reusable conversations

`call label` visits another label and saves the exact continuation. `return`
resumes the caller, including remaining actions and inline text. Calls may nest;
they share variables, and pauses, animations, choices, and event waits continue
to work inside the called label.

```renpy
label folder_step:
    wizard "Want some folder advice?"
    menu:
        "Folder tips":
            call folder_advice
            $ library_tips_read = true
        "Skip":
            $ library_tips_read = false
    wizard "Now choose your music folders."
    $ end

label folder_advice:
    wizard "A small folder is a good place to start."
    wizard "You can add more folders later."
    return
```

Bare `call` and `return` run after the preceding spoken line is advanced. This
keeps a helper's final line readable before returning. The `$ call label` and
`$ return` forms are inline cues: they run immediately when typing reaches them,
like other dollar-prefixed actions. A helper that reaches its end also returns
to the caller. A top-level `return` finishes the current conversation.

`jump label` transfers control without adding a return address; it can be used
inside a helper and still return to the original caller. An explicit `$ end`, a
checkpoint return, or a setup-page change discards the call stack. Use checkpoints
for recovery that leaves a tutorial, and calls for detours that should resume it.
The Debug panel shows the current call depth. Calls are limited to 64 nested
frames; missing/empty targets or excess depth are logged and skipped.

Menu options can contain multiple statements, including calls and conditional
blocks. Selecting an option runs its body and then continues after the menu.
Existing single-`jump` menu options keep their original behavior.

## Conditional menu choices

Add `if condition` after a choice's quoted title to hide it when false. Use
`enabled_if condition` to keep a false choice visible but disabled. Conditions
use the same safe flags and scalar comparisons as dialogue branches, and are
evaluated when the menu opens. Reopening the menu evaluates them again.

```renpy
label search_options:
    wizard "What next?"
    menu:
        "Try again" if retries >= 1:
            call search
        "Use these lyrics" enabled_if lyrics_found:
            jump done
        "Later":
            return
```

Keep an unconditional exit available when practical. If no choice is enabled,
the engine clears the menu and continues after it so setup cannot get stuck.

## Interrupt-and-resume reactions

Register an event handler with `react(event_name, label)` before the dialogue
it should interrupt. Use `react(event_name, none)` to remove it. Registrations
last for the current setup page; a new page clears them.

```renpy
label introduction:
    before react(wizard_moved, moved)
    wizard "Let me show you around."
    $ pause(2)
    extend " First, your library."

label moved:
    before sound("surprised")
    wizard "Oh! New coordinates. Where was I?"
    return
```

A reaction saves the current line, typed character position, remaining actions,
call stack, event wait, expression, and remaining reading/pause time. `return`,
reaching the label's end, or `$ end` restores that continuation without replaying
its actions. Reaction variables remain changed. As with other inline commands,
`$ end` runs immediately; use bare `return` to keep the final reaction readable.
Reaction sounds replace current audio; interrupted clips are not replayed.

An explicit `wait_event` gets first claim on matching events. Other registered
events interrupt typing, reading delays, pauses, or event waits. Menus, scene
windows, and scripted movement defer reactions until ordinary dialogue resumes.
Incoming events stay buffered during exit confirmation and reactions; reactions
do not nest. Changing setup pages or closing setup discards the continuation.
The bundled welcome, library, and appearance introductions react to user moves.

Appearance also registers reactions for all built-in presets and the five basic
accent colors (Green, Blue, Purple, Rose, and Orange). Choosing a preset emits its `theme_` event
(for example, `theme_oled_black`) after the appearance has been applied. These
reactions also work after the introduction ends, while setup controls remain
usable, and restore the prior text and keyboard state when finished. Initial
preset restoration is silent. Consecutive selections of the same preset are
silent, and rapid browsing retains only the latest queued theme reaction.
