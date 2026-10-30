"""Song prefetch must populate the cache used when playback activates LRCX rows."""

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace

import pytest

import core.lyrics_controller as controller_module
import lyrics.furigana as readings
import lyrics.furigana_widget as renderer_module
from lyrics.loader import load_lyrics


@pytest.mark.parametrize("mecab_mode", ["batch", "line"])
def test_lrcx_prefetch_caches_readings_before_playback_visits_every_line(qapp, tmp_path, monkeypatch, mecab_mode):
    path = tmp_path / "song.lrcx"
    path.write_text(
        "[format:LRCX]\n[version:1]\n[g:auto]\n"
        "[00:00.650]> **ワタシ 超最強**{\\n}LOL!\n"
        "[00:03.050]超最強 な～んです\n"
        "[00:06.900]\n"
        "[00:14.570]目と目あったならズッキュンと\n"
        "[00:16.440]キミの<00:17.000>{心|こころ}\n"
        "[00:18.300]超最強 な～んです\n", encoding="utf-8",
    )
    controller = controller_module.LyricsController()
    controller.lyric_lines = load_lyrics(path)
    original_lines = deepcopy(controller.lyric_lines)
    assert all(line.segments for line in controller.lyric_lines if line.text)

    monkeypatch.setattr(readings, "_READING_MODE", "auto")
    monkeypatch.setattr(readings, "_ACTIVE_PARSER", "mecab_local")
    monkeypatch.setattr(readings, "_MECAB_PARSE_MODE", mecab_mode)
    monkeypatch.setattr(readings, "_AUTO_LANGUAGE_CONTEXT", "chinese")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())
    monkeypatch.setattr(readings, "find_reading_provider", lambda *args, **kwargs: None)
    analyzed = []

    def analyze(text):
        analyzed.append(text)
        return [{"surface": part, "reading": None} for part in text.split("\n") if part]

    monkeypatch.setattr(readings, "_analyze", analyze)
    prefetch_results = []

    class ImmediateExecutor:
        def submit(self, worker):
            # The GUI and worker must start with the same cache context.
            assert readings.get_auto_language_context() == "japanese"
            result = worker()
            prefetch_results.append(result)
            future = Future()
            future.set_result(result)
            return future

    class NoPlaybackParsing:
        def submit(self, *args, **kwargs):
            raise AssertionError("Playback should use prefetched readings, not start a reading worker")

    monkeypatch.setattr(controller_module, "_FURIGANA_PREFETCH_EXECUTOR", ImmediateExecutor())
    monkeypatch.setattr(renderer_module, "_FURIGANA_EXECUTOR", NoPlaybackParsing())
    controller._furigana_progress_bridge = SimpleNamespace(
        progress=SimpleNamespace(emit=lambda *args: None),
        finished=SimpleNamespace(emit=lambda *args: None),
    )
    controller._start_furigana_prefetch()
    assert prefetch_results == [(4, 4)]
    assert len(analyzed) == (1 if mecab_mode == "batch" else 4)
    before_playback = list(analyzed)
    renderer = renderer_module.FuriganaWidget()
    try:
        # Cover new lines and revisits, including ruby, formatting, visual
        # newlines, repeated text and explicitly timed karaoke chunks.
        for line in controller.lyric_lines * 2:
            if line.text:
                cached = readings.get_cached_furigana_segments(line.text)
                assert cached is not None
                assert "".join(piece["text"] for piece in cached) == line.text
            renderer.set_segments(line.text, line.segments, line.end,
                                  style_spans=line.style_spans, is_blockquote=line.is_blockquote)
        assert analyzed == before_playback
        controller._start_furigana_prefetch()
        assert prefetch_results[-1] == (0, 0)
        assert analyzed == before_playback
        assert controller.lyric_lines == original_lines
    finally:
        renderer.deleteLater()
