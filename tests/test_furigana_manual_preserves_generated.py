from concurrent.futures import Future

import lyrics.furigana_widget as renderer


def test_karaoke_manual_ruby_never_disables_whole_line_generation(qapp, monkeypatch):
    text = "ManualGenerated"
    cache = {}
    requests = []
    future = Future()

    class Executor:
        def submit(self, work):
            requests.append(work)
            return future

    def generate(value):
        cache[value] = [
            {"text": "Manual", "reading": "automatic"},
            {"text": "Generated", "reading": "generated"},
        ]
        return cache[value]

    monkeypatch.setattr(renderer, "_FURIGANA_EXECUTOR", Executor())
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", cache.get)
    monkeypatch.setattr(renderer, "build_furigana_segments", generate)
    widget = renderer.FuriganaWidget()
    segments = [
        {"text": "Manual", "reading": "authored", "start": 0, "end": 1},
        {"text": "Generated", "start": 1, "end": 2},
    ]
    try:
        widget.set_segments(text, segments, line_end=2)
        assert len(requests) == 1
        assert widget.segments[0]["reading"] == "authored"
        # Repeated renders must not duplicate the pending whole-line request.
        widget.set_segments(text, segments, line_end=2)
        assert len(requests) == 1

        future.set_result(requests[0]())
        widget._poll_readings()
        assert [part["reading"] for part in widget.segments] == ["authored", "generated"]
        assert [(part["start"], part["end"]) for part in widget.segments] == [(0, 1), (1, 2)]
        assert widget.karaoke_line_end == 2
        assert widget._karaoke_furigana_pending is None
        assert len(requests) == 1
    finally:
        widget.close()
        widget.deleteLater()
