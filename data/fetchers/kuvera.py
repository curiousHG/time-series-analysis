"""Kuvera public JSON API — fallback metadata source when AdvisorKhoj lacks a fund.

Resolution is two-step: a normalized token-set name match against Kuvera's scheme list
proposes candidate codes, then the candidate's detail payload must ISIN-match the AMFI
record before anything is accepted — a name match can propose, only the ISIN disposes.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from functools import lru_cache

import httpx

from data.constants import HEADERS

logger = logging.getLogger(__name__)

_LIST_URL = "https://api.kuvera.in/mf/api/v4/fund_schemes/list.json"
_DETAIL_URL = "https://api.kuvera.in/mf/api/v5/fund_schemes/{code}.json"
_FUND_URL = "https://kuvera.in/mutual-funds/scheme/{slug}"

# Tokens that differ between AMFI and Kuvera naming conventions but carry no identity.
_NOISE_TOKENS = frozenset({"fund", "plan", "option", "scheme", "of", "the", "-"})


def normalize_tokens(name: str) -> frozenset[str]:
    """Order-free identity tokens of a scheme name (lowercased, punctuation stripped)."""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return frozenset(t for t in cleaned.split() if t and t not in _NOISE_TOKENS)


@lru_cache(maxsize=1)
def _code_index() -> list[tuple[frozenset[str], str]]:
    """(token-set, kuvera code) for every scheme in Kuvera's list. One fetch per process."""
    resp = httpx.get(_LIST_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    index: list[tuple[frozenset[str], str]] = []
    for asset_class in resp.json().values():  # {class: {sub_cat: {amc: [{c, n, ...}]}}}
        for sub_cat in asset_class.values():
            for funds in sub_cat.values():
                for f in funds:
                    code, name = f.get("c"), f.get("n")
                    if code and name:
                        index.append((normalize_tokens(name), code))
    logger.info("Kuvera code index loaded: %d schemes", len(index))
    return index


def _candidate_codes(scheme_name: str, max_candidates: int = 4) -> list[str]:
    """Kuvera codes whose name tokens best match `scheme_name` (exact set first, then
    highest Jaccard overlap ≥ 0.75)."""
    target = normalize_tokens(scheme_name)
    scored: list[tuple[float, str]] = []
    for tokens, code in _code_index():
        if tokens == target:
            scored.append((2.0, code))  # exact set match outranks everything
            continue
        union = len(tokens | target)
        if union:
            j = len(tokens & target) / union
            if j >= 0.75:
                scored.append((j, code))
    scored.sort(reverse=True)
    return [code for _, code in scored[:max_candidates]]


def fetch_fund_metadata_kuvera(scheme_name: str, isins: tuple[str | None, ...]) -> dict | None:
    """Metadata dict (same shape as fetch_fund_metadata) from Kuvera, or None.

    :param isins: acceptable ISINs from the AMFI record (growth/reinvestment); a candidate
        is accepted only when its detail ISIN is one of these.
    """
    accept = {i for i in isins if i}
    if not accept:
        return None  # without an ISIN there is no safe way to verify a name match

    for code in _candidate_codes(scheme_name):
        try:
            resp = httpx.get(_DETAIL_URL.format(code=code), headers=HEADERS, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.debug("Kuvera detail failed for %s: %s", code, e)
            continue
        if not payload:
            continue
        d = payload[0]
        if d.get("ISIN") not in accept:
            continue

        aum = _num(d.get("aum"))
        return {
            "scheme_name": scheme_name,
            # Kuvera reports AUM in Rs millions (verified vs AdvisorKhoj crores).
            "aum_crores": round(aum / 10.0, 2) if aum else None,
            "aum_as_of": None,
            "expense_ratio": _num(d.get("expense_ratio")),
            "expense_ratio_as_of": _iso_date(d.get("expense_ratio_date")),
            "benchmark": None,  # Kuvera doesn't expose the SEBI benchmark
            "launch_date": _iso_date(d.get("start_date")),
            "category": d.get("fund_category"),
            "asset_class": d.get("fund_type"),
            "status": "Open Ended" if d.get("maturity_type") == "Open Ended" else d.get("maturity_type"),
            "min_investment": _num(d.get("lump_min")),
            "min_topup": _num(d.get("lump_min_additional")),
            "turnover_ratio": _num(d.get("portfolio_turnover")),
            "exit_load": None,
            "fund_house": None,  # repository fills from amfi_schemes, same as AdvisorKhoj path
            "source_url": _FUND_URL.format(slug=d.get("slug", code)),
        }
    return None


def _num(v) -> float | None:
    """Kuvera mixes numerics and numeric strings; normalise to float or None."""
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _iso_date(v):
    """'2013-01-01' → date; passthrough for None/invalid."""
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
