"""Display helpers — short labels for scheme names in charts and pickers."""

import re
from collections.abc import Iterable

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


def unique_short_names(names: Iterable[str]) -> dict[str, str]:
    """name -> short label, guaranteed unique across `names`.

    Sibling variants (Growth vs IDCW, Direct vs Regular) shorten to the same label; a clashing
    label gets its option, then its plan, appended, and the full name as a last resort.
    """
    names = list(dict.fromkeys(names))
    labels = {n: short_scheme_name(n) for n in names}

    def qualify(fn) -> None:
        clashes = {label for label in labels.values() if list(labels.values()).count(label) > 1}
        for n in names:
            if labels[n] in clashes:
                labels[n] = fn(n)

    qualify(lambda n: f"{short_scheme_name(n)} ({detect_option(n)})")
    qualify(lambda n: f"{short_scheme_name(n)} ({detect_plan(n) or 'Regular'}, {detect_option(n)})")
    qualify(lambda n: n)
    return labels


def make_slug(name: str) -> str:
    """Deterministic AdvisorKhoj-compatible slug from a scheme name."""
    return "-".join(c.strip("-") for c in name.replace("(", " ").replace(")", " ").split() if c and c != "-").lower()


def detect_plan(name: str) -> Plan | None:
    """Return 'Direct', 'Regular', or None (older schemes, ETFs lack the distinction)."""
    if not name:
        return None
    if DIRECT_RE.search(name):
        return "Direct"
    if REGULAR_RE.search(name):
        return "Regular"
    return None


def detect_option(name: str) -> FundOption:
    """Return 'Growth'/'IDCW'/'Bonus'/'ETF'/'Other'. Never None — every fund gets a category.

    Priority (order matters):
      1. IDCW first — names with both IDCW and Growth: IDCW is the meaningful payout flavour.
      2. Growth — "Growth Option/Plan" anywhere, "- Growth -" mid-string, end-anchored
         "growth", and the legacy "Cumulative" term.
      3. Bonus (legacy bonus-units payout). 4. ETF (no Growth/IDCW, trades on exchange).
      5. Other (debt funds with non-standard naming, FoFs, etc.).
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
