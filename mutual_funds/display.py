"""Display helpers — short labels for scheme names in charts and pickers."""

import re

from mutual_funds.constants import (
    BONUS_RE,
    CUMULATIVE_RE,
    DIRECT_RE,
    ETF_RE,
    GROWTH_BETWEEN_DASHES_RE,
    GROWTH_END_RE,
    GROWTH_PHRASE_RE,
    IDCW_RE,
    NOISE_PATTERNS,
    REGULAR_RE,
    FundOption,
    Plan,
)


def short_scheme_name(name: str) -> str:
    """Strip trailing plan/option suffixes (Direct[-Plan], Regular[-Plan], Growth, IDCW) for display."""
    if not name:
        return name
    s = name
    for _ in range(6):
        before = s
        for pat in NOISE_PATTERNS:
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


def detect_plan(name: str) -> Plan | None:
    """Return 'Direct', 'Regular', or None (for funds without that distinction — older schemes,
    ETFs, etc.)."""
    if not name:
        return None
    if DIRECT_RE.search(name):
        return "Direct"
    if REGULAR_RE.search(name):
        return "Regular"
    return None


def detect_option(name: str) -> FundOption:
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
    if IDCW_RE.search(name):
        return "IDCW"
    if GROWTH_PHRASE_RE.search(name) or GROWTH_BETWEEN_DASHES_RE.search(name) or CUMULATIVE_RE.search(name):
        return "Growth"
    tail = re.sub(r"[\s\.\)\(]+$", "", name)
    if GROWTH_END_RE.search(tail):
        return "Growth"
    if BONUS_RE.search(name):
        return "Bonus"
    if ETF_RE.search(name):
        return "ETF"
    return "Other"
