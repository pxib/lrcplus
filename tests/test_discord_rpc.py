from enum import IntEnum
from types import SimpleNamespace

import pytest

from plugins.community.discord_rpc import plugin as rpc_module
from plugins.community.discord_rpc.plugin import DEFAULT_SETTINGS, DiscordRpcPlugin


class ActivityType(IntEnum):
    PLAYING = 0
    LISTENING = 2
    WATCHING = 3
    COMPETING = 5


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(DiscordRpcPlugin, "_load_persistent_cover_cache", lambda self: {})
    monkeypatch.setattr(rpc_module, "ActivityType", ActivityType)
    instance = DiscordRpcPlugin()
    path = tmp_path / "track.mp3"
    path.touch()
    instance.player_state = 1
    player = SimpleNamespace(
        PlaybackState=SimpleNamespace(PlayingState=1, PausedState=2, StoppedState=0),
        playbackState=lambda: instance.player_state,
        position=lambda: 30_000,
        duration=lambda: 180_000,
    )
    instance.window = SimpleNamespace(current_audio_path=path, media_player=player)
    instance.current_path = path
    instance.metadata = {"title": "A song", "artist": "An artist", "album": "An album"}
    monkeypatch.setattr(instance, "_local_metadata", lambda path: instance.metadata)
    monkeypatch.setattr(instance, "_cached_cover", lambda path: "https://example.com/cover.jpg")
    instance.tasks = []
    monkeypatch.setattr(instance, "_schedule_rpc_task", lambda *task: instance.tasks.append(task))
    return instance


def payload(plugin):
    plugin.update_presence(force=True)
    action, (values, _) = plugin.tasks[-1]
    assert action == "update"
    return values


def test_existing_settings_keep_default_layout_and_progress(plugin, monkeypatch):
    monkeypatch.setattr(rpc_module.time, "time", lambda: 1000)
    values = payload(plugin)
    assert values == {
        "details": "A song", "state": "An album",
        "large_text": "An artist", "large_image": "https://example.com/cover.jpg",
        "activity_type": ActivityType.LISTENING, "start": 970, "end": 1150,
    }
    plugin._current_rpc_lyric = "A lyric"
    assert payload(plugin)["state"] == "An album | A lyric"


def test_templates_use_metadata_and_status_without_evaluating_or_recursing(plugin):
    plugin.metadata["title"] = "{artist}"
    plugin._current_rpc_lyric = "<b>Lyric</b>"
    plugin.settings.update(
        details_template="{artist} - {title}",
        state_template="{status}: {lyric} {unknown} {title.__class__}",
        large_text_template="{album}", activity_type="watching",
    )
    values = payload(plugin)
    assert values["details"] == "An artist - {artist}"
    assert values["state"] == "Playing: <b>Lyric</b> {unknown} {title.__class__}"
    assert values["large_text"] == "An album"
    assert values["activity_type"] == ActivityType.WATCHING


def test_disabled_lyrics_are_excluded_from_every_template(plugin):
    plugin._current_rpc_lyric = "Private lyric"
    plugin.settings.update(include_lyrics=False, details_template="Track{lyric_suffix}",
                           large_text_template="Artist {lyric}")
    values = payload(plugin)
    assert values["details"] == "Track"
    assert values["state"] == "An album"
    assert values["large_text"] == "Artist"
    assert "Private lyric" not in str(values)


def test_empty_fields_are_omitted_and_long_metadata_is_bounded(plugin):
    plugin.metadata["title"] = "歌" * 200
    plugin.settings.update(state_template=" \n ", large_text_template="")
    values = payload(plugin)
    assert values["details"] == "歌" * 128
    assert "state" not in values
    assert "large_text" not in values


def test_missing_metadata_and_artwork_are_handled(plugin, monkeypatch):
    plugin.metadata.update(artist=None, album=None)
    monkeypatch.setattr(plugin, "_cached_cover", lambda path: None)
    monkeypatch.setattr(plugin, "_start_cover_resolution", lambda *args: None)
    values = payload(plugin)
    assert values["state"] == "Unknown artist"
    assert "large_image" not in values
    assert "large_text" not in values


def test_single_character_templates_meet_discord_minimum_length(plugin):
    plugin.settings.update(details_template="歌", state_template="♪")
    values = payload(plugin)
    assert values["details"] == "歌\u200b"
    assert values["state"] == "♪\u200b"


def test_changing_tracks_discards_previous_lyrics(plugin, tmp_path):
    plugin._current_rpc_lyric = "Previous track's lyric"
    next_track = tmp_path / "next.mp3"
    next_track.touch()
    plugin.window.current_audio_path = next_track
    assert payload(plugin)["state"] == "An album"
    assert plugin._current_rpc_lyric is None


@pytest.mark.parametrize("state", [2, 0])
def test_hidden_paused_or_stopped_activity_returns_on_resume(plugin, state):
    plugin.settings["show_when_paused"] = False
    plugin.player_state = state
    plugin.update_presence(force=True)
    assert plugin.tasks[-1] == ("clear",)
    plugin.player_state = 1
    assert payload(plugin)["details"] == "A song"


def test_progress_can_be_hidden_and_is_always_absent_while_paused(plugin):
    plugin.settings["show_timestamps"] = False
    assert "start" not in payload(plugin)
    assert "end" not in payload(plugin)
    plugin.settings.update(show_timestamps=True, state_template="{status}")
    plugin.player_state = 2
    values = payload(plugin)
    assert values["state"] == "Paused"
    assert "start" not in values
    assert "end" not in values


def load_settings(plugin, stored):
    api = SimpleNamespace(
        get_setting=lambda key, default: stored.get(key, default),
        set_setting=lambda key, value: stored.__setitem__(key, value),
        register_settings_widget=lambda *args, **kwargs: None,
    )
    plugin.on_load(SimpleNamespace(api=api, on=lambda *args: None))


def test_older_and_malformed_settings_fall_back_to_defaults(plugin):
    load_settings(plugin, {"include_lyrics": False, "activity_type": [],
                           "details_template": None, "lyric_lines_per_update": "bad"})
    assert plugin.settings["include_lyrics"] is False
    assert plugin.settings["details_template"] == "{title}"
    assert plugin.settings["activity_type"] == "listening"
    assert plugin.settings["lyric_lines_per_update"] == 10


def test_settings_preview_save_reload_and_reset(qapp, plugin, monkeypatch):
    stored = {}
    load_settings(plugin, stored)
    monkeypatch.setattr(plugin, "_start_cover_resolution", lambda *args: pytest.fail("Network artwork in preview"))
    widget = plugin.create_settings_widget(None)
    try:
        widget.details_template.setText("{artist} / {title}")
        widget.state_template.setText("{status}")
        widget.activity_type.setCurrentIndex(widget.activity_type.findData("competing"))
        widget.show_timestamps.setChecked(False)
        widget.show_when_paused.setChecked(False)
        widget.include_lyrics.setChecked(False)
        widget.lyric_lines_per_update.setValue(4)
        widget.lookup_online_artwork.setChecked(False)
        widget.upload_embedded_artwork.setChecked(False)
        assert "An artist / A song" in widget.text_preview.text()
        assert "Competing in LyricsPlus" in widget.text_preview.text()
        assert stored == {}
        assert plugin.tasks == []
        plugin.apply_settings_widget(widget)
        assert stored["details_template"] == "{artist} / {title}"
        assert stored["activity_type"] == "competing"
        assert stored["lyric_lines_per_update"] == 4
        assert plugin.tasks[-1][0] == "update"
        load_settings(plugin, stored)
        assert payload(plugin)["activity_type"] == ActivityType.COMPETING
        assert "start" not in payload(plugin)
        widget.restore_defaults.click()
        # Reset changes the form; persistence still requires Save.
        assert stored["activity_type"] == "competing"
        plugin.apply_settings_widget(widget)
        assert stored == DEFAULT_SETTINGS
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_preview_without_a_track_uses_sample_text(qapp, plugin):
    plugin.window = None
    widget = plugin.create_settings_widget(None)
    try:
        assert "Song title" in widget.text_preview.text()
        assert "Album | Current lyric line" in widget.text_preview.text()
        widget.include_lyrics.setChecked(False)
        assert "Current lyric line" not in widget.text_preview.text()
    finally:
        widget.deleteLater()
        qapp.processEvents()
