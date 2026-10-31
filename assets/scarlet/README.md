# Scarlet Refrain

The hidden Touhou-inspired boss fight, opened by clicking the About logo
seven times.

- `danmaku.py`: gameplay simulation, collisions, projectiles, and point items.
- `danmaku_game.py`: game window, input, animation, and game/music lifecycle.
- `danmaku_scene.py`: Japanese dialogue, portraits, playfield, and HUD.
- `boss_music.py`: independent Windows MIDI playback.
- `sound_effects.py`: preloaded WAV effects, bounded voices, and rate limits.
- `dialogue.py`: the user-supplied Japanese/English pre- and post-fight script.
- `char/`: character sprites and source notices.
- `char/port/`: dialogue portraits and generation prompts.
- `bgm/`: MIDI theme and its source notice.
- `sfx/`: user-provided sound effects and their event mapping.

All game resources are resolved relative to `assets/scarlet/` through the
package's directory constants. The application build includes this folder
through its existing `assets/` data entry.
The shared Japanese font and app settings remain in the main application.

Tests are in `tests/test_about_game.py`, `tests/test_boss_music.py`, and
`tests/test_deathbomb.py`, plus `tests/test_scarlet_sfx.py`.

Flandre descends from above before the introductory conversation. Press **Z**
or **Space** to advance, **Esc** to skip a conversation, and **L** to switch
between Japanese (the default) and English. The theme starts at the script's
marked BGM cue. Clearing the fight begins the post-fight conversation.
**M** mutes/unmutes both the music and sound effects.

Press **X** within **100 ms** of a hit to deathbomb: one bomb saves the life,
clears danmaku, and grants the normal bomb invulnerability. Point drops and
respawning happen only when the window expires. Without bombs, hits resolve
immediately. Pausing freezes the deathbomb window; holding X does not auto-bomb.
