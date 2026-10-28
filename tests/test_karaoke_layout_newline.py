from lyrics.karaoke_layout import build_karaoke_render_pieces


def _normalized_for_multiline():
    return [
        {
            "text": "らららー",
            "reading": None,
            "reading_source": None,
            "ruby_spans": [],
            "start": 1.0,
            "end": 2.0,
            "instant": False,
            "start_offset": 0,
            "end_offset": 4,
            "compact_start": 0,
            "compact_end": 4,
            "timing_group": 0,
            "karaoke_group": 0,
            "lyric_group": 99,
            "visual_line": 0,
        },
        {
            "text": "hello",
            "reading": None,
            "reading_source": None,
            "ruby_spans": [],
            "start": 3.0,
            "end": 4.0,
            "instant": False,
            "start_offset": 5,
            "end_offset": 10,
            "compact_start": 4,
            "compact_end": 9,
            "timing_group": 1,
            "karaoke_group": 1,
            "lyric_group": 99,
            "visual_line": 1,
        },
    ]


def test_newline_does_not_shift_second_timestamped_chunk():
    pieces = build_karaoke_render_pieces(
        "らららー\nhello",
        _normalized_for_multiline(),
        # Deliberately simulate a tokenizer that dropped the structural newline.
        [
            {"text": "らららー", "reading": None},
            {"text": "hello", "reading": None},
        ],
        line_end=4.0,
    )

    assert [piece["text"] for piece in pieces] == ["らららー", "hello"]
    assert [piece["start"] for piece in pieces] == [1.0, 3.0]
    assert [piece["visual_line"] for piece in pieces] == [0, 1]
    assert [piece["timing_group"] for piece in pieces] == [0, 1]
    assert all("\n" not in piece["text"] for piece in pieces)


def test_missing_token_annotation_never_hides_text_after_newline():
    pieces = build_karaoke_render_pieces(
        "AAA\nBBB",
        [
            {
                "text": "AAA",
                "reading": None,
                "reading_source": None,
                "ruby_spans": [],
                "start": 10.0,
                "end": 11.0,
                "instant": False,
                "start_offset": 0,
                "end_offset": 3,
                "compact_start": 0,
                "compact_end": 3,
                "timing_group": 0,
                "karaoke_group": 0,
                "lyric_group": 7,
                "visual_line": 0,
            },
            {
                "text": "BBB",
                "reading": None,
                "reading_source": None,
                "ruby_spans": [],
                "start": 12.0,
                "end": 13.0,
                "instant": False,
                "start_offset": 4,
                "end_offset": 7,
                "compact_start": 3,
                "compact_end": 6,
                "timing_group": 1,
                "karaoke_group": 1,
                "lyric_group": 7,
                "visual_line": 1,
            },
        ],
        # Provider fails to return a token for BBB entirely.
        [{"text": "AAA", "reading": None}],
        line_end=13.0,
    )

    assert [piece["text"] for piece in pieces] == ["AAA", "BBB"]
    assert [piece["start"] for piece in pieces] == [10.0, 12.0]
