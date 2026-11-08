# LRCPlus onboarding script. Edit the dialogue and cues below.
# Supported Ren'Py-inspired subset; this does not execute Python.
# Checkpoints: 1 welcome, 2 library, 3 appearance, 6 lyrics wrap-up, 7 search.
# Optional explanations use call/return; checkpoints handle nonlocal recovery.

default detailed_tour = true
default library_tips_read = false
default local_lyrics_explained = false
default lyrics_attempts = 0
default lyrics_found = false
default search_in_progress = false
default wizard_moves = 0
default idle_reminders = 0

label firststeplanguage:
    wizard "Youkoso to LRCPlus!"
    $ auto(2)
    wizard "I'm the setup wizard. Tiny window. Big responsibility."
    $ auto(3)
    wizard "To COMMENCE forth, please choose a language."
    $ auto(2)
    wizard "Oh, right."
    $ pause(0.8)
    extend " I didn't even localize this software. English it is. Mb."
    $ auto(3)
    wizard "Before we get going: you can click the dialogue or press Space to move along."
    $ auto(3)
    wizard "The controls down here change text speed, Auto-read, and motion. Adjust them whenever."
    $ auto(4)
    wizard "If I stop for a choice or wait for you to do something in the player, Auto-read will wait too."
    $ auto(4)
    wizard "So. How much tour are we doing?"
    menu:
        "Show me around":
            call welcome_detailed
        "Just the essentials":
            call welcome_quick
    before checkpoint(1)
    wizard "We'll sort out your music folders, pick a look, try some reading preferences, and talk lyrics. Hit Next when you're ready."
    $ end

label welcome_detailed:
    $ detailed_tour = true
    wizard "A fellow appreciator of unnecessary dialogue. Excellent."
    $ auto(3)
    wizard "I'll point out the useful stuff, and you can ask for a little more detail along the way."
    $ auto(3)
    wizard "You can still skip setup whenever. I won't take it personally."
    $ pause(0.6)
    extend " Probably."
    return

label welcome_quick:
    $ detailed_tour = false
    wizard "Got you. Short explanations, optional detours, then music."
    $ auto(3)
    wizard "If something catches your eye, the longer explanations will still be there."
    return

label secondstepmusiclibraryfolders:
    wizard "First up: where does your music live?"
    $ auto(2)
    wizard "LRCPlus can scan folders you add to your library."
    $ pause(3)
    if detailed_tour:
        wizard "You don't need to solve your entire folder situation today. One folder is a perfectly respectable start."
    wizard "If you have a... lot of music, the scan may take a little while."
    $ pause(0.7)
    extend " Your collection has lore. I respect that."
    $ auto(3)
    wizard "Want a couple of folder tips before the controls appear?"
    menu:
        "Folder tips":
            call library_tips
        "I'm good":
            call library_ready
    before checkpoint(2)
    wizard "Use Add music folder below to choose a folder. You can also leave this for later."
    $ auto(4)
    wizard "When you're happy with the folder list, hit Next. More folders can always come later."
    $ end

label library_tips:
    wizard "Start with the folder that actually contains your music collection."
    $ auto(3)
    wizard "If your collection is split across drives or folders, you can add more than one."
    $ auto(3)
    wizard "A small folder is handy for getting a feel for the player before scanning the big archive."
    $ auto(4)
    wizard "And if you keep lyric files beside your songs, leave those together. Future you will appreciate it."
    $ auto(4)
    wizard "There. Folder wisdom. Delivered by a window that doesn't own any folders."
    $ library_tips_read = true
    return

label library_ready:
    wizard "Fair. You know where the music is. I was just checking."
    $ pause(2)
    if library_tips_read:
        wizard "And we've already covered the folder wisdom. I am capable of remembering things now."
    return

label thirdstepappearance:
    wizard "Alrighhht, this is the spicy part. Appearance."
    $ auto(3)
    wizard "I prefer Sakura ngl. But this is your player, so my vote is purely decorative."
    $ pause(4)
    if detailed_tour:
        wizard "The light and dark modes change the overall look. The presets give you different color choices."
    wizard "Want a quick explanation, or are you ready to make it look like you?"
    menu:
        "Explain the choices":
            call appearance_tips
        "Let me pick":
            call appearance_ready
    before checkpoint(3)
    wizard "The appearance controls are yours now. Try a mode or a preset and see what feels good."
    $ auto(4)
    wizard "You can always change your theme later in Settings. This is not a legally binding aesthetic commitment."
    $ end

label appearance_tips:
    wizard "Light or dark is the big choice. Pick whichever feels comfortable to read."
    $ auto(3)
    wizard "Then try a theme preset. You're allowed to pick one because the name sounds nice."
    $ auto(3)
    wizard "Check the text and controls as well as the colors. Pretty is good. Being able to read things is also pretty good."
    $ auto(4)
    wizard "The best theme is the one you still like after staring at it for a while."
    $ pause(0.6)
    extend " Deeply scientific advice."
    return

label appearance_ready:
    wizard "Go for it. I'll stand here and pretend I have no strong opinions about pink."
    return

label readingpreferences:
    wizard "One more thing before the lyrics search: can you actually read the lyrics comfortably?"
    $ auto(4)
    wizard "There's a sample coming up. Adjust the lyric size and try furigana or romaji to see what feels right."
    $ auto(4)
    if detailed_tour:
        wizard "Furigana adds Japanese readings above the words. Romaji shows those readings in Latin letters."
        $ auto(4)
    wizard "Try the sample below and pick what's comfortable today. You can change your reading preferences in Settings whenever."
    $ end

label fourthsteplyrics:
    wizard "Okay. Music, a little personality, and now the thing in the name: lyrics."
    $ auto(3)
    wizard "LRCPlus can use your own lyric files, or help you search for lyrics online."
    $ pause(3)
    if library_tips_read:
        wizard "Remember the bit about keeping lyrics beside your songs? This is where that starts being useful."
    wizard "Want me to show you the online search?"
    menu:
        "Yes":
            jump lyrics_online
        "No":
            jump ok
        "Explain local lyrics":
            jump lyrics_local
    before checkpoint(6)
    before toggle_outside_click(false)
    wizard "You can explore furigana, romanization, and other lyric display options in Settings."
    $ pause(4)
    if lyrics_found:
        wizard "And you've already found some lyrics online. First expedition: successful."
        $ auto(3)
    elif lyrics_attempts >= 2:
        wizard "We've given online search a couple of tries. You can leave it for later without holding up setup."
        $ auto(4)
    else:
        wizard "You don't need to find the perfect lyrics during setup. You can come back to search whenever."
        $ auto(4)
    wizard "If a lyric file includes timing, it can follow the song. Plain text is useful too; it just doesn't carry the same timing information."
    $ pause(5)
    if detailed_tour:
        wizard "Start with a song you know well. It's much easier to notice whether the lyrics and timing feel right."
    wizard "That's the tour. Pick a track, get comfortable, and let the player do its thing."
    $ auto(3)
    wizard "Thanks for hanging out with a setup window for this long. Next takes you to the finish."
    $ end

label lyrics_local:
    $ local_lyrics_explained = true
    wizard "Local lyrics are lyric files you already have. No search required to explain that one."
    $ auto(3)
    wizard "LRC files can include line timestamps. LRCX can carry extra detail such as word timing and furigana."
    $ auto(4)
    wizard "LRCPlus prefers LRCX when matching LRC and LRCX files are both available."
    $ auto(4)
    wizard "Keep the lyric file with the corresponding song and give it a matching filename."
    $ auto(4)
    wizard "If your collection is already set up that way, nice. Past you did us a favor."
    $ auto(3)
    wizard "Want to see online search as well?"
    menu:
        "Show online search":
            jump lyrics_online
        "Continue setup":
            jump lyrics_continue

label lyrics_online:
    wizard "You can search for lyrics online from LRCPlus."
    $ next
    if local_lyrics_explained:
        wizard "Same goal as local files. Different way of finding the words."
    wizard "Let's see..."
    $ pause(1)
    $ point_at(lyrics_button,in_out_cubic)
    extend " ^ right here."
    $ auto(2)
    wizard "That's the lyrics control. I'll open its menu and show you where search lives."
    $ auto(3)
    wizard "I'll... click on it for you."
    $ pause(1)
    $ click_control(lyrics_button,menu_disrupted)
    $ watch_menu(lyrics_menu,menu_disrupted)
    $ move(78.4%,20%,out_cubic)
    extend " Hmm..."
    $ auto(2)
    wizard "Ah, there it is."
    $ pause(1)
    $ click_menu(lyrics_menu,search_lyrics,menu_disrupted)
    $ auto(2)
    before hide_cursor
    before toggle_outside_click(true)
    before checkpoint(7)
    wizard "Okay, try searching for lyrics now."
    if search_in_progress:
        jump lyrics_waiting_results
    $ move(68.4%,33%,out_cubic)
    $ wait_event(lyrics_search_started,lyrics_search_closed,wizard_moved,idle(20))
    if event == "lyrics_search_closed":
        jump search_closed
    if event == "wizard_moved":
        jump window_moved
    if event == "wizard_idle":
        jump youretakingtoolong
    $ lyrics_attempts += 1
    $ search_in_progress = true
    jump lyrics_waiting_results

label lyrics_waiting_results:
    wizard "Ooh, searching... I'll stay out of the way while that comes back."
    $ wait_event(lyrics_search_results,lyrics_search_failed,lyrics_search_empty,lyrics_search_closed,wizard_moved,idle(20))
    if event == "lyrics_search_closed":
        jump search_closed
    if event == "lyrics_search_failed":
        jump search_failed
    if event == "lyrics_search_empty":
        jump no_results
    if event == "lyrics_search_results":
        jump results_found
    if event == "wizard_moved":
        jump window_moved
    if event == "wizard_idle":
        jump youretakingtoolongbro

label menu_disrupted:
    wizard "Where the f... where did that go? I was trying to show you the search, bro."
    $ auto(3)
    wizard "No big deal. The lyrics menu will still be there after setup."
    $ auto(3)
    wizard "I'll stop poking at things and let you explore it yourself later."
    $ pause(1)
    jump lyrics_continue

label search_failed:
    $ search_in_progress = false
    wizard "Yikes... something went wrong with the search. "
    $ toggle_outside_click(false)
    extend "That doesn't mean your song has no lyrics."
    $ pause(4)
    if lyrics_attempts >= 2:
        wizard "We've given it a couple of tries. It's completely fine to leave this for later."
    wizard "You can retry, get a few search tips, or keep setting up."
    menu:
        "Try again":
            jump lyrics_retry
        "Search tips":
            jump search_tips
        "Continue":
            jump lyrics_continue

label no_results:
    $ search_in_progress = false
    wizard "No lyrics this time... "
    $ toggle_outside_click(false)
    extend "Could be the search terms. Could be the catalog."
    $ auto(4)
    wizard "Try the artist and song title without extra filename clutter, if there is any."
    $ auto(4)
    wizard "Another attempt, a little more advice, or shall we move on?"
    menu:
        "Try again":
            jump lyrics_retry
        "Search tips":
            jump search_tips
        "Continue":
            jump lyrics_continue

label search_tips:
    before toggle_outside_click(false)
    wizard "Start with the artist and song title. Those are usually the useful bits."
    $ auto(3)
    wizard "If the query includes a track number, extension, or release tag, try leaving that part out."
    $ auto(4)
    wizard "Different recordings can have different timings. Check that a result actually fits the version you're playing."
    $ auto(4)
    wizard "And if nothing useful turns up, local lyrics are still an option. Searching online isn't homework."
    $ auto(4)
    wizard "What do you want to do?"
    menu:
        "Search again":
            jump lyrics_retry
        "Continue setup":
            jump lyrics_continue

label results_found:
    $ search_in_progress = false
    $ lyrics_found = true
    wizard "There we go! You can pick a result to take a closer look."
    $ pause(3)
    wizard "Give the title and artist a quick check. A familiar title can still belong to a different song."
    $ auto(4)
    wizard "If you have a few versions to choose from, look for the one that fits your recording."
    $ pause(4)
    if lyrics_attempts >= 2:
        wizard "Took a little persistence. We got there."
    wizard "I'll head back to my corner. You can explore the results at your own pace after the tour."
    $ pause(1)
    $ reset
    $ return_checkpoint(6)

# Keep the two search waits together so fast search responses can be buffered.
label lyrics_retry:
    before toggle_outside_click(true)
    wizard "Go ahead, give it another try. You can close the search window if you'd rather move on."
    $ wait_event(lyrics_search_started,lyrics_search_closed)
    if event == "lyrics_search_closed":
        jump search_closed
    $ lyrics_attempts += 1
    $ search_in_progress = true
    wizard "Let's see... fingers crossed for a useful result."
    $ wait_event(lyrics_search_results,lyrics_search_failed,lyrics_search_empty,lyrics_search_closed)
    if event == "lyrics_search_closed":
        jump search_closed
    if event == "lyrics_search_failed":
        jump search_failed
    if event == "lyrics_search_empty":
        jump no_results
    if event == "lyrics_search_results":
        jump results_found

label search_closed:
    $ search_in_progress = false
    wizard "Did you just... close the search? Okay, that's fine. You can always search again later."
    $ pause(2)
    wizard "We'll keep moving. No compulsory lyric research on my watch."
    $ pause(3)
    $ reset
    $ return_checkpoint(6)

label lyrics_continue:
    $ search_in_progress = false
    wizard "No worries, you can always search again later."
    $ pause(3)
    if local_lyrics_explained:
        wizard "And you've got the local lyric file option in your back pocket."
    wizard "The search button isn't going anywhere. Neither are your songs."
    $ pause(1)
    $ reset
    $ return_checkpoint(6)

label window_moved:
    $ wizard_moves += 1
    wizard "What are you doing? Get off me!"
    $ pause(1)
    if wizard_moves >= 3:
        wizard "At this point I'm going to need a seatbelt."
        $ pause(1)
    elif wizard_moves >= 2:
        wizard "We are rearranging the furniture again. Okay. Finding my balance."
        $ pause(1)
    else:
        wizard "Okay. New spot. Finding my feet."
        $ pause(0.5)
    wizard "Bro."
    $ pause(0.8)
    extend " You can move me, but I was in the middle of something."
    $ auto(3)
    wizard "Alright. Back to the search. I won't make you open the menu all over again."
    $ pause(3)
    $ return checkpoint(7)

label youretakingtoolong:
    $ idle_reminders += 1
    wizard "You still there? No rush. I'm just checking the window didn't eat you."
    $ pause(3)
    if idle_reminders >= 2:
        wizard "We can leave search for later if this isn't a good moment."
    wizard "Want more time, a few search tips, or the rest of setup?"
    menu:
        "More time":
            jump lyrics_more_time
        "Search tips":
            jump search_tips
        "Continue setup":
            jump lyrics_continue

label youretakingtoolongbro:
    $ idle_reminders += 1
    wizard "Still waiting on that search? We can give it more time."
    $ auto(3)
    wizard "Or we can move on. You don't need to wait here just to finish setting up."
    menu:
        "Keep waiting":
            jump lyrics_more_time
        "Continue setup":
            jump lyrics_continue

label lyrics_more_time:
    before toggle_outside_click(true)
    wizard "Take your time. I'll be right here."
    $ pause(2)
    $ return_checkpoint(7)

label ok:
    wizard "Fair enough."
    $ pause(1)
    extend " Local files, online search, or just listening for now. All valid."
    $ pause(4)
    $ return_checkpoint(6)
