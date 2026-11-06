import math

import pytest
from mutagen.id3 import TXXX

from core.replaygain import ReplayGain, parse_replaygain, read_replaygain
from core.settings import get_playback_settings, save_playback_settings


@pytest.mark.parametrize("tags", [
    {"REPLAYGAIN_TRACK_GAIN": ["-6.0 dB"], "REPLAYGAIN_TRACK_PEAK": ["0.9"]},
    {"TXXX:replaygain_track_gain": TXXX(text=["-6 dB"]),
     "TXXX:REPLAYGAIN_TRACK_PEAK": TXXX(text=["0.9"])},
    {"----:com.apple.iTunes:replaygain_track_gain": [b"-6.0 dB"],
     "----:com.apple.iTunes:replaygain_track_peak": [b"0.9"]},
])
def test_reads_vorbis_id3_and_mp4_tags(tags):
    result = parse_replaygain(tags)
    assert result.track_gain == -6
    assert result.track_peak == 0.9
    assert result.multiplier("track") == pytest.approx(10 ** (-6 / 20))


def test_album_falls_back_to_track_with_matching_peak():
    tags = ReplayGain(track_gain=6, track_peak=0.8, album_peak=1.5)
    assert tags.multiplier("album") == 1.25
    tags = ReplayGain(track_gain=6, track_peak=0.8, album_gain=3, album_peak=1.5)
    assert tags.multiplier("album") == pytest.approx(1 / 1.5)


def test_preamp_and_peak_clipping_limit():
    tags = ReplayGain(track_gain=6, track_peak=1)
    assert tags.multiplier("track", 6, True) == 1
    assert tags.multiplier("track", 6, False) == pytest.approx(10 ** (12 / 20))
    assert tags.multiplier("off", 6, False) == 1


@pytest.mark.parametrize("value", ["NaN", "inf", "-inf", [], "broken", None])
def test_malformed_tags_leave_untagged_files_unchanged(value):
    tags = parse_replaygain({"replaygain_track_gain": value})
    assert tags.multiplier("track", 24) == 1
    assert math.isfinite(tags.multiplier("album"))


def test_missing_file_is_unity(tmp_path):
    assert read_replaygain(tmp_path / "missing.mp3") == ReplayGain()


def test_preferences_round_trip_and_validation():
    save_playback_settings({"gapless_playback": True, "replaygain_mode": "album",
                            "replaygain_preamp": -3, "replaygain_prevent_clipping": False})
    values = get_playback_settings()
    assert values["gapless_playback"] is True
    assert values["replaygain_mode"] == "album"
    assert values["replaygain_preamp"] == -3
    assert values["replaygain_prevent_clipping"] is False
    values = get_playback_settings({"playback": {"replaygain_mode": "bad", "replaygain_preamp": "nan"}})
    assert values["replaygain_mode"] == "off"
    assert values["replaygain_preamp"] == 0


def test_settings_controls_keep_speed_and_pitch(qapp):
    from ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog([], {}, playback_settings={
        "gapless_playback": True, "replaygain_mode": "track", "replaygain_preamp": 4,
        "speed_percent": 125, "pitch_linked_to_speed": True,
    })
    try:
        values = dialog.get_playback_settings()
        assert values["gapless_playback"] is True
        assert values["replaygain_mode"] == "track"
        assert values["replaygain_preamp"] == 4
        assert values["speed_percent"] == 125
        assert values["pitch_linked_to_speed"] is True
    finally:
        dialog.deleteLater()
        qapp.processEvents()
