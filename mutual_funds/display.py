"""Display helpers — short labels for scheme names in charts and pickers."""

import re

# Each pattern is anchored at end-of-string. We loop until the name stops shrinking
# so multi-suffix tails like "- Direct Plan - Growth - IDCW" peel off layer by layer.
# Anchoring avoids eating valid mid-name occurrences (e.g. "Axis Growth Opportunities Fund").
_NOISE_PATTERNS = [
    r"[\s\-]*\(direct\s+plan\)\s*$",
    r"[\s\-]*\(regular\s+plan\)\s*$",
    r"[\s\-]*direct\s+plan\s*$",
    r"[\s\-]*regular\s+plan\s*$",
    r"[\s\-]*growth\s+option\s*$",
    r"[\s\-]*growth\s+plan\s*$",
    r"[\s\-]*idcw(\s+(reinvestment|payout))?\s*$",
    r"[\s\-]*growth\s*$",
    r"[\s\-]+direct\s*$",
    r"[\s\-]+regular\s*$",
]


def short_scheme_name(name: str) -> str:
    """Strip trailing plan/option suffixes (Direct[-Plan], Regular[-Plan], Growth, IDCW) for display."""
    if not name:
        return name
    s = name
    for _ in range(6):
        before = s
        for pat in _NOISE_PATTERNS:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        if s == before:
            break
    s = re.sub(r"\s*-\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or name


def short_scheme_names(names: list[str]) -> list[str]:
    return [short_scheme_name(n) for n in names]
