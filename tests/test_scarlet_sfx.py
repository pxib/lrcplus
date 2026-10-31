from pathlib import Path

import pytest
from PySide6.QtMultimedia import QSoundEffect

from assets.scarlet import SFX_DIR
from assets.scarlet.danmaku import Bullet, DanmakuGame, PointItem
from assets.scarlet.sound_effects import SOUNDS, SoundEffects


def test_sound_bank_loads_each_supplied_wav_once_per_bounded_voice(qapp, fake_sound_effects):
    sounds = SoundEffects(effect_factory=fake_sound_effects.factory)
    try:
        assert set(sounds.effects) == set(SOUNDS)
        assert len(fake_sound_effects.voices) == sum(spec[2] for spec in SOUNDS.values())
        for voice in fake_sound_effects.voices:
            assert Path(voice.source.toLocalFile()).is_file()
            assert 0 < voice.volume < 1
            assert voice.loops == 1
    finally:
        sounds.stop()
        sounds.deleteLater()


def test_rapid_hits_are_rate_limited_and_do_not_allocate_or_restart_voices(qapp, fake_sound_effects):
    now = [0.0]
    sounds = SoundEffects(effect_factory=fake_sound_effects.factory, clock=lambda: now[0])
    try:
        count = len(fake_sound_effects.voices)
        for _ in range(50):
            sounds.play_many(["bossdamage"] * 5)
        assert fake_sound_effects.played == ["bossdamage"]
        now[0] = .1
        sounds.play("bossdamage")
        assert fake_sound_effects.played == ["bossdamage", "bossdamage"]
        now[0] = .2
        sounds.play("bossdamage")
        assert len(fake_sound_effects.played) == 2  # Both voices are still busy.
        assert len(fake_sound_effects.voices) == count
    finally:
        sounds.stop()
        sounds.deleteLater()


def test_mute_stops_every_voice_and_repeated_sync_is_idle(qapp, fake_sound_effects):
    sounds = SoundEffects(effect_factory=fake_sound_effects.factory)
    try:
        sounds.play("bossattack1")
        assert any(voice.playing for voice in fake_sound_effects.voices)
        sounds.set_muted(True)
        stops = sum(voice.stops for voice in fake_sound_effects.voices)
        sounds.set_muted(True)
        sounds.play("playerdeath")
        assert not any(voice.playing for voice in fake_sound_effects.voices)
        assert sum(voice.stops for voice in fake_sound_effects.voices) == stops
        sounds.set_muted(False)
        sounds.play("itempickup")
        assert fake_sound_effects.played[-1] == "itempickup"
    finally:
        sounds.stop()
        sounds.deleteLater()


def test_loading_or_failed_effects_are_not_queued_for_later(qapp, fake_sound_effects):
    sounds = SoundEffects(effect_factory=fake_sound_effects.factory)
    try:
        for voice in sounds.effects["bossdeath"]:
            voice.current_status = QSoundEffect.Status.Loading
        sounds.play("bossdeath")
        assert not fake_sound_effects.played
        for voice in sounds.effects["bossdeath"]:
            voice.current_status = QSoundEffect.Status.Error
        sounds.play("bossdeath")
        assert not fake_sound_effects.played
    finally:
        sounds.deleteLater()


def test_missing_sfx_folder_is_nonfatal(qapp, fake_sound_effects, tmp_path):
    sounds = SoundEffects(directory=tmp_path, effect_factory=fake_sound_effects.factory)
    try:
        sounds.play_many(SOUNDS)
        assert not sounds.effects
        assert not fake_sound_effects.played
    finally:
        sounds.deleteLater()


def test_boss_death_has_priority_over_attack_and_damage_cues(qapp, fake_sound_effects):
    sounds = SoundEffects(effect_factory=fake_sound_effects.factory)
    try:
        sounds.play_many(["bossdamage", "bossattack1", "bossdeath", "bossdeath"])
        assert fake_sound_effects.played == ["bossdeath"]
    finally:
        sounds.stop()
        sounds.deleteLater()


def test_grazes_pickups_and_boss_hits_emit_one_cue_per_event_batch():
    game = DanmakuGame()
    game.start_or_pause()
    game._spawn_in = 100
    bx, by = game.boss
    game.shots = [Bullet(bx, by, 0, 0), Bullet(bx, by, 0, 0)]
    game.items = [PointItem(game.x, game.y), PointItem(game.x, game.y)]
    game.bullets = [Bullet(game.x + 18, game.y, 0, 0)]
    game.update(1 / 120)
    assert set(game.take_sound_events()) == {"bossdamage", "itempickup", "graze"}
    assert game.take_sound_events() == []
    game.update(1 / 120)
    assert game.take_sound_events() == []  # Same bullet cannot graze twice.


def test_player_death_waits_for_deathbomb_expiry_and_success_plays_life_cue():
    game = DanmakuGame()
    game.start_or_pause()
    game.bullets = [Bullet(game.x, game.y, 0, 0)]
    game.update(1 / 120)
    assert "playerdeath" not in game.take_sound_events()
    game.bomb()
    assert set(game.take_sound_events()) == {"extendlife", "bossattack2"}
    game.reset()
    game.start_or_pause()
    game.bullets = [Bullet(game.x, game.y, 0, 0)]
    game.update(1 / 120)
    game.update(game.DEATHBOMB_WINDOW)
    assert game.take_sound_events() == ["playerdeath"]


def test_boss_wave_sounds_follow_pattern_and_clear_reason():
    game = DanmakuGame()
    game.start_or_pause()
    game._spawn()
    assert game.take_sound_events() == ["bossattack1"]
    game.elapsed = 25
    game._spawn()
    assert game.take_sound_events() == ["bossattack2"]
    game.hp = 0
    game._check_win()
    assert game.take_sound_events() == ["bossdeath"]
    game._check_win()
    assert game.take_sound_events() == []


def test_countdown_warns_once_per_second_and_freezes_when_paused():
    game = DanmakuGame()
    game.start_or_pause()
    game._spawn_in = 100
    game.elapsed = game.DURATION - 10.01
    game.update(.02)
    assert game.take_sound_events() == ["timeout"]
    game.update(.1)
    assert game.take_sound_events() == []
    game.pause()
    game.update(.1)
    assert game.take_sound_events() == []
    game.start_or_pause()
    game.elapsed = game.DURATION - .01
    game.update(.02)
    assert game.take_sound_events() == ["timeout"]
    assert game.state == "won"
