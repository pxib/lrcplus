# LRCPlus onboarding script. Edit the dialogue and cues below.
# This uses a supported Ren'Py-inspired subset; it is not a full Ren'Py runtime.

label firststeplanguage:
    wizard "Youkoso to LRCPlus!"
    $ auto(3)
    wizard "To COMMENCE forth, please choose a language."
    $ auto(3)
    wizard "Oh, right. I forgot I didn't even localize this software. Mb, skip this step."
    $ end

label secondstepmusiclibraryfolders:
    wizard "LRCPlus can scan your music library for lyrics."
    $ auto(2)
    wizard "If you have a... lot of music, it may take a little while to scan."
    $ auto(3)
    wizard "You can always add more folders later. I think."
    $ end

label thirdstepappearance:
    wizard "Alrighhht, this is the spicy part. You can adjust the appearance of LRCPlus to your liking. I prefer Sakura ngl."
    $ auto(6)
    wizard "Pick whichever feels more like you."
    $ auto(3)
    wizard "You can always change your theme later in Settings~"
    $ end

label fourthsteplyrics:
    wizard "LRCPlus can display lyrics from your own files, or help you search for them online."
    $ auto(3)
    wizard "Okay, so... you wanna search for lyrics online?"
    menu:
        "Yes":
            jump lyrics_online
        "No":
            jump ok
    before checkpoint(6)
    wizard "You can explore furigana, romanization, and other display options in Settings."
    $ end

label lyrics_online:
    wizard "You can search for lyrics online from LRCPlus."
    $ next
    wizard "Let's see..."
    $ pause(1)
    $ point_at(lyrics_button,in_out_cubic)
    extend " ^ right here."
    $ auto(2)
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
    $ move(68.4%,33%,out_cubic)
    $ wait_event(lyrics_search_started,lyrics_search_closed,wizard_moved,idle(20))
    if event == "lyrics_search_closed":
        jump search_closed
    if event == "wizard_moved":
        jump window_moved
    if event == "wizard_idle":
        jump youretakingtoolong
    wizard "Ooh, searching..."
    $ wait_event(lyrics_search_results,lyrics_search_failed,lyrics_search_empty,lyrics_search_closed,wizard_moved,wizard_idle,idle(20))
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
    $ auto(2)
    wizard "Okay, whatever. Let's just move on."
    $ pause(1)
    jump lyrics_continue

label search_failed:
    wizard "Yikes... something went wrong with the search. "
    $ toggle_outside_click(false)
    extend "Want to try again?"
    menu:
        "Try again":
            jump lyrics_retry
        "Continue":
            jump lyrics_continue

label no_results:
    wizard "No lyrics this time... "
    $ toggle_outside_click(false)
    extend "Maybe try a different search?"
    menu:
        "Try again":
            jump lyrics_retry
        "Continue":
            jump lyrics_continue

label results_found:
    wizard "There we go! You can pick a result to take a closer look."
    $ pause(3)
    $ reset
    $ return_checkpoint(6)

label lyrics_retry:
    before toggle_outside_click(true)
    wizard "Go ahead, give it another try."
    $ wait_event(lyrics_search_started,lyrics_search_closed)
    if event == "lyrics_search_closed":
        jump search_closed
    wizard "Let's see..."
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
    wizard "Did you just... close the search? Okay, that's fine. You can always search again later."
    $ pause(2)
    $ reset
    $ return_checkpoint(6)

label lyrics_continue:
    wizard "No worries, you can always search again later."
    $ pause(2)
    $ reset
    $ return_checkpoint(6)

label window_moved:
    wizard "What are you doing? Get off me!"
    $ pause(2)
    wizard "Bro."
    $ pause(2)
    $ return checkpoint(7)

label youretakingtoolongbro:
    wizard "You still there? Take your time."
    $ pause(2)
    $ return checkpoint(7)

label ok:
    wizard "Fair enough."
    $ pause(2)
    $ return_checkpoint(6)
