# LRCPlus onboarding script specification

Edit `onboarding.rpy` to change the wizard's dialogue and routes. The wizard reads that file on each conversation. The older `dialogue.json` and `dialogueroute.json` are retained as backups; changes to them are only used if `onboarding.rpy` is absent.

This is a **Ren'Py-inspired subset**, not the Ren'Py engine: it does not run arbitrary Python, and other Ren'Py statements are not supported. Its `.rpy` syntax is interpreted by LRCPlus. Use four spaces for statements, eight for menu choices and `if` jumps, and twelve for menu jumps. Start comments with `#` on their own line.

## Labels and dialogue

The wizard starts the four setup steps at `firststeplanguage`, `secondstepmusiclibraryfolders`, `thirdstepappearance`, and `fourthsteplyrics`. Other labels are routes. Each label is unique.

```renpy
label firststeplanguage:
    wizard "Welcome to LRCPlus!"
    $ auto(3)
    wizard "Let's get started."
    $ end
```

`wizard` displays a line with the speaker name Setup Wizard. Text is typed gradually; Space or a click reveals the current stretch, then advances when the line waits for input. `extend` continues the current line after a cue, which lets effects run in the middle of a sentence:

```renpy
    wizard "Wait..."
    $ pause(2)
    extend " okay, let's go."
    $ end
```

Strings use double quotes; write `\"` for a quotation mark and `\\` for a literal backslash. The text itself is not Python code.

## Cues

A cue written as `$ ...` runs after the preceding stretch of text. Use `before ...` immediately before a `wizard` line to run it at the start of that line. Cues execute in order. `auto`, `next`, `end`, and `continue_setup` end their cue sequence and should be last on a line.

| Cue | Meaning |
| --- | --- |
| `$ next` | Wait for the user to advance. A dialogue line with no cue also waits. |
| `$ auto(3)` | Advance automatically after three whole seconds; the user can advance sooner. |
| `$ end` | End the conversation, leave the line visible, and enable setup controls. |
| `$ continue_setup` | Wait for the user, then return to the setup step. |
| `$ pause(2)` | Pause for two seconds before the next cue or text. Decimals are supported. |
| `$ idle(10)` | Wait until there has been no user activity for ten continuous seconds; mouse, keyboard, touch, scrolling and moving the wizard reset the wait. At the end of a line it advances automatically. |
| `$ move(100,200)` | Move the wizard relative to the main window's content area. |
| `$ move(75%,20%)` | Move relative to the main window's width and height; percentages and pixel offsets may be mixed. |
| `$ point_at(lyrics_button)` | Place the wizard below the LRC dropdown and align the first `^` in the current dialogue line with its arrow. An optional second argument selects movement easing. |
| `$ click_control(lyrics_button,menu_disrupted)` | Click the LRC menu control by name, or jump to `menu_disrupted` if it is unavailable. |
| `$ move(100,200,in_out_cubic)` | Move with `linear`, `out_cubic`, or `in_out_cubic` easing. Default is `out_cubic`. |
| `$ reset` or `$ reset(linear)` | Return to the wizard's starting position, optionally choosing an easing. |
| `$ click(300,80)` or `$ click(75%,20%)` | Move the fake cursor and click a point relative to the main window. |
| `$ hide_cursor` | Hide the fake cursor; it otherwise stays at its last position. |
| `$ toggle_outside_click(true)` / `$ toggle_outside_click(false)` | Explicitly allow or block clicks on the main window. The setting starts blocked and persists between steps and routes. The older `$ toggle_outside_click` without an argument still flips the state. |
| `$ watch_menu(lyrics_menu,menu_disrupted)` | Watch the Lyrics menu. If it closes unexpectedly, jump to `menu_disrupted`. |
| `$ click_menu(lyrics_menu,search_lyrics,menu_disrupted)` | Move the cursor to the Search Lyrics menu item and activate it. If the menu or item is unavailable, jump to `menu_disrupted`. |
| `before checkpoint(6)` | Mark the following line as checkpoint 6. |
| `checkpoint 6` | Mark the next dialogue line; this is a shorter alternative to `before checkpoint(6)`. |
| `$ return_checkpoint(6)` or `$ return checkpoint(6)` | Return to the line marked with checkpoint 6, including a later line or a marker in another label. |
| `$ wait_event(name)` | Wait for an event; several comma-separated event names may be listed. |
| `$ wait_event(wizard_moved,idle(10))` | Wait for a listed event or ten seconds of inactivity, whichever happens first. The inactivity outcome is `wizard_idle`. |

Clicks target enabled widgets in the main window or its open menus. They do not move the real mouse or click other applications. The wizard's Debug panel shows coordinates. Percentages scale with the main window, but the control at a position may change with the layout.

Scripted movement can place the wizard outside the screen. Percentages refer to the main window's content area, not the wizard's size. `$ reset` returns it to its starting position and keeps the reset destination on the available screen when possible.

## Menus and routes

```renpy
label fourthsteplyrics:
    wizard "Want to search online?"
    menu:
        "Yes":
            jump lyrics_online
        "No":
            jump skip_search
    before checkpoint(6)
    wizard "You can change lyrics settings later."
    $ end

label skip_search:
    wizard "No worries."
    $ pause(2)
    $ return_checkpoint(6)
```

Each choice must have a `jump` to a defined label. Menu text currently cannot contain commas or parentheses. Returning to a checkpoint replays its marked line. Returns first use a marker already visited in the current conversation, then search the current step and finally all script labels, including routes and other setup steps. Use unique checkpoint numbers to avoid ambiguity. Visited markers are cleared on the next setup step. Route labels are case-sensitive.

You can also use `jump lyrics_continue` as a standalone statement, at the same indentation as `wizard` and `$` statements. It runs after the preceding dialogue and cues and opens that label without a choice.

## Search events and conditions

Available events are `lyrics_search_started`, `lyrics_search_results`, `lyrics_search_empty`, `lyrics_search_failed`, `lyrics_search_closed`, and `wizard_moved`. The close event fires when the search window closes, including after selecting lyrics. `wizard_moved` fires when the user moves the setup window, but not for scripted animations. Events are buffered during the current step so a fast response is not missed. Starting a new search clears unconsumed events from the previous search. A wait at the end of a line advances when it receives a matching event.

`idle` monitors mouse movement and input received by LRCPlus; keyboard activity in another application is not observed. When the idle wait completes, `event` becomes `wizard_idle`, so a following `if event == "wizard_idle":` can select a route. For example:

```renpy
    wizard "Still there?"
    $ idle(10)
    if event == "wizard_idle":
        jump idle_response
```

To wait for an event and inactivity at the same time, include one `idle(seconds)` inside `wait_event`:

```renpy
    wizard "Try searching for lyrics."
    $ wait_event(lyrics_search_started,lyrics_search_closed,wizard_moved,idle(10))
    if event == "wizard_idle":
        wizard "You there?"
    if event == "lyrics_search_closed":
        jump search_closed
```

The first matching event wins and cancels the idle timer. Activity resets the idle timer. After an inline response, execution continues to the next statement, so add further branches or a route appropriate to your flow.

```renpy
label lyrics_online:
    before toggle_outside_click(true)
    wizard "Try searching for lyrics."
    $ wait_event(lyrics_search_started)
    wizard "Let's see..."
    $ wait_event(lyrics_search_results,lyrics_search_failed,lyrics_search_empty)
    if event == "lyrics_search_failed":
        jump search_failed
    if event == "lyrics_search_empty":
        jump no_results
    if event == "lyrics_search_results":
        jump results_found
```

`event` is the most recent event matched by `wait_event`. You can also put one `wizard` line directly inside an `if` block. After the user advances that line, the script continues:

```renpy
    $ wait_event(wizard_moved)
    if event == "wizard_moved":
        wizard "Hey, what are you doing? Off me!"
    wizard "Anyway..."
```

An `if` block currently supports one `wizard` line or one `jump`. Conditions only support the form shown here; they are not arbitrary Python expressions. Use `toggle_outside_click(true)` or `toggle_outside_click(false)` to set the state explicitly.
