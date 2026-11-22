import math
from queue import SimpleQueue
import shutil
import struct
from threading import Event
import time
import wave

import pytest
from mutagen.id3 import TXXX

from core.replaygain import ReplayGain, parse_replaygain, read_replaygain
from core.replaygain_scan import scan_replaygain
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


def _write_tone(path, amplitude):
    rate = 48000
    frames = [round(amplitude * 32767 * math.sin(2 * math.pi * index / 48)) for index in range(rate)]
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack(f"<{rate}h", *frames))


def test_scan_measures_track_and_album_gain_and_rejects_stale_cache(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is unavailable")
    quiet = tmp_path / "quiet.wav"
    loud = tmp_path / "loud.wav"
    _write_tone(quiet, 0.16)
    _write_tone(loud, 0.32)
    contents = quiet.read_bytes(), loud.read_bytes()
    scanned = scan_replaygain(ffmpeg, [(quiet, loud)], True, Event(), SimpleQueue())
    quiet_gain, loud_gain = (result.gain for result in scanned)
    assert quiet_gain.track_gain - loud_gain.track_gain == pytest.approx(6.02, abs=0.03)
    assert quiet_gain.track_peak == pytest.approx(0.16, abs=0.0001)
    assert quiet_gain.album_peak == loud_gain.album_peak == pytest.approx(0.32, abs=0.0001)
    assert loud_gain.track_gain < quiet_gain.album_gain < quiet_gain.track_gain
    assert quiet_gain.album_gain == loud_gain.album_gain
    assert read_replaygain(quiet) == quiet_gain
    assert read_replaygain(loud) == loud_gain
    assert (quiet.read_bytes(), loud.read_bytes()) == contents

    scan_replaygain(ffmpeg, [(quiet,)], False, Event(), SimpleQueue())
    assert read_replaygain(quiet) == quiet_gain
    loud.write_bytes(contents[1] + b"\0")
    assert read_replaygain(loud) == ReplayGain()


def test_album_scan_skips_unavailable_files_and_cancelled_results(monkeypatch, tmp_path):
    song = tmp_path / "track.wav"
    song.touch()
    missing = tmp_path / "missing.wav"
    cancelled = Event()

    def cancel_measurement(*_):
        cancelled.set()
        return [-20], 0.5

    monkeypatch.setattr("core.replaygain_scan._measure", cancel_measurement)
    assert scan_replaygain("ffmpeg", [(song,)], True, cancelled, SimpleQueue()) is None
    assert read_replaygain(song) == ReplayGain()

    monkeypatch.setattr("core.replaygain_scan._measure", lambda *_: ([-20], 0.5))
    results = scan_replaygain("ffmpeg", [(song, missing)], True, Event(), SimpleQueue())
    assert {result.path: result.status for result in results} == {song: "Album incomplete", missing: "File unavailable"}
    assert read_replaygain(song) == ReplayGain(track_gain=2, track_peak=0.5)


def test_library_replaygain_dialog_scans_full_album(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMenu
    from core.music_library import LibraryTrack
    from ui.main_window import Player

    files = [tmp_path / f"track-{index}.wav" for index in range(3)]
    for path in files:
        path.touch()
    monkeypatch.setattr("ui.queue_actions.shutil.which", lambda *_: "ffmpeg")
    monkeypatch.setattr("core.replaygain_scan._measure", lambda *_: ([-23], 0.25))
    window = Player()
    try:
        window.library_tracks = [LibraryTrack(str(path), path.stem, album="Album" if i < 2 else "Other")
                                 for i, path in enumerate(files)]
        window.current_audio_path = files[0]
        window.playback_settings["replaygain_mode"] = "album"
        window._populate_library_view()
        window.show()
        tree = window.library_table
        album = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                     if tree.topLevelItem(i).childCount() == 2)
        album.setExpanded(True)
        album.child(1).setHidden(True)
        qapp.processEvents()

        class ScanMenu(QMenu):
            def exec(self, _point):
                actions = self.actions()
                replaygain = next(action for action in actions if action.text() == "ReplayGain").menu()
                assert replaygain.actions()[0].text() == "Scan selected tracks…"
                replaygain.actions()[1].trigger()

        monkeypatch.setattr("ui.queue_actions.QMenu", ScanMenu)
        window._show_library_queue_menu(tree.visualItemRect(album.child(0)).center())
        dialog = window._replaygain_scan_dialog
        deadline = time.monotonic() + 5
        while dialog.close_button.text() == "Cancel" and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert dialog.status.text() == "Saved ReplayGain for 2 of 2 tracks."
        assert dialog.results.topLevelItem(0).text(2) == "+5.00 dB"
        assert read_replaygain(files[1]).album_gain == 5
        assert read_replaygain(files[2]) == ReplayGain()
        while window._replaygain_job._future is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert window._replaygain_multiplier() == pytest.approx(10 ** (5 / 20))
        dialog.close_button.click()
        assert window._replaygain_scan_dialog is None
    finally:
        window.close()
        window.deleteLater()
