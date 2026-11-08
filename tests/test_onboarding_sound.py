"""Bundled reactions use their own channel and never block script execution."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core import settings
from core.onboarding_script import compile_script, load_script
from ui.onboarding import OnboardingDialog, parse_dialogue_line
from ui.onboarding_sound import OnboardingSounds, SOUND_DIR, resolve_sound


@pytest.fixture(autouse=True)
def fake_audio(monkeypatch, qapp):
    players, outputs = [], []

    class Signal:
        def connect(self, callback):
            self.callback = callback

        def emit(self, *args):
            self.callback(*args)

    class Player:
        def __init__(self, parent):
            self.sources = []
            self.stops = 0
            self.plays = 0
            self.errorOccurred = Signal()
            players.append(self)

        def setAudioOutput(self, output):
            self.output = output

        def setSource(self, source):
            self.sources.append(source)

        def stop(self):
            self.stops += 1

        def play(self):
            self.plays += 1

    class Output:
        def __init__(self, parent):
            outputs.append(self)

        def setVolume(self, volume):
            self.volume = volume

    monkeypatch.setattr("ui.onboarding_sound.make_media_player", Player)
    monkeypatch.setattr("ui.onboarding_sound.make_audio_output", Output)
    return SimpleNamespace(players=players, outputs=outputs)


@pytest.fixture
def wizard(qapp):
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.auto_read_check.setChecked(False)
    yield dialog
    dialog.close()
    qapp.processEvents()


def play_scene(dialog, source):
    dialog._cancel_dialogue_activity()
    dialog.dialogue_lines = compile_script(source)["scene"]
    dialog.dialogue_index = -1
    dialog.advance_dialogue()


def test_all_bundled_formats_and_stems_resolve():
    files = list(SOUND_DIR.iterdir())
    assert files
    for path in files:
        if path.is_file():
            assert resolve_sound(path.name) == path.resolve()
            assert resolve_sound(path.stem) == path.resolve()


@pytest.mark.parametrize("name", ["", "../adore.ogg", "sub/adore.ogg", "sub\\adore.ogg",
                                      "https://example.com/a.mp3", "C:/a.mp3", "adore.exe"])
def test_sound_names_cannot_escape_bundle(name):
    assert resolve_sound(name) is None
    with pytest.raises(ValueError, match="line 2"):
        import json
        compile_script('label scene:\n    before sound(' + json.dumps(name) + ')\n    wizard "Hi"\n')


def test_playback_is_lazy_reuses_one_channel_and_does_not_touch_music(fake_audio):
    sounds = OnboardingSounds()
    assert fake_audio.players == []
    assert sounds.play("adore")
    assert sounds.play("interesting.ogg")
    assert len(fake_audio.players) == len(fake_audio.outputs) == 1
    player = fake_audio.players[0]
    assert player.plays == 2
    assert player.stops == 2
    assert fake_audio.outputs[0].volume == 0.45
    assert [Path(source.toLocalFile()).name for source in player.sources] == ["adore.ogg", "interesting.ogg"]
    sounds.close()
    assert player.sources[-1].isEmpty()


def test_missing_file_does_not_initialize_audio_or_raise(fake_audio, caplog):
    sounds = OnboardingSounds()
    assert not sounds.play("not_bundled")
    assert not fake_audio.players
    assert "missing or invalid" in caplog.text


def test_muting_stops_current_clip_and_does_not_replay_on_unmute(fake_audio):
    sounds = OnboardingSounds(enabled=False)
    assert not sounds.play("adore")
    assert not fake_audio.players
    sounds.set_enabled(True)
    assert sounds.play("adore")
    player = sounds.player
    stops = player.stops
    sounds.set_enabled(False)
    assert player.stops > stops
    assert not sounds.play("idea")
    sounds.set_enabled(True)
    assert player.plays == 1
    sounds.close()
    assert not sounds.play("idea")


def test_audio_errors_are_logged_without_interrupting_script(wizard, caplog):
    wizard.sounds.player.errorOccurred.emit(1, "Codec unavailable")
    assert "Codec unavailable" in caplog.text
    wizard.finish_typing()
    assert wizard.description.text() == "Youkoso to LRCPlus!"


@pytest.mark.parametrize("sound_statement", ['$ sound("idea.ogg")', 'before sound("idea.ogg")'])
def test_sound_cues_keep_pause_and_text_order(wizard, sound_statement):
    player = wizard.sounds.player
    plays = player.plays
    play_scene(wizard, f'''label scene:
    before sound("playful")
    wizard "Wait."
    $ pause(0.01)
    {sound_statement}
    extend " Done."
    $ end
''')
    assert player.plays == plays + 1
    assert Path(player.sources[-1].toLocalFile()).stem == "playful"
    wizard.finish_typing()
    assert player.plays == plays + 1
    QTest.qWait(30)
    assert player.plays == plays + 2
    assert Path(player.sources[-1].toLocalFile()).stem == "idea"
    wizard.finish_typing()
    assert wizard.description.text() == "Wait. Done."
    assert wizard.next.isEnabled()


def test_renpy_play_and_stop_forms_are_actions_not_displayed_text(wizard):
    player = wizard.sounds.player
    plays = player.plays
    play_scene(wizard, '''label scene:
    play sound "interesting.ogg"
    wizard "Reaction."
    stop sound
    $ end
''')
    assert wizard.full_text == "Reaction."
    assert player.plays == plays + 1
    stops = player.stops
    wizard.finish_typing()
    assert player.stops > stops
    assert wizard.next.isEnabled()


@pytest.mark.parametrize("transition", ["route", "page", "close", "debug"])
def test_scene_changes_stop_old_audio(wizard, monkeypatch, transition):
    player = wizard.sounds.player
    stops = player.stops
    if transition == "route":
        monkeypatch.setattr("ui.onboarding.load_route", lambda name: [("Guide", r"Next.\n")])
        wizard.select_route("next")
    elif transition == "page":
        wizard.set_page(2)
    elif transition == "close":
        wizard.close()
        assert player.sources[-1].isEmpty()
    else:
        wizard.debug_cancel_activity()
    assert player.stops > stops


def test_sound_toggle_is_keyboard_accessible_and_remembered(wizard, qapp):
    wizard.activateWindow()
    wizard.sound_check.setFocus()
    qapp.processEvents()
    QTest.keyClick(wizard.sound_check, Qt.Key.Key_Space)
    assert not wizard.sound_check.isChecked()
    assert wizard.typing_timer.isActive()
    assert not wizard.sounds.enabled
    wizard.close()
    assert settings.get_onboarding_pacing()["sound_enabled"] is False
    reopened = OnboardingDialog(wizard.player)
    try:
        assert not reopened.sound_check.isChecked()
        assert not reopened.sounds.enabled
        assert reopened.sounds.player is None
    finally:
        reopened.close()


def test_authored_sound_cues_reference_real_assets():
    from core.onboarding_state import decode_payload

    cues = []
    pending = list(load_script().values())
    while pending:
        lines = pending.pop()
        for _, text in lines:
            for command in parse_dialogue_line(text)[1]:
                if command.startswith(r"\sound("):
                    name = decode_payload(command[7:-1])
                    cues.append(name)
                    assert resolve_sound(name) is not None
                elif command.startswith(r"\random("):
                    pending.extend(decode_payload(command[8:-1]))
                elif command.startswith((r"\choose(", r"\choices(")):
                    offset = 8 if command.startswith(r"\choose(") else 9
                    pending.extend(arm["lines"] for arm in decode_payload(command[offset:-1]))
    assert cues


@pytest.mark.parametrize("enabled", [True, False])
def test_expression_follows_each_line_even_when_sound_is_muted(wizard, qapp, enabled):
    wizard.sound_check.setChecked(enabled)
    play_scene(wizard, '''label scene:
    before sound("thinking.ogg")
    wizard "First."
    wizard "Neutral."
    before sound("confused")
    wizard "Third."
    $ end
''')
    wizard.finish_typing()
    qapp.processEvents()
    assert wizard.expression_label.text() == "Thinking..."
    assert wizard.expression_label.isVisible()
    position = wizard.title.pos()
    wizard.advance_dialogue()
    qapp.processEvents()
    assert wizard.expression_label.text() == ""
    assert not wizard.expression_label.isVisible()
    assert wizard.title.pos() == position
    wizard.finish_typing()
    wizard.advance_dialogue()
    assert wizard.expression_label.text() == "Confused"
    assert wizard.expression_label.isVisible()
    wizard.set_page(2)
    assert not wizard.expression_label.isVisible()


def test_expression_changes_when_inline_sound_reaches_extended_text(wizard):
    play_scene(wizard, '''label scene:
    before sound("playful")
    wizard "Wait."
    $ pause(0.01)
    before sound("idea")
    extend " More."
    $ next
''')
    assert wizard.expression_label.text() == "Playful"
    wizard.finish_typing()
    assert wizard.expression_label.text() == "Playful"
    QTest.qWait(30)
    assert wizard.expression_label.text() == "Inspired"
    wizard.finish_typing()
    assert wizard.expression_label.text() == "Inspired"
    wizard.advance_dialogue()
    assert not wizard.expression_label.isVisible()


def test_helper_expression_is_not_left_on_caller_continuation(wizard, monkeypatch):
    labels = compile_script('''label scene:
    before sound("thinking")
    wizard "Caller"
    $ call helper
    extend " again."
    $ next
label helper:
    before sound("playful")
    wizard "Helper."
    return
''')
    monkeypatch.setattr("ui.onboarding.load_route", lambda name: labels[name])
    wizard.text_speed_combo.setCurrentIndex(wizard.text_speed_combo.findData("instant"))
    wizard.select_route("scene")
    assert wizard.expression_label.text() == "Playful"
    wizard.advance_dialogue()
    assert wizard.description.text() == "Caller again."
    assert not wizard.expression_label.isVisible()


def test_explicit_stop_clears_expression_but_mute_does_not(wizard):
    assert wizard.expression_label.text() == "Curious"
    wizard.sound_check.setChecked(False)
    assert wizard.expression_label.text() == "Curious"
    wizard.typing_timer.stop()
    wizard.script_commands = [r"\soundstop"]
    wizard.tokens = []
    wizard.run_script_command()
    assert not wizard.expression_label.isVisible()
