# Flandre Extra encounter

`flandre.py` defines eighteen independently startable phases: eight alternating
non-spells and spells, followed directly by **And Then Will There Be None?**
and **Ripples of 495 Years**. `DanmakuGame` retains the existing player weapons,
warm-up, collisions, deathbomb windows, graze, drops, item magnet and scoring.
The existing dialogue, music handoff and victory conversation remain in use.

## Fidelity and references

This is a mechanical reconstruction of `assets/flandrescarletplan.md`, not a
frame-perfect port of the original ECL scripts. **Every numerical tuning value
is an approximation**, including HP, durations, bullet counts, velocities,
reflection limits, positions, intervals and movement paths. The exceptions are
the explicitly requested structural counts (three clones, eight yellow lanes,
seven green lanes, three blue rings, four clock lasers, four rainbow waves).
Lunatic is an optional difficulty variant of this game's reconstruction.

The original-game reference identified for calibration is
[raak1010's EoSD Extra boss recording](https://www.youtube.com/watch?v=s8JmfCYmtHo)
(2009), rather than the later *New Classic* remake. The N1/S1 through N8/S8,
then S9/S10 order was cross-checked against the explicit encounter list in
[the original-game danmaku archive](https://w.atwiki.jp/thydsh/pages/64.html)
(last updated in 2011). A video reference was located, but video inspection
and original game data were unavailable here. The archive's BMP screenshots
could not be retrieved for comparison. Detailed choreography has **not** been
independently verified against footage. No frame-by-frame comparison is claimed.

The non-spells use progressively denser rotating red rings and aimed blue fans;
their exact original choreography remains a fidelity limitation. The spell
mechanics are implemented as follows, with approximate geometry and scheduling:

| Spell | Implemented mechanic | Calibration still needed |
| --- | --- | --- |
| Cranberry Trap | Moving hidden side emitters, purple converging fans, blue snapshot-aimed volleys | Source paths, corridor density and volley offsets |
| Lævateinn | Boss-attached sweeping sword capsule, warning, separate persistent bullet trails, rear opening | Swing arcs, pauses, flame appearance and trail spacing |
| Four of a Kind | Three independently damageable clones, shared red rings, fixed eight-way yellow, aimed seven-way green, three staggered blue rings | Clone positions, HP, offsets and original movement |
| Kagome, Kagome | Inward square cages with gates; yellow events apply spatially weighted directional impulses to existing green bullets | Exact original grid topology and deformation function |
| Maze of Love | Deterministic paired spiral walls around the central boss, layered semicircles | Route direction, arm spacing, angular speed and secondary placement |
| Starbow Break | Seven ballistic color bands with an apex and descent, seeded launch jitter, repeating middle/right/right/middle offsets | Band curvature, launch positions and four-wave timing |
| Catadioptric | Up/right/left five-way spreads, five successive upward singles, boss movement and recoil; large parents reflect and emit smaller independent trails | Original boundary rules, bounce counts, descent and trail angles |
| Clock that Ticks Away the Past | Four quarter-offset rotating beam capsules sharing a laterally moving pivot, independently aimed red fans | Beam paths, extent, rotation direction and sequence |
| And Then Will There Be None? | Hidden moving sources chase player-relative offsets and leave expanding blue trails; later side-launched square lines turn independently and fold | Chase path, part split, fold topology and timing |
| Ripples of 495 Years | Seeded varying ring centers, expanding/reflected rings, bounded acceleration and faster emission with damage/time | Original center selection, boundary rules and escalation curve |

Reflection currently applies to the top and sides, with the bottom open.
The implementation mirrors boundary overshoot and reverses only the relevant
velocity component. Ballistic shots remain alive above the top until descent.
Finite lifetimes and reflection limits bound the number of active objects.

## Phase lifecycle and integration

`Phase` stores ID, both names, HP, timer, spell number, starting position,
completion policy and tuning. `FlandreEncounter` owns the attack scheduler,
seeded random generator, clones, lasers, emitters and capture results.
`DanmakuGame.boss_state` exposes entering, intro, active, non-spell,
transition and defeated states while keeping playing/paused separate.

Pattern events run on integer **60 Hz** ticks, independently of rendering.
Movement and collisions use the existing normalized delta-time simulation with
substeps no larger than 1/120 second. As before, a single update accepts at
most 0.1 seconds, so long application stalls don't advance unseen gameplay.
Projectiles use slotted data objects and separate simulation/rendering passes;
the existing engine has no object pool. Automated stress checks exercise the
whole duration of every phase on both difficulties with fewer than 1,800
simultaneous bullets, without discarding bullets to enforce a cap.

Clears stop the scheduler and remove clones and beam hazards immediately.
Remaining bullets become collectible stars. The existing four-second break and
smooth recentering finish before the next scheduler starts. Each projectile
records its phase ID; stale projectiles are discarded. Items, magnets and
player control continue during the break. Deathbomb/death resolution still
precedes a phase clear.

The survival spell hides the boss and disables damage, homing targeting and
boss contact. It clears only on timeout. All other cards accept shots and
bombs after the HP fill. Ordinary timeouts advance without a capture;
**Q.E.D. must be defeated by damage** and its timeout ends the run without
victory. After its damage clear, the collection break leads to the existing
outro and a single clear bonus.

`encounter.captures` records `(phase_id, captured)` results. Damage clears
without a death/bomb capture ordinary spells; clean survival captures S9.
The sidebar shows the capture count. Existing score values are preserved.
Bombs clear ordinary bullets, damage clones and temporarily suppress beam
collision; they do not become unavailable during specialized spells.

## Practice and tuning

From the repository root:

```powershell
.\Scripts\python.exe -m assets.scarlet.practice --spell 7 --seed 495 --invulnerable
.\Scripts\python.exe -m assets.scarlet.practice --phase nonspell_4 --difficulty Lunatic
.\Scripts\python.exe -m assets.scarlet.practice --spell 9 --slow
```

This development launcher does not save high scores. F5 restarts the current
phase, F6 advances, F7 toggles invulnerability and F8 toggles quarter speed.
Its overlay shows state, phase title, timer, HP, projectile count, seed,
invulnerability and speed. Existing movement/shoot/bomb/pause keys still work.
R restarts the whole warm-up. Ordinary game windows do not handle these debug
keys or show the overlay.

For programmatic practice, use `game.start_phase("kagome", seed=495)` or a
zero-based sequence index. This resets player position/resources and the
phase's hazards and RNG. Set `debug=True` on construction to enable
`debug_command("restart" | "next" | "invulnerable" | "slow")`.

Tune `SPELLS` and `PHASES` in `flandre.py`: speeds and geometry use playfield
pixels/second and pixels; intervals use 60 Hz pattern frames; angles use
radians; phase limits use seconds. Boss geometry uses the existing 384 × 448
playfield. Laser rendering and hit tests consume the same segment and width.

## Verification

`tests/test_flandre_encounter.py` checks all phase activations and full-duration
termination on both difficulties, shot/bomb damage, survival immunity, seeded
repeatability at 30/60 rendering updates, beam collision and deathbombs,
clone destruction, cage deformation, ballistic apex/descent, reflection,
trail creation, capture rules, transitions, cleanup, practice controls, final
timeout failure and final damage victory. All ten phases are also rendered
through the real Qt scene. Existing Scarlet tests cover retained gameplay,
story, UI, sound and stage music.

Rendered frames were inspected locally for all ten spells. This confirms
presentation and distinct formations, not original-game fidelity over time.
The next fidelity pass should compare recordings of practice runs to the
original reference and adjust the parameters/functions identified above.
