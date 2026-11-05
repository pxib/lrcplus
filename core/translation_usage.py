"""Local estimates for translation input, never an account balance."""
from dataclasses import dataclass


@dataclass(frozen=True)
class QuotaReference:
    label: str
    characters: int
    period: str


DEEPL_QUOTAS = {
    "developer": QuotaReference("API Developer", 1_000_000, "total"),
    "free": QuotaReference("Legacy API Free", 500_000, "month"),
    "growth_monthly": QuotaReference("API Growth monthly", 1_000_000, "month"),
    "growth_annual": QuotaReference("API Growth annual", 12_000_000, "year"),
    "pro": None,
}


def quota_reference(provider, deepl_plan="developer"):
    if provider == "deepl":
        return DEEPL_QUOTAS.get(deepl_plan)
    return {
        "azure": QuotaReference("Azure F0", 2_000_000, "month"),
        "google": QuotaReference("Google NMT free credit", 500_000, "month"),
        "mymemory": QuotaReference("MyMemory anonymous", 5_000, "day"),
    }.get(provider)


def estimate_usage(texts):
    """Count exactly the source strings sent; whitespace and newlines count.

    The adapters reuse identical strings within a run. Python len counts Unicode
    code points, so these are character estimates, independent of UTF-8 limits.
    """
    texts = list(texts)
    unique = set(texts)
    characters = sum(map(len, unique))
    return {
        "characters": characters,
        "rows": len(texts), "unique_rows": len(unique),
        "repeated_characters": sum(map(len, texts)) - characters,
    }


def format_estimate(provider, texts, *, deepl_plan="developer", sent=0):
    usage = estimate_usage(texts)
    characters = usage["characters"]
    count = f"~{characters:,} characters"
    detail = f"{usage['unique_rows']:,} unique / {usage['rows']:,} rows"
    if usage["repeated_characters"]:
        detail += f"; {usage['repeated_characters']:,} repeated characters reused"
    if not provider:
        return f"Offline: no character quota. {count} ({detail})."
    text = f"Next run: {count} ({detail})."
    reference = quota_reference(provider, deepl_plan)
    if reference:
        percentage = 100 * characters / reference.characters
        text += (f"\n~{percentage:.3g}% of {reference.label}'s "
                 f"{reference.characters:,} characters/{reference.period} reference allowance.")
        if characters > reference.characters:
            text += " This run exceeds that full allowance."
    else:
        if provider == "datpmt":
            text += "\nDatpmt publishes no character allowance; service availability and limits may change."
        else:
            text += "\nUsage-based plan: no included character allowance assumed."
    return text + f"\nSent from this tab: ~{sent:,} characters. Actual remaining quota is unknown."
