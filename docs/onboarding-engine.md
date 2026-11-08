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

## Reading and pauses

The wizard's **Auto-read** checkbox starts enabled and is remembered alongside
text speed and reduced motion when setup closes. Turning it off stops automatic
dialogue advancement; clicking the text, pressing Space, or using the arrow still
advances dialogue. Turning it on while a line is ready starts a fresh reading delay.

- `$ auto(3)` waits three seconds after the preceding text finishes, then advances
  if Auto-read is enabled. Its arrow stays visible for manual advancement.
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
