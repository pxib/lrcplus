from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx


def test_invalid_backward_segment_end_is_not_serialized():
    line = LyricLine(
        start=163.634,
        text="それだけだったんだよ",
        has_karaoke=True,
        segments=[
            LyricSegment(163.634, "それ"),
            LyricSegment(163.966, "だけ "),
            LyricSegment(164.964, "それ"),
            LyricSegment(165.243, "だけ"),
            LyricSegment(165.847, "だっ"),
            LyricSegment(166.427, "たん", end=163.810),
            LyricSegment(167.542, "だよ"),
        ],
    )

    result = serialize_lrcx([line])
    assert "<02:43.810>" not in result
    assert "<02:47.542>だよ" in result
