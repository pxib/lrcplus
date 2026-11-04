import pytest

from core.translation_usage import estimate_usage, format_estimate, quota_reference


def test_unicode_whitespace_and_exact_duplicates_match_sent_source():
    assert estimate_usage(["字😀\n ", "字😀\n ", "字😀"]) == {
        "characters": 6, "rows": 3, "unique_rows": 2, "repeated_characters": 4,
    }
    assert estimate_usage([])["characters"] == 0


@pytest.mark.parametrize("provider,profile,limit,period", [
    ("azure", "developer", 2_000_000, "month"),
    ("google", "developer", 500_000, "month"),
    ("mymemory", "developer", 5_000, "day"),
    ("deepl", "developer", 1_000_000, "total"),
    ("deepl", "free", 500_000, "month"),
    ("deepl", "growth_monthly", 1_000_000, "month"),
    ("deepl", "growth_annual", 12_000_000, "year"),
])
def test_allowances_are_explicit_references(provider, profile, limit, period):
    reference = quota_reference(provider, profile)
    assert (reference.characters, reference.period) == (limit, period)
    text = format_estimate(provider, ["a" * 1000], deepl_plan=profile, sent=500)
    assert f"{limit:,} characters/{period} reference allowance" in text
    assert "Sent from this tab: ~500" in text
    assert "Actual remaining quota is unknown" in text


def test_local_and_paid_usage_and_oversized_run():
    assert "Offline: no character quota" in format_estimate(None, ["A", "A"])
    assert "1 repeated characters reused" in format_estimate(None, ["A", "A"])
    assert "no included character allowance assumed" in format_estimate("deepl", ["A"], deepl_plan="pro")
    assert "~120%" in format_estimate("mymemory", ["A" * 3000, "B" * 3000])
    assert "exceeds that full allowance" in format_estimate("mymemory", ["A" * 3000, "B" * 3000])
