def distribute(start, end, total_chars, boundaries):
    duration = end - start
    return [
        (
            start + duration * (a / total_chars),
            start + duration * (b / total_chars),
        )
        for a, b in boundaries
    ]


def test_new_boundaries_get_distinct_times():
    values = distribute(
        6.660, 9.110, 8,
        [(0, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 8)],
    )
    assert values[0] == (6.660, 7.2725)
    assert values[-1][1] == 9.110
    assert len({round(start, 6) for start, _ in values}) > 1
