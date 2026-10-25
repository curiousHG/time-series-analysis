"""Resolving and storing a fund's AdvisorKhoj portfolio key."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import unquote

from core.database import get_session
from core.models import AmfiScheme, MfMetadata, MfRegistry
from data.exceptions import SourceNotFound
from data.fetchers.advisorkhoj import fetch_portfolio
from mutual_funds.advisorkhoj import base_name, name_candidates, same_fund
from mutual_funds.display import make_slug
from mutual_funds.holdings import normalize_asset_allocation, normalize_holdings, normalize_sector_allocation

if TYPE_CHECKING:
    import polars as pl

logger = logging.getLogger(__name__)


def _name_from_source_url(url: str | None) -> str | None:
    """The scheme name an earlier AdvisorKhoj scrape requested, taken from `url`; None for other URLs."""
    if not url or "advisorkhoj.com" not in url:
        return None
    tail = unquote(url.rstrip("/").rsplit("/", 1)[-1]).strip()
    return tail or None


def _returned_same_fund(row: dict, scheme_code: int, our_base: str) -> bool:
    """Whether an AdvisorKhoj portfolio row belongs to our fund (any plan/option variant)."""
    try:
        got = int(row.get("scheme_code"))
    except (TypeError, ValueError):
        got = None
    if got == scheme_code:
        return True
    if got is not None:
        with get_session() as session:
            other = session.get(AmfiScheme, got)
        if other is not None:
            return same_fund(base_name(other.scheme_name, other.plan, other.option), our_base)
    common = row.get("scheme_amfi_common") or ""
    return bool(common) and same_fund(common, our_base)


def _store_advisorkhoj_slug(scheme_code: int, slug: str) -> None:
    with get_session() as session:
        reg = session.get(MfRegistry, scheme_code)
        if reg is not None and reg.advisorkhoj_slug != slug:
            reg.advisorkhoj_slug = slug
            session.add(reg)
            session.commit()


def resolve_and_fetch_portfolio(scheme_code: int) -> tuple[str, dict]:
    """Find the verified AdvisorKhoj key for a scheme → (key, portfolio payload).

    Tries the stored key, the earlier metadata spelling, then `name_candidates`; the winner is
    persisted on `mf_registry.advisorkhoj_slug`. Raises `SourceNotFound` when nothing verifies.
    """
    with get_session() as session:
        scheme = session.get(AmfiScheme, scheme_code)
        if scheme is None:
            raise SourceNotFound("AdvisorKhoj portfolio", str(scheme_code))
        reg = session.get(MfRegistry, scheme_code)
        meta = session.get(MfMetadata, scheme_code)
        stored = reg.advisorkhoj_slug if reg else None
        old_name = _name_from_source_url(meta.source_url if meta else None)
        name, plan, option = scheme.scheme_name, scheme.plan, scheme.option

    our_base = base_name(name, plan, option)
    keys = [stored] if stored else []
    keys += name_candidates(our_base, plan, option, known=[old_name] if old_name else None)

    tried: set[str] = set()
    for key in keys:
        if key in tried:
            continue
        tried.add(key)
        try:
            resp = fetch_portfolio(key)
        except SourceNotFound:
            continue
        rows = (resp.get("schemePortfolioAnalysisResponse") or {}).get("schemePortfolioList") or []
        if not rows:
            continue
        if _returned_same_fund(rows[0], scheme_code, our_base):
            if key != stored:
                _store_advisorkhoj_slug(scheme_code, key)
            return key, resp
        logger.debug(
            "AdvisorKhoj key %r answered with scheme %s, not %s — rejected",
            key,
            rows[0].get("scheme_code"),
            scheme_code,
        )
    raise SourceNotFound("AdvisorKhoj portfolio", name)


def resolve_advisorkhoj_slug(scheme_code: int) -> str | None:
    """The verified AdvisorKhoj key for a scheme (from `mf_registry` when stored), or None."""
    with get_session() as session:
        reg = session.get(MfRegistry, scheme_code)
        if reg is not None and reg.advisorkhoj_slug:
            return reg.advisorkhoj_slug
    try:
        key, _ = resolve_and_fetch_portfolio(scheme_code)
    except SourceNotFound:
        return None
    return key


def fetch_holdings_for_scheme(scheme_code: int) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Resolve, fetch and normalise one scheme's (holdings, sectors, assets), tagged with its own slug."""
    _, resp = resolve_and_fetch_portfolio(scheme_code)
    with get_session() as session:
        own_slug = make_slug(session.get(AmfiScheme, scheme_code).scheme_name)
    return (
        normalize_holdings(resp, own_slug),
        normalize_sector_allocation(resp, own_slug),
        normalize_asset_allocation(resp, own_slug),
    )
