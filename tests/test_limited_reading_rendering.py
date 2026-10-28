from lyrics.widgets import ScrollingLyricsWidget


def test_prefetched_readings_refresh_outside_limited_render_window(monkeypatch):
    cached_readings = [{"text": "later", "reading": "reading"}]
    monkeypatch.setattr(
        "lyrics.scrolling_widget.get_cached_furigana_segments",
        lambda _text: cached_readings,
    )

    class RowStub:
        def __init__(self, lyric):
            self.lyric = lyric
            self.segments = []

        def apply_manual_overrides(self, segments):
            return segments

        def _update_content_geometry(self):
            pass

        def update(self):
            pass

    class WidgetStub:
        rows = [RowStub("current"), RowStub("later")]
        limited_reading_rendering = True
        current_line = 0
        limited_reading_range = 0

    ScrollingLyricsWidget.refresh_furigana_for_texts(WidgetStub())

    assert WidgetStub.rows[1].segments == cached_readings
