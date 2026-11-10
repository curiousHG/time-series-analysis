"""Kuvera public JSON API: fallback fund-metadata source, name-matched then ISIN-verified."""

from __future__ import annotations

import logging
import re
import time
from functools import lru_cache

import httpx

from data.constants import HEADERS
from data.exceptions import UpstreamFormatError
from data.fetchers.schemas import FundMetadata, KuveraSchemeDetail, KuveraSchemeList, validate_upstream

logger = logging.getLogger(__name__)

_LIST_URL = "https://api.kuvera.in/mf/api/v4/fund_schemes/list.json"
_DETAIL_URL = "https://api.kuvera.in/mf/api/v5/fund_schemes/{code}.json"
_FUND_URL = "https://kuvera.in/mutual-funds/scheme/{slug}"

_NOISE_TOKENS = frozenset({"fund", "plan", "option", "scheme", "of", "the", "-"})

_THROTTLE_STATUSES = frozenset({429, 403, 503})
_BACKOFF_SECONDS = 5.0


def _get_detail(code: str) -> KuveraSchemeDetail | None:
    for attempt in range(2):
        resp = httpx.get(_DETAIL_URL.format(code=code), headers=HEADERS, timeout=30)
        if resp.status_code in _THROTTLE_STATUSES and attempt == 0:
            logger.warning("Kuvera returned %s for %s — backing off %.0fs", resp.status_code, code, _BACKOFF_SECONDS)
            time.sleep(_BACKOFF_SECONDS)
            continue
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise UpstreamFormatError("Kuvera scheme detail", f"expected a list, got {type(payload).__name__}")
        if not payload:
            return None
        return validate_upstream("Kuvera scheme detail", KuveraSchemeDetail, payload[0])
    raise RuntimeError("unreachable")


def normalize_tokens(name: str) -> frozenset[str]:
    """Order-free identity tokens of a scheme name (lowercased, punctuation stripped)."""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return frozenset(t for t in cleaned.split() if t and t not in _NOISE_TOKENS)


@lru_cache(maxsize=1)
def _code_index() -> list[tuple[frozenset[str], str]]:
    """(token-set, kuvera code) for every scheme in Kuvera's list. One fetch per process."""
    resp = httpx.get(_LIST_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    payload = validate_upstream("Kuvera scheme list", KuveraSchemeList, resp.json())
    index: list[tuple[frozenset[str], str]] = []
    for asset_class in payload.values():
        for sub_cat in asset_class.values():
            for funds in sub_cat.values():
                for f in funds:
                    code, name = f.get("c"), f.get("n")
                    if code and name:
                        index.append((normalize_tokens(name), code))
    if not index:
        raise UpstreamFormatError("Kuvera scheme list", "no scheme carried both a 'c' (code) and 'n' (name) key")
    logger.info("Kuvera code index loaded: %d schemes", len(index))
    return index


def _candidate_codes(scheme_name: str, max_candidates: int = 4) -> list[str]:
    """Kuvera codes whose name tokens best match `scheme_name` (exact set first, then
    highest Jaccard overlap ≥ 0.75)."""
    target = normalize_tokens(scheme_name)
    scored: list[tuple[float, str]] = []
    for tokens, code in _code_index():
        if tokens == target:
            scored.append((2.0, code))
            continue
        union = len(tokens | target)
        if union:
            j = len(tokens & target) / union
            if j >= 0.75:
                scored.append((j, code))
    scored.sort(reverse=True)
    return [code for _, code in scored[:max_candidates]]


def fetch_fund_metadata_kuvera(scheme_name: str, isins: tuple[str | None, ...]) -> FundMetadata | None:
    """`FundMetadata` from Kuvera, or None.

    :param isins: acceptable ISINs from the AMFI record (growth/reinvestment); a candidate
        is accepted only when its detail ISIN is one of these.
    """
    accept = {i for i in isins if i}
    if not accept:
        return None

    for code in _candidate_codes(scheme_name):
        try:
            d = _get_detail(code)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in _THROTTLE_STATUSES:
                raise
            logger.debug("Kuvera detail failed for %s: %s", code, e)
            continue
        except Exception as e:
            logger.debug("Kuvera detail failed for %s: %s", code, e)
            continue
        if d is None or d.isin not in accept:
            continue

        return FundMetadata(
            scheme_name=scheme_name,
            aum_crores=round(d.aum / 10.0, 2) if d.aum else None,
            aum_as_of=None,
            expense_ratio=d.expense_ratio,
            expense_ratio_as_of=d.expense_ratio_date,
            benchmark=None,
            launch_date=d.start_date,
            category=d.fund_category,
            asset_class=d.fund_type,
            status="Open Ended" if d.maturity_type == "Open Ended" else d.maturity_type,
            min_investment=d.lump_min,
            min_topup=d.lump_min_additional,
            turnover_ratio=d.portfolio_turnover,
            exit_load=None,
            investment_objective=(d.investment_objective or "")[:2000] or None,
            risk_level=(d.crisil_rating or "").removesuffix(" Risk") or None,
            fund_manager=d.fund_manager,
            fund_house=None,
            source_url=_FUND_URL.format(slug=d.slug or code),
        )
    return None
