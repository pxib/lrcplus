"""Lyrics offsets affect karaoke, while transport and pitch keep media time."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWidget

from lyrics.models import LyricLine, LyricSegment
from plugins.core.karaoke_screen.plugin import KaraokeScreenPlugin
from plugins.core.karaoke_screen.screen import KaraokeScreen


@pytest.fixture
def screen(qapp):
    plugin = KaraokeScreenPlugin()
    window = QWidget()
    plugin.window = window
    window.media_player = SimpleNamespace(position=lambda: 0)
    window.lyric_lines = [
        LyricLine(start, f"Line {index}",
                  [LyricSegment(start, f"Line {index}", end=start + 10)],
                  end=start + 10, has_karaoke=True)
        for index, start in enumerate((0, 10, 20, 30))
    ]
    plugin.screen = KaraokeScreen(window)
    plugin._position_anchor_monotonic = None
    plugin._screen_line_index = None
    plugin._screen_next_line_index = None
    plugin._screen_next_text = None
    plugin.context = SimpleNamespace(api=SimpleNamespace(
        get_current_lyrics=lambda: [
            {"text": line.text, "start_ms": int(line.start * 1000)}
            for line in window.lyric_lines
        ],
        get_position=lambda: window.media_player.position(),
        get_duration=lambda: 60000,
        get_current_track=lambda: "song.mp3",
    ))
    yield plugin
    # Child widgets do not receive closeEvent when their parent closes.
    # Stop their polling before another test replaces the reading cache.
    plugin.screen.current_lyric.stop_workers()
    plugin.screen.next_lyric.stop_workers()
    plugin.screen.close()
    window.close()
    window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("offset", [2.5, -2.5, 0.0])
def test_sweep_uses_lyric_time_and_pitch_uses_media_time(screen, offset):
    screen.window.lyrics_offset_seconds = offset
    screen.window.media_player.position = lambda: 12000
    screen.refresh_pitch_position()

    assert screen.screen.current_lyric.karaoke_position == pytest.approx(12 - offset)
    assert screen.screen.next_lyric.karaoke_position == pytest.approx(12 - offset)
    assert screen.screen.pitch_lane.position_seconds == 12


def test_sweep_preserves_negative_lyric_time_before_delayed_start(screen):
    screen.window.lyrics_offset_seconds = 2.5
    screen.refresh_pitch_position()
    assert screen.screen.current_lyric.karaoke_position == -2.5


@pytest.mark.parametrize("offset", [4.0, -4.0, 0.0])
@pytest.mark.parametrize("lyric_time,top,bottom", [
    (15.9, 0, 1),
    (16.0, 2, 1),
    (19.9, 2, 1),
    (20.0, 2, 3),
])
def test_pair_lookahead_uses_offset_lyric_time(screen, qapp, offset, lyric_time, top, bottom):
    media_ms = round((lyric_time + offset) * 1000)
    screen.window.lyrics_offset_seconds = offset
    screen.window.media_player.position = lambda: media_ms
    screen.window.current_line = 1 if lyric_time < 20 else 2
    screen.screen.show()
    qapp.processEvents()
    screen.refresh_karaoke_screen()

    assert screen._screen_line_index == top
    assert screen._screen_next_line_index == bottom
    assert screen.screen.progress.value() == media_ms
