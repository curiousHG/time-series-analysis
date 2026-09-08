"""AdvisorKhoj scheme-name resolution — pure helpers, no I/O.

AdvisorKhoj keys every fund by the name AMFI published *before* its 2025 feed change
("<base> - Direct Plan - Growth"). AMFI now emits the plan/option in separate columns with
looser text ("Growth Option", "GROWTH", "IDCW-Re-investment"), so no single string derived
from today's feed reliably matches. These helpers generate the candidate spellings a resolver
tries in order, and the base-fund comparison used to accept or reject what comes back.
"""

from __future__ import annotations

import re

_OPTION_SHORT = {
    "growth": "Growth",
    "idcw": "IDCW",
    "idcw payout": "IDCW Payout",
    "idcw-payout": "IDCW Payout",
    "idcw reinvestment": "IDCW Reinvestment",
    "idcw-reinvestment": "IDCW Reinvestment",
    "idcw-re-investment": "IDCW Reinvestment",
    "idcw re-investment": "IDCW Reinvestment",
}


def base_name(scheme_name: str, plan: str | None, option: str | None) -> str:
    """The fund's name without its plan/option suffix — what every variant of a fund shares.

    Inverse of `compose_scheme_name`: strips " - <plan> - <option>", " - <plan>" or
    " - <option>" from the end, whichever was appended.
    """
    for suffix in (
        f" - {plan} - {option}" if plan and option else None,
        f" - {plan}" if plan else None,
        f" - {option}" if option else None,
    ):
        if suffix and scheme_name.endswith(suffix):
            return scheme_name[: -len(suffix)]
    return scheme_name


def short_option(option: str) -> str:
    """ "Growth Option" → "Growth", "GROWTH" → "Growth", "IDCW-Re-investment" → "IDCW Reinvestment".
    Unknown flavours ("Weekly IDCW") pass through with only the trailing "Option" removed."""
    bare = re.sub(r"\s*Option$", "", option.strip(), flags=re.I).strip()
    return _OPTION_SHORT.get(bare.lower(), bare)


_RENAME_NOTE_RE = re.compile(r"\s*\((?:formerly|erstwhile|earlier)[^)]*\)", re.I)


def base_variants(base: str) -> list[str]:
    """The base name plus the same without a rename note — "Sundaram Large Cap Fund (Formerly
    Known as Sundaram Blue Chip Fund)" is keyed by AdvisorKhoj under one of the two plain names."""
    variants = [base]
    stripped = re.sub(r"\s+", " ", _RENAME_NOTE_RE.sub("", base)).strip()
    if stripped and stripped != base:
        variants.append(stripped)
    return variants


def name_candidates(base: str, plan: str | None, option: str | None, *, known: list[str] | None = None) -> list[str]:
    """Candidate AdvisorKhoj scheme names, most likely first, deduplicated.

    `known` are spellings that resolved before (a stored slug's name, the URL an earlier
    metadata scrape used) and are tried first. Order of the generated forms follows the
    measured hit rate on live funds: "<base> - <plan> - <short option>" resolves the large
    majority, the plan-without-"Plan" form next, AMFI's verbatim option text after that.
    Each form is generated for every `base_variants` spelling.
    """
    out: list[str] = list(known or [])
    plan_short = re.sub(r"\s+Plan$", "", plan, flags=re.I) if plan else None
    for b in base_variants(base):
        if plan and option:
            opt = short_option(option)
            out += [
                f"{b} - {plan} - {opt}",
                f"{b} - {plan_short} - {opt}",
                f"{b} - {plan} - {option}",
                f"{b} - {opt} - {plan}",
                f"{b} - {plan_short} - {option}",
            ]
        elif plan:
            out += [f"{b} - {plan}", f"{b} - {plan_short}"]
        elif option:
            out += [f"{b} - {short_option(option)}", f"{b} - {option}"]
        out.append(b)

    seen: set[str] = set()
    unique: list[str] = []
    for name in out:
        key = re.sub(r"\s+", " ", name).strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(re.sub(r"\s+", " ", name).strip())
    return unique


def same_fund(base_a: str, base_b: str) -> bool:
    """Whether two base names denote the same fund, tolerant of punctuation and case
    ("Axis ELSS- Tax Saver Fund" vs "Axis ELSS Tax Saver Fund")."""
    return _tokens(base_a) == _tokens(base_b)


def _tokens(s: str) -> tuple[str, ...]:
    return tuple(t for t in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if t)
