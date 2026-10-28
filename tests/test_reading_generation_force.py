from lyrics.furigana import (
    build_furigana_segments,
    clear_reading_segment_cache,
    prefetch_furigana_batches,
    set_automatic_readings_enabled,
    set_reading_mode,
)
from lyrics.reading_providers import register_reading_provider, unregister_reading_provider


def test_explicit_generation_can_bypass_automatic_reading_opt_out():
    provider_id = "test_force_generation"
    register_reading_provider(
        provider_id,
        "Test provider",
        lambda text: [{"text": text, "reading": "generated"}],
        batch_generator=lambda texts: [
            [{"text": text, "reading": "generated"}]
            for text in texts
        ],
    )
    set_reading_mode(provider_id)
    set_automatic_readings_enabled(False)
    clear_reading_segment_cache()

    try:
        single = build_furigana_segments("single", force=True)
        assert any(segment.get("reading") for segment in single)

        completed, total = prefetch_furigana_batches(["first", "second"], force=True)
        assert (completed, total) == (2, 2)
        assert any(
            segment.get("reading")
            for segment in build_furigana_segments("first", force=True)
        )
    finally:
        set_automatic_readings_enabled(True)
        set_reading_mode("auto")
        unregister_reading_provider(provider_id)
        clear_reading_segment_cache()