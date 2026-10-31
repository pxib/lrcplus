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

Tests are in `tests/test_about_game.py`, `tests/test_boss_music.py`,
`tests/test_deathbomb.py`, `tests/test_scarlet_sfx.py`, and
`tests/test_scarlet_phases.py`, `tests/test_scarlet_weapons.py`, and
`tests/test_scarlet_collection.py`.

The game starts at **0 power** with a warm-up lasting up to **42 seconds**.
Twelve pairs of small enemies drop red **P** items (+8 power) and blue point
items when defeated. Shoot with **Z** and collect their drops; power, score,
lives, and bombs carry into the boss fight. **Esc** skips to the boss fight.
There are 192 power points available, enough to reach 90+ by collecting half
the P drops or MAX by collecting two thirds. After 36 seconds, the warm-up can
end early if power is at least 90 and no enemies or drops remain. Power must
be earned by collecting drops; the transition does not grant it automatically.
The enemies use the three fairy rows in
`img/transparent/cirno-effects.png`, cycling rows between waves. Their four idle frames
loop at 10 fps, at the original 32 × 32 size with transparency and no smoothing.
Animation follows each enemy's active gameplay time, so pausing freezes it.
After the warm-up, Flandre descends from above before the introductory conversation. Press **Z**
or **Space** to advance, **Esc** to skip a conversation, and **L** to switch
between Japanese (the default) and English. The theme starts at the script's
marked BGM cue. Clearing the fight begins the post-fight conversation.
**M** mutes/unmutes both the music and sound effects.

Press **X** shortly after a hit to deathbomb: one bomb saves the life,
clears danmaku, and grants the normal bomb invulnerability. Successive saves
shorten the window from **100 → 75 → 50 → 25 ms** (minimum 25 ms); losing a
life resets it. Ordinary bombs do not affect this streak. Holding X does not
auto-bomb. Bombs have a **2-second cooldown**, shown beside the Bomb stock;
pausing freezes it. Death clears the cooldown.

Above **10 power**, two red-and-white yin-yangs rotate in opposite directions
beside Reimu. **Shift** smoothly brings them together above her. Each option
fires **three main projectiles** per volley; at 10 power or below, no options
appear and the three-shot main volley comes from Reimu. Unfocused fire also
launches **five homing projectiles** from Reimu herself. The Power meter shows
the actual value (maximum 128); boss damage also drops P items to build power.

Items fall straight down at **65 pixels/second**. Death drops first scatter
upward over 0.25 seconds, then use the same constant fall speed, without gravity
or bouncing. Point pickups award **1,000 points**, with the amount rising
vertically at constant speed for 0.8 seconds before disappearing.

At **MAX power (128)**, moving into the top quarter of the playfield (112 pixels
from the top) attracts every on-screen item toward Reimu at 480 pixels/second.
The collection line is shown at MAX power. Once attracted, items continue
following her when she moves back down. Death cancels ordinary item attraction;
pausing freezes item movement.

When the rescue window expires (or immediately without bombs), Reimu drops
points and spends **0.45 seconds** flattening, stretching horizontally, and
fading at the hit position. She cannot move, shoot, collect, graze, or bomb
during this animation. Existing danmaku keeps moving. Respawn and its 2.5-second
invulnerability begin after the animation; the last life instead ends the game.
Pausing freezes both the animation and the deathbomb window.

Flandre has **three separate 360-HP bars**, shown together at the top of the
playfield. Emptying a bar or surviving its **25-second** timer advances to a
fresh bar and a harder pattern; only the third phase ends the fight. Every
clear starts a **4-second break** with no boss attacks or contact damage.
Flandre eases back to the upper center over the first 1.2 seconds. All remaining
enemy bullets become gold **star items**, each worth 100 points, that fly toward
Reimu at any power level. A lethal bomb preserves these star rewards too.
Player projectiles are discarded, the timer and wave reset for the next bar,
and the music continues. Pausing freezes the break and recentering. The final
bar gets the same collection break before the post-fight dialogue and clear
bonus; stars do not increment the blue point-item count.
Closely timed star pickups share one accumulating score popup to keep large
bullet clears readable; the popup continues rising at the normal constant speed.

After the break, each new bar fills **from left to right over 1.2 seconds**. Shots and bombs
cannot damage Flandre during the fill or before her first volley; she starts
attacking while the bar fills. The 25-second phase timer begins after the fill.
Life loss itself preserves danmaku. A pending hit or death animation
resolves before a phase change or victory.
