def layout_width(segments, padding):
    total = 0
    for width, has_reading in segments:
        total += width
        if has_reading:
            total += padding
    return total


def test_kanji_reading_segments_keep_a_gap():
    # Large kanji/ruby segments keep their padding after each reading-bearing
    # segment, so both ruby units contribute a gap.
    assert layout_width(
        [(10, True), (20, False), (10, True), (30, False)],
        6,
    ) == 82


def test_kana_continuation_does_not_get_its_own_gap():
    # The gap belongs after the kanji/ruby unit, not after every segment.
    assert layout_width(
        [(10, True), (20, False)],
        6,
    ) == 36


def test_unannotated_kana_has_no_artificial_segment_gap():
    assert layout_width(
        [(10, False), (10, False), (10, False)],
        6,
    ) == 30
