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


def make_slug(name: str) -> str:
    """Deterministic AdvisorKhoj-compatible slug from a scheme name."""
    return "-".join(c.strip("-") for c in name.replace("(", " ").replace(")", " ").split() if c and c != "-").lower()


# Plan / option detection from scheme-name strings -----------------------------------------

_DIRECT_RE = re.compile(r"\bdirect(\s+plan)?\b", re.IGNORECASE)
_REGULAR_RE = re.compile(r"\bregular(\s+plan)?\b", re.IGNORECASE)

# IDCW also matches "IDCWOption" (no space) and "Dividend".
_IDCW_RE = re.compile(r"(?:idcw|income\s+distribution|\bdividend\b)", re.IGNORECASE)

# Growth-as-payout-option detection — order of checks matters:
#   1. Phrase "Growth Option" or "Growth Plan" anywhere — strong signal.
#   2. Token "Growth" sandwiched between dashes (e.g. "...- Growth - Direct Plan").
#   3. Token "Growth" at end of name (with or without leading dash/whitespace).
#  None of these match "Axis Growth Opportunities Fund" because "Growth" is followed by
#  letters, not Plan/Option/dash/end.
_GROWTH_PHRASE_RE = re.compile(r"\bgrowth\s+(option|plan)\b", re.IGNORECASE)
_GROWTH_BETWEEN_DASHES_RE = re.compile(r"-\s*growth\s*-", re.IGNORECASE)
_GROWTH_END_RE = re.compile(r"(?:^|[\s\-])growth\s*$", re.IGNORECASE)
# "Cumulative" is a legacy term for the Growth payout option.
_CUMULATIVE_RE = re.compile(r"\bcumulative\b", re.IGNORECASE)

_BONUS_RE = re.compile(r"\bbonus\b", re.IGNORECASE)
_ETF_RE = re.compile(r"\betf\b", re.IGNORECASE)


def detect_plan(name: str) -> str | None:
    """Return 'Direct', 'Regular', or None (for funds without that distinction — older schemes,
    ETFs, etc.)."""
    if not name:
        return None
    if _DIRECT_RE.search(name):
        return "Direct"
    if _REGULAR_RE.search(name):
        return "Regular"
    return None


def detect_option(name: str) -> str:
    """Return one of 'Growth', 'IDCW', 'Bonus', 'ETF', 'Other'.

    Never None — every fund gets a category so the filter UI has full coverage.

    Priority:
      1. IDCW first (some names contain both IDCW and Growth Option; IDCW wins as the
         meaningful payout flavour).
      2. Growth (covers "Growth Option/Plan" anywhere, "- Growth -" mid-string,
         end-anchored "growth", and the legacy "Cumulative" term).
      3. Bonus (legacy bonus-units payout option).
      4. ETF (these don't have Growth/IDCW since they trade on exchange).
      5. Other (everything else — debt funds with non-standard naming, FoFs, etc.).
    """
    if not name:
        return "Other"
    if _IDCW_RE.search(name):
        return "IDCW"
    if _GROWTH_PHRASE_RE.search(name) or _GROWTH_BETWEEN_DASHES_RE.search(name) or _CUMULATIVE_RE.search(name):
        return "Growth"
    tail = re.sub(r"[\s\.\)\(]+$", "", name)
    if _GROWTH_END_RE.search(tail):
        return "Growth"
    if _BONUS_RE.search(name):
        return "Bonus"
    if _ETF_RE.search(name):
        return "ETF"
    return "Other"
