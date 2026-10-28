from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx
from lyrics.lrcx_parser import load_lrcx


def test_karaoke_boundaries_survive_lrcx_roundtrip(tmp_path):
    line = LyricLine(
        start=28.630,
        text="さっきも見たおじさんが",
        segments=[
            LyricSegment(28.630, "さっき"),
            LyricSegment(29.100, "も"),
            LyricSegment(29.500, "見た"),
            LyricSegment(30.200, "おじさんが"),
        ],
        end=31.910,
        has_karaoke=True,
    )
    path = tmp_path / "lyrics.lrcx"
    path.write_text(serialize_lrcx([line]), encoding="utf-8")

    loaded = load_lrcx(path)[0]

    assert loaded.has_karaoke
    assert [segment.text for segment in loaded.segments] == [
        "さっき", "も", "見た", "おじさんが"
    ]


def test_ruby_is_preserved_when_karaoke_splits_one_reading_token():
    from lyrics.furigana import split_reading_by_text_ranges

    assert split_reading_by_text_ranges(
        "何千回",
        "なんぜんかい",
        [(0, 1), (1, 2), (2, 3)],
    ) == ["なん", "ぜん", "かい"]

    assert split_reading_by_text_ranges(
        "越える",
        "こえる",
        [(0, 1), (1, 3)],
    ) == ["こ", "える"]


def test_editing_ruby_does_not_change_karaoke_timestamps():
    line = LyricLine(
        start=69.832,
        text="君の心へ届けて",
        segments=[
            LyricSegment(69.832, "君", end=70.482),
            LyricSegment(70.482, "の", end=70.901),
            LyricSegment(70.901, "心", end=71.242),
            LyricSegment(71.242, "へ", end=71.597),
            LyricSegment(71.597, "届い", end=72.046),
            LyricSegment(72.046, "て", end=72.394),
        ],
        end=72.394,
        has_karaoke=True,
    )

    before = [(s.start, s.end, s.text, s.instant) for s in line.segments]

    # Equivalent to an exact-match manual ruby edit in the editor.  Timing
    # fields must remain byte-for-byte untouched by the reading operation.
    line.segments[0].ruby = "きみ"
    line.segments[0].ruby_source = "manual"

    after = [(s.start, s.end, s.text, s.instant) for s in line.segments]
    assert after == before

    text = serialize_lrcx([line])
    assert "[01:09.832]{君|きみ}<01:10.482>の" in text
    assert "<01:05.482>" not in text
    assert "<01:01.357>" not in text


def test_editing_reading_preserves_other_tokens_across_karaoke_boundaries():
    """Editing one furigana cell must not make the other readings disappear."""
    from lyrics.furigana import split_reading_by_text_ranges

    line = LyricLine(
        start=10.000,
        text="何千回越える",
        segments=[
            LyricSegment(10.000, "何", end=10.200),
            LyricSegment(10.200, "千", end=10.400),
            LyricSegment(10.400, "回", end=10.600),
            LyricSegment(10.600, "越", end=10.800),
            LyricSegment(10.800, "える", end=11.000),
        ],
        end=11.000,
        has_karaoke=True,
    )

    tokens = [
        ("何", "なん"),
        ("千", "ぜん"),
        ("回", "かい"),
        ("越", "こ"),
        ("える", None),
    ]

    token_ranges = []
    position = 0
    for base, reading in tokens:
        end = position + len(base)
        token_ranges.append((position, end, base, reading))
        position = end

    position = 0
    for segment in line.segments:
        end_position = position + len(segment.text)
        pieces = []
        for token_start, token_end, token_base, token_reading in token_ranges:
            overlap_start = max(position, token_start)
            overlap_end = min(end_position, token_end)
            if overlap_start >= overlap_end or not token_reading:
                continue
            piece = split_reading_by_text_ranges(
                token_base,
                token_reading,
                [(overlap_start - token_start, overlap_end - token_start)],
            )[0]
            if piece:
                pieces.append(piece)
        segment.ruby = "".join(pieces) or None
        segment.ruby_source = "manual" if segment.ruby else None
        position = end_position

    assert [s.ruby for s in line.segments] == [
        "なん", "ぜん", "かい", "こ", None
    ]
