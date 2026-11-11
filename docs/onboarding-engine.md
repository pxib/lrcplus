# Onboarding dialogue

Edit `assets/onboarding/onboarding.rpy`. The script uses a small Ren'Py-inspired
language. It never executes Python.

## Setup steps

Reopening the wizard from Settings opens **Ayami**, a returning-user companion
window. Its scripted `companion_welcome` greeting introduces the optional chat
input above the pacing separator. **Run setup again** returns to the original
wizard; first-run setup has no AI controls or network requests.

**AI settings** accepts an OpenAI-compatible Chat Completions API base URL,
model ID, and optional API key (local servers may not need one). HTTPS is
required except for localhost. No provider or model is selected automatically.
The key is session-only unless the user enables Windows account-bound encrypted
storage. If account encryption is unavailable, the key remains session-only and
the window explains that it was not saved. Clearing the key and saving removes
the stored credential. The request
format follows the [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

**Don't know where to get an API key?** opens a four-step local guide with
maiko's introduction, NanoGPT and NVIDIA NIM links, setup instructions, and
usage/cost information. Previous/Next navigate the guide; Done or Close returns
to the untouched settings form, including unsaved fields. Provider links open
externally only when clicked. Opening/browsing the guide makes no AI requests.
The guide text is maintained in `ui/ai_key_guide.py`; provider requirements and
pricing should be rechecked when editing it.

Chat requests use Qt's asynchronous HTTP transport after Send or Regenerate,
without automatic retries. They include Ayami's character instructions, up to ten
recent completed exchanges, and enabled user-authored memories; they do not
attach player state, file paths, songs, lyrics, or settings. Chats are saved
locally and can be reopened from the conversation picker.
Replies are displayed as plain text and cannot execute script commands or app
actions. Closing discards any pending reply; an already-sent request may finish
at the provider. This prototype does not stream replies. Its character prompt is
in `core/companion_chat.py`. Optional memory checks make separate requests as
described below.

**New conversation**, **Rename**, and **Delete** manage locally saved chats.
Chats are named from the first message until renamed. Sending and receiving
autosave the exchange; unfinished requests reopen as stopped. Reopening restores
pages and recent model context without replaying sounds or making a request.
Changing AI settings starts a new view while retaining the old saved chat. Chat
records and memory facts are readable JSON in the app data directory's
`companion.json`; they contain no provider key or custom authentication headers.
Writes are atomic. Invalid existing data is preserved and saving is disabled
rather than overwriting it. A failed save is reported in the window.

While a request is pending, **Send** becomes **Stop**. Stop aborts the local HTTP
connection and ignores stale completions; it cannot guarantee cancellation or
refunds at the provider. **Regenerate** requests a replacement for the latest
answer using the conversation before that exchange. The original answer remains
until a replacement succeeds, including after stopping or failing regeneration.
Regeneration replaces the answer on the same page and its model context; token
usage for every completed request remains counted for the window.

**Memories** opens a user-edited list: one fact per line, up to 20 facts of 200
characters each. Save adds/edits/removes facts explicitly; clearing the list
removes all saved memories. The inclusion checkbox can disable their use without
deleting them. Enabled facts are supplied with future messages to the selected
provider, labeled as factual data rather than instructions. Disabling/removing a fact does not erase prior chat messages
or provider records that already contain it.

The automatic memory mode has **Off**, **Suggest for approval** (default), and
**Automatically save ordinary preferences**. While inclusion is enabled, it checks
new user messages every three completed exchanges, using a separate request to
the configured provider/model. **Check now** saves the editor settings and checks
the last three completed user messages immediately. No extraction starts on
close. Checkpoints prevent automatic retries of the same batch, including after
a failed or stopped check. Provider-specific body parameters are retained except
chat-only tools, response formats, and stop sequences. Extraction sends selected
user messages and existing/dismissed facts, without assistant dialogue or the full
character prompt. It asks for stable, explicitly stated facts, excluding moods,
roleplay, guesses, third-party statements, sensitive details, and credentials.

Each accepted candidate must have a category, topic, source page, and exact
supporting user quote. Invalid candidates, exact duplicates, and recently
dismissed/removed facts are ignored. Up to 50 pending suggestions are stored.
**Review** shows the source and supports editing, Save/Replace, or Dismiss.
**Sources** shows provenance for approved/automatic memories; manual edits retain
source details only for unchanged facts. Automatic mode saves only ordinary
preferences or communication preferences with no detected conflict. Names,
projects, topic conflicts, full memory lists, and potential overlaps with
unclassified manual facts require approval. Fact extraction and semantic conflict
detection depend on the model; review remains available for corrections.

Memory checks can run alongside chat. Their reported tokens contribute to the
window total and are labeled **Last memory check** when shown. **Stop check**
cancels local waiting; switching chats, disabling extraction/inclusion, or closing
the companion also cancels it. A failed check leaves chat unaffected, and an
invalid suggestion response still contributes its reported usage. Extraction
results arriving during manual editing become suggestions rather than changing
the list underneath the editor. Suggestions, dismissals, checkpoints, and source
details persist locally with chats and memories.

The AI prompt is a curated adaptation of `assets/onboarding/CANON_CHARACTER.md`:
identity, voice, relevant preferences, relationships, and ways to respond to the
user. Character traits are translated into delivery guidance rather than a full
biography. Runtime capabilities and expression syntax are defined by the actual
chat implementation, independently of the canon document's older description of
script-only dialogue. The canon file is not loaded or appended to each request;
edit the compact prompt explicitly when changing the AI characterization.

Conversation history is displayed inline as one exchange per page:
**You:** the sent message, followed by **Ayami:** its reply. **Previous** and
**Next** browse the session's exchanges, with a **Page N of M** indicator and
disabled buttons at either end. Long exchanges scroll within their own bounded
page. Sending always opens the newest page and continues the latest conversation;
browsing an earlier page does not branch or roll back the chat. The page shows a
pending/failed state until a reply arrives; retrying the same failed message
reuses its page. A reply arriving while an older page is selected updates its own
page without changing the selected page. Browsing restores expression labels
without replaying sounds, making requests, or changing token totals. All exchanges
remain browsable in the saved conversation independently of the ten recent
exchanges supplied to the model. No separate history window is used.

AI replies may start with one optional `[expression:playful]`-style prefix.
Only the fixed set of bundled reaction names is accepted; unknown names are
stripped without playing a sound. The prefix is removed from displayed text and
history. One reaction plays before the reply appears, respecting the existing
Sound toggle. The expression label still updates while muted. Replies without a
tag clear the preceding expression. Text elsewhere cannot trigger reactions or
script commands. The prompt asks for sparse reactions, with ordinary replies
silent by default.

The **Tokens** line shows the latest reply's input/output/total usage and the
window's cumulative reported total. Counts come from the provider response's
`usage` object, never from model-authored text. When input and output counts are
both present, a missing total is computed from their sum. Missing usage is marked
as unreported, so an incomplete session total is not presented as complete.
The tooltip shows cached/reasoning token details when available; these are not
added a second time. Counts include instructions and resent conversation history,
and are not a context-size meter or billing estimate. Failed/discarded requests
may consume tokens without appearing here. Session counts survive changes to AI
settings and reset when the companion window is reopened. The response fields
are documented in [NanoGPT's Chat Completion reference](https://docs.nano-gpt.com/api-reference/endpoint/chat-completion).

**AI settings > Advanced parameters** accepts three optional JSON fields:
additional body parameters (object), excluded body parameter names (array), and
additional request headers (object of strings). For example, include
`{"service_tier": "flex", "reasoning_effort": "low"}` and exclude
`["temperature"]`. The provider/model must support the chosen parameters.
Body parameters are merged before exclusions are applied. `model`, `messages`,
and `stream` cannot be overridden or excluded. Bearer authentication uses the
API key field; managed authentication and transport headers cannot be replaced.
Body parameters and exclusions are saved with preferences; custom headers follow
the same session-only/encrypted storage choice as the API key. Do not put
credentials in the body parameters field. Blank fields use empty objects/arrays.
Invalid JSON, non-finite numbers, and invalid headers block saving and sending.

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
