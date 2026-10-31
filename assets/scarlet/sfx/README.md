# Scarlet Refrain sound effects

The WAV files in this folder were provided by the user. Their original source
and author credits have not been supplied; the files retain their upstream
rights and are not covered by the application's MIT license.

| File | Event |
| --- | --- |
| `bossattack1.wav` | Boss spiral bullet waves |
| `bossattack2.wav` | Later boss patterns and the bomb burst |
| `bossdamage.wav` | Player shots damaging the boss |
| `bossdeath.wav` | Defeating the boss by reducing HP to zero |
| `extendlife.wav` | Successful deathbomb: life saved |
| `graze.wav` | First graze of each enemy bullet |
| `itempickup.wav` | Collecting point items |
| `menuinteract.wav` | Dialogue, language, mute, pause/resume, and restart controls |
| `playerdeath.wav` | A committed death after the deathbomb window expires |
| `timeout.wav` | Final ten-second countdown and surviving to time-out |

There is no separate player-shot or bomb WAV in the supplied set. Bombs use
the stronger attack cue; firing itself stays silent until shots hit the boss.
No extra-life scoring rule has been added: the life cue acknowledges a save.

Effects are preloaded with bounded voice pools and per-sound rate limits.
Repeated collisions in a frame coalesce into one cue. Missing, loading, or
failed sounds are skipped without delaying gameplay. Pausing, hiding, closing,
and restarting stop active voices. **M** mutes both music and sound effects.
