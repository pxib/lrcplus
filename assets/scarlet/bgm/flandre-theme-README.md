# Scarlet Refrain stage and boss themes

`extrastagetheme.mid` and `flandre_theme.MID` were provided by the user for
the hidden stage and boss fight. Both original files are preserved; their MIDI arrangement/source credits have not
been supplied. The music retains its upstream rights.

On Windows, the game uses its own MCI MIDI sequencer and the system MIDI
synthesizer. No extra package, audio conversion, or downloaded soundfont is
required. The extra-stage theme plays and loops throughout the warm-up,
Flandre's entrance, and the opening dialogue. It stops at the supplied boss-theme
cue (before "I have a play toy over here..."). The boss theme plays through
the rest of the introductory dialogue, combat, and the post-fight conversation,
looping at its end. It stops when the post-fight conversation finishes or is
skipped, the game restarts, the player loses, or the window is hidden or closed.
Press **M** to mute/unmute
both music and sound effects without losing the current music position.

The sequencer silently opens the stage MIDI when the game loads, ahead of the
warm-up. The stage theme keeps playing until the boss-theme cue, when the
sequencer switches tracks and begins the boss MIDI. Only one sequencer is open
at a time. Restarting returns to the stage theme; hiding or closing releases the
current device even if playback never started. Pause and mute affect either track.

This sequencer is independent of the main music player's media and settings.
Playback failures leave the game playable and show a Japanese BGM status.

References:
https://learn.microsoft.com/en-us/windows/win32/multimedia/midi-sequencer-command-set
https://learn.microsoft.com/en-us/windows/win32/multimedia/open
