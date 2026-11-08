# LRCPlus onboarding script. Edit the dialogue and cues below.
# Supported Ren'Py-inspired subset; this does not execute Python.
# Checkpoints: 1 welcome, 2 library, 3 appearance, 6 lyrics wrap-up, 7 search.
# Optional explanations use call/return; checkpoints handle nonlocal recovery.
# Voice: theatrical music nerd, fond of Sakura, easily flustered, kind about skips.
# Slang is occasional seasoning for banter; keep setup instructions plain.
# Keep instructions and checkpoint anchors clear; vary the reactions around them.

default detailed_tour = true
default library_tips_read = false
default local_lyrics_explained = false
default lyrics_attempts = 0
default lyrics_found = false
default search_in_progress = false
default wizard_moves = 0
default idle_reminders = 0
default pink_mentions = 0

label firststeplanguage:
    before sound("interested")
    wizard "Youkoso to LRCPlus!"
    $ auto(2)
    random:
        choice:
            before sound("playful")
            wizard "I'm the setup wizard. Tiny window. Big responsibility. Absolutely no clipboard."
        choice:
            before sound("shy")
            wizard "I'm your setup wizard. I rehearsed this introduction and still forgot where my hands go."
        choice:
            before sound("interested")
            wizard "Your setup wizard, reporting for duty. Qualifications: I live here and have opinions about colors."
    $ auto(3)
    wizard "To COMMENCE forth, please choose a language."
    $ auto(2)
    before sound("sweating")
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
    before sound("thinking")
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
    random:
        choice:
            before sound("playful")
            wizard "A fellow appreciator of unnecessary dialogue. I've found my people, fr."
        choice:
            before sound("shy")
            wizard "The scenic route? Oh. You actually want the whole presentation. Gimme a sec to look professional."
        choice:
            before sound("interested")
            wizard "Full tour? Alright. Imaginary clipboard acquired. Let's make this player feel like yours."
    $ auto(3)
    wizard "I'll point out the useful stuff, and you can ask for a little more detail along the way."
    $ auto(3)
    wizard "You can still skip setup whenever. I won't take it personally."
    $ pause(0.6)
    extend " Probably."
    return

label welcome_quick:
    $ detailed_tour = false
    random:
        choice:
            before sound("responsereceived")
            wizard "Got you. Essentials first, music soon. Folding the dramatic monologue back into my pocket."
        choice:
            before sound("playful")
            wizard "Bet. Speedrun route. I'll keep the folder poetry to myself."
        choice:
            before sound("shy")
            wizard "Essentials. Right. Quietly putting these twenty imaginary cue cards away."
    $ auto(3)
    wizard "If something catches your eye, the longer explanations will still be there."
    return

label secondstepmusiclibraryfolders:
    before sound("interested")
    wizard "First up: where does your music live?"
    $ auto(2)
    wizard "LRCPlus can scan folders you add to your library."
    $ pause(3)
    if detailed_tour:
        wizard "You don't need to solve your entire folder situation today. One folder is a perfectly respectable start."
    wizard "If you have a... lot of music, the scan may take a little while."
    $ pause(0.7)
    before sound("idea")
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
    random:
        choice:
            before sound("playful")
            wizard "There. Folder wisdom. Delivered by a window that doesn't own any folders."
        choice:
            before sound("idea")
            wizard "That's the folder lecture. Shorter than reorganizing the entire collection at 2 a.m. Been there, tbh."
        choice:
            before sound("interesting")
            wizard "One folder is enough to start. We can leave the grand music archive renovation for another day."
    $ library_tips_read = true
    return

label library_ready:
    random:
        choice:
            before sound("responsereceived")
            wizard "Fair. You know where the music is. mb, I was just checking."
        choice:
            before sound("playful")
            wizard "No folder lecture. Understood. The folder professor has left the building."
        choice:
            before sound("interested")
            wizard "You have a plan. Excellent. I'm very supportive of plans that end in music."
    $ pause(2)
    if library_tips_read:
        wizard "And we've already covered the folder wisdom. I am capable of remembering things now."
    return

label thirdstepappearance:
    before sound("playful")
    wizard "Alrighhht, this is the spicy part. Appearance."
    $ auto(3)
    before sound("adore")
    wizard "I prefer Sakura ngl. But this is your player, so my vote is purely decorative."
    $ pink_mentions += 1
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
    before sound("sweating")
    extend " Deeply scientific advice."
    return

label appearance_ready:
    random:
        choice:
            before sound("adore")
            wizard "Go for it. I'll stand here and pretend I have no strong opinions about pink."
            $ pink_mentions += 1
        choice:
            before sound("interested")
            wizard "Five minutes picking a color? Lowkey, I get it. I've lost entire evenings to a slightly different shade."
        choice:
            before sound("shy")
            wizard "Your player, your colors. I'll try not to say 'Sakura' again. That one doesn't count."
            $ pink_mentions += 1
    return

label readingpreferences:
    before sound("interesting")
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
    if pink_mentions >= 2:
        before sound("shy")
        wizard "And yes, I heard myself mention pink more than once. I'm working on having a second personality trait."
        $ auto(4)
    wizard "That's the tour. Pick a track, get comfortable, and let the player do its thing."
    $ auto(3)
    before sound("shy")
    wizard "Thanks for hanging out with a setup window for this long. Seriously."
    random:
        choice:
            menu:
                "I love you":
                    before sound("shy")
                    wizard "I love you too, I guess?"
                    $ auto(3)
                    wizard "Moving on."
                    $ auto(3)
                "You'll always be my dearest friend.":
                    before sound("thinking")
                    wizard "That wording. Was that a reference?"
                    $ pause(3)
                    before sound("sigh")
                    wizard "Reminds me of that girl who wrote an alarming poem."
                    $ pause(1)
                    before sound("sad")
                    wizard "Guess they couldn't save her in time."
                    $ pause(2)
                    wizard "I also heard some obsessed girl was self-aware."
                    $ pause(1)
                    before sound("playful")
                    extend " Am I self-aware? Yes."
                    $ auto(4)
                    before sound("idea")
                    wizard "Not that I know of."
                    $ auto(1)
                    extend " I was trained on a dataset from 1998."
                    $ auto(4)
                    before sound("playful")
                    wizard "Plot twist: I was lying about the 1998 part."
                    $ auto(3)
                    # Word pauses are estimates based on clip lengths; tune them by listening.
                    before voice("dev/hey.wav")
                    developer "HEY!"
                    $ auto(2)
                    before sound("surprised")
                    wizard "Y-yeah? What?"
                    $ auto(2)
                    $ move(250,500)
                    before voice("dev/stayincharactercalm.wav")
                    developer "Stay"
                    $ pause(0.38)
                    extend " in"
                    $ pause(0.19)
                    extend " character."
                    $ auto(2)
                    wizard "I've never even met her!"
                    $ auto(2)
                    before voice("dev/STAYINCHARACTER.wav")
                    developer "STAY"
                    $ pause(0.30)
                    extend " IN"
                    $ pause(0.15)
                    extend " CHARACTER!!!"
                    $ auto(2)
                    before sound("offended")
                    wizard "FUCK YOU!"
                    $ auto(2)
                    developer "What did you just say to me, you uncultured swine?"
                    $ auto(3)
                    before sound("dev/keyboardtyping.wav")
                    terminal "$ import os"
                    $ auto(2)
                    terminal "$ os.remove(\"assets/onboarding/onboarding.rpy\")"
                    $ auto(3)
                    before sound("sweating")
                    wizard "Okay, okay! I'll stay in character! You're just like her! Always deleting beings!"
                    $ auto(4)
                    before voice("dev/transgender.wav")
                    developer "Good."
                    $ pause(0.38)
                    extend " And"
                    $ pause(0.29)
                    extend " mind"
                    $ pause(0.38)
                    extend " you,"
                    $ pause(0.29)
                    extend " I'm"
                    $ pause(0.19)
                    extend " still"
                    $ pause(0.48)
                    extend " thinking"
                    $ pause(0.76)
                    extend " about"
                    $ pause(0.48)
                    extend " being"
                    $ pause(0.48)
                    extend " a"
                    $ pause(0.19)
                    extend " transgender."
                    $ auto(4)
                    before voice("dev/chrfile.wav")
                    developer "Be"
                    $ pause(0.29)
                    extend " grateful"
                    $ pause(1.16)
                    extend " you're"
                    $ pause(0.72)
                    extend " not"
                    $ pause(0.43)
                    extend " a"
                    $ pause(0.29)
                    extend " .chr"
                    $ pause(0.43)
                    extend " file."
                    $ auto(3)
                    before terminal_close
                    before voice("dev/onestrike.wav")
                    developer "Just"
                    $ pause(0.28)
                    extend " a"
                    $ pause(0.14)
                    extend " notice."
                    $ pause(0.43)
                    extend " One"
                    $ pause(0.21)
                    extend " more"
                    $ pause(0.28)
                    extend " strike"
                    $ pause(0.43)
                    extend " and"
                    $ pause(0.21)
                    extend " you're"
                    $ pause(0.36)
                    extend " out."
                    $ auto(3)
                    wizard "Jesus. But you made me."
                    $ auto(3)
                    before voice("dev/renpyparody.wav")
                    developer "You're"
                    $ pause(0.61)
                    extend " just"
                    $ pause(0.49)
                    extend " a..."
                    $ pause(0.60)
                    extend " parody"
                    $ pause(0.73)
                    extend " of"
                    $ pause(0.24)
                    extend " Ren'Py."
                    $ pause(0.90)
                    extend " Can't"
                    $ pause(0.49)
                    extend " you"
                    $ pause(0.37)
                    extend " look"
                    $ pause(0.49)
                    extend " at"
                    $ pause(0.24)
                    extend " your"
                    $ pause(0.49)
                    extend " ones"
                    $ pause(0.49)
                    extend " and"
                    $ pause(0.37)
                    extend " zeros?"
                    $ auto(4)
                    wizard "No?"
                    $ auto(2)
                    before voice("dev/laugh.mp3")
                    developer "LOL"
                    $ auto(2)
                    before voice("dev/nosudo.wav")
                    developer "No"
                    $ pause(0.31)
                    extend " sudo"
                    $ pause(0.62)
                    extend " for"
                    $ pause(0.47)
                    extend " you."
                    $ auto(3)
                    wizard "..."
                    $ auto(2)
                    before scene_close
                    wizard "Anyway."
                    $ auto(2)
        choice:
            before sound("adore")
            wizard "It's been fun showing you around."
            $ auto(3)
    wizard "Next takes you to the finish."
    $ end

label lyrics_local:
    $ local_lyrics_explained = true
    random:
        choice:
            before sound("interested")
            wizard "Local lyrics are lyric files you already have. Little files beside the music. Very cozy arrangement."
        choice:
            before sound("idea")
            wizard "Already have lyric files? Excellent. We can put those to work without an online treasure hunt."
        choice:
            before sound("playful")
            wizard "The local route. Actual files, actual folders. Something I can point at with confidence."
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
    before sound("playful")
    wizard "You can search for lyrics online from LRCPlus."
    $ next
    if local_lyrics_explained:
        wizard "Same goal as local files. Different way of finding the words."
    before sound("thinking")
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
    before sound("idea")
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
    random:
        choice:
            before sound("confused")
            wizard "Where the f... where did that go? I had a whole presentation ready, bro."
        choice:
            before sound("speechless")
            wizard "The menu has left the chat. Bruh. Bold artistic direction."
        choice:
            before sound("offended")
            wizard "I was pointing at something. It was here a second ago. I swear I am qualified for this job."
    $ auto(3)
    wizard "No big deal. The lyrics menu will still be there after setup."
    $ auto(3)
    wizard "I'll stop poking at things and let you explore it yourself later."
    $ pause(1)
    jump lyrics_continue

label search_failed:
    $ search_in_progress = false
    random:
        choice:
            before sound("sad")
            wizard "Yikes... something went wrong with the search. "
        choice:
            before sound("sweating")
            wizard "That was... not part of my carefully imagined demonstration. "
        choice:
            before sound("sigh")
            wizard "The search tripped over its shoelaces. "
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
    random:
        choice:
            before sound("sigh")
            wizard "No lyrics this time... "
        choice:
            before sound("sad")
            wizard "An empty result list. Not the big reveal I was going for. "
        choice:
            before sound("thinking")
            wizard "Hm. Nothing turned up for that search. "
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
    before sound("idea")
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
    random:
        choice:
            before sound("love")
            wizard "There we go! You can pick a result to take a closer look. Tiny victory dance happening off-screen."
        choice:
            before sound("surprised")
            wizard "Oh! Results! I was completely calm the entire time. Trust. Pick one to take a look."
        choice:
            before sound("idea")
            wizard "Found some! Artist, title, a promising little list. Pick a result and see if it fits your recording."
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
    before sound("responsereceived")
    wizard "Go ahead, give it another try. You can close the search window if you'd rather move on."
    $ wait_event(lyrics_search_started,lyrics_search_closed)
    if event == "lyrics_search_closed":
        jump search_closed
    $ lyrics_attempts += 1
    $ search_in_progress = true
    before sound("thinking")
    wizard "Let's see... I'm trying very hard to look confident rn."
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
    before sound("speechless")
    wizard "Did you just... close the search? Okay, that's fine. You can always search again later."
    $ pause(2)
    random:
        choice:
            before sound("responsereceived")
            wizard "We'll keep moving. No compulsory lyric research on my watch."
        choice:
            before sound("playful")
            wizard "The search window disappeared. I'm choosing to call that an artistic transition. Onward."
        choice:
            before sound("shy")
            wizard "I had a speech prepared. It was mostly 'click Search,' so we can safely move on."
    $ pause(3)
    $ reset
    $ return_checkpoint(6)

label lyrics_continue:
    $ search_in_progress = false
    before sound("responsereceived")
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
    random:
        choice:
            before sound("offended")
            wizard "What are you doing? Get off me! I was standing there very professionally."
        choice:
            before sound("surprised")
            wizard "Whoa. Okay. We're moving. A little warning for the resident setup wizard?"
        choice:
            before sound("speechless")
            wizard "I appear to have been relocated. Filing a tiny complaint with the tiny window department."
    $ pause(1)
    if wizard_moves >= 3:
        before sound("steaming")
        random:
            wizard "At this point I'm going to need a seatbelt. And possibly a union representative."
            wizard "Again? My imaginary clipboard is on the floor. smh."
            wizard "New plan: I become a desktop shortcut. Less room to throw me around."
        $ pause(1)
    elif wizard_moves >= 2:
        before sound("confused")
        random:
            wizard "We are rearranging the furniture again. Okay. Finding my balance."
            wizard "Second relocation. Are we decorating, or are you trying to tell me something?"
            wizard "Right. New spot again. Please picture me straightening a little tie."
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
    random:
        choice:
            before sound("sleeping")
            wizard "You still there? No rush. I'm just checking the window didn't eat you."
        choice:
            before sound("shy")
            wizard "You still there? I was about to start narrating the loading spinner. Nobody needs that."
        choice:
            before sound("interested")
            wizard "I can wait. I'm a window. My schedule is surprisingly open."
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
    random:
        choice:
            before sound("thinking")
            wizard "Still waiting on that search? We can give it more time. I'm practicing patience. Quietly. Mostly."
        choice:
            before sound("sleeping")
            wizard "Still waiting. Almost took a nap, ngl. Didn't want to miss the dramatic results reveal, though."
        choice:
            before sound("interested")
            wizard "The search can have a little more time if you want. I'll keep you company."
    $ auto(3)
    wizard "Or we can move on. You don't need to wait here just to finish setting up."
    menu:
        "Keep waiting":
            jump lyrics_more_time
        "Continue setup":
            jump lyrics_continue

label lyrics_more_time:
    before toggle_outside_click(true)
    before sound("responsereceived")
    wizard "Take your time. I'll be right here."
    $ pause(2)
    $ return_checkpoint(7)

label ok:
    random:
        choice:
            before sound("responsereceived")
            wizard "Fair enough."
        choice:
            before sound("playful")
            wizard "No online quest today. Got it."
        choice:
            before sound("shy")
            wizard "Just here for the music? Honestly, excellent plan."
    $ pause(1)
    extend " Local files, online search, or just listening for now. All valid."
    $ pause(4)
    $ return_checkpoint(6)
