"""Holdings, sector, and asset allocation repository.

Tables are keyed on `scheme_code` (FK → amfi_schemes), but public APIs still take
`slugs: list[str]` — resolved to scheme_code via a cached `make_slug(scheme_name)` map.
Output frames re-derive `schemeSlug` / `schemeName` so downstream callers don't change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from urllib.parse import unquote

import polars as pl
from sqlmodel import col, delete, func, select

from core.database import get_session
from core.models import AmfiScheme, MfAssetAllocation, MfHolding, MfMetadata, MfRegistry, MfSectorAllocation
from data.constants import HOLDINGS_FIELD_MAP
from data.exceptions import SourceNotFound
from data.fetchers.mutual_fund import fetch_portfolio_by_slug
from mutual_funds.advisorkhoj import base_name, name_candidates, same_fund
from mutual_funds.display import make_slug
from mutual_funds.holdings import (
    normalize_asset_allocation,
    normalize_holdings,
    normalize_sector_allocation,
)
from mutual_funds.table_schema import (
    ASSET_SCHEMA,
    HOLDINGS_SCHEMA,
    SECTOR_SCHEMA,
    empty_df,
)

logger = logging.getLogger("data.repositories.holdings")


# ---- Slug ↔ scheme_code resolution -------------------------------------------------------


@lru_cache(maxsize=1)
def _slug_to_code_map_cached() -> dict[str, int]:
    """Build `{slug → scheme_code}` from amfi_schemes. Process-level LRU cache (size 1).

    The persisted `scheme_slug` column was dropped, so this is the only slug → code
    path. Every code path that mutates `amfi_schemes` MUST call `clear_slug_cache()`
    after commit, else newly inserted schemes stay invisible to holdings save/load
    until process restart (mutators: sync_amfi_master, save_nav_df / save_metadata
    synthetic-mint branches, registry_service._resolve_or_mint_code,
    scripts/dedupe_synthetic_codes.py).
    """
    with get_session() as session:
        rows = session.exec(select(AmfiScheme.scheme_code, AmfiScheme.scheme_name)).all()
    out: dict[str, int] = {}
    for code, name in rows:
        if name:
            s = make_slug(name)
            if s:
                out[s] = code
    return out


def slug_to_code_map() -> dict[str, int]:
    """Public read of the cached slug → scheme_code map."""
    return _slug_to_code_map_cached()


def clear_slug_cache() -> None:
    """Drop the cached slug → scheme_code map. Call after AMFI sync or dedupe."""
    _slug_to_code_map_cached.cache_clear()


def _resolve_slug(slug: str) -> int | None:
    return _slug_to_code_map_cached().get(slug)


def _resolve_slugs(slugs: list[str]) -> list[int]:
    m = _slug_to_code_map_cached()
    return [m[s] for s in slugs if s in m]


# ---- Save paths --------------------------------------------------------------------------


def _polars_row_to_holding(row: dict, scheme_code: int) -> MfHolding:
    fields = {HOLDINGS_FIELD_MAP[k]: row.get(k) for k in HOLDINGS_FIELD_MAP}
    return MfHolding(scheme_code=scheme_code, **fields)  # type: ignore[arg-type]


def _sector_row(row: dict, code: int) -> MfSectorAllocation:
    return MfSectorAllocation(
        scheme_code=code, portfolio_date=row.get("portfolioDate"), sector=row.get("sector"), weight=row.get("weight")
    )


def _asset_row(row: dict, code: int) -> MfAssetAllocation:
    return MfAssetAllocation(
        scheme_code=code,
        portfolio_date=row.get("portfolioDate"),
        asset_class=row.get("assetClass"),
        weight=row.get("weight"),
    )


def _stage_rows(session, df: pl.DataFrame, build, scheme_code: int | None = None) -> None:
    """Stage one ORM row per frame row on `session` (no commit). A row whose slug resolves to no
    scheme is skipped with a warning."""
    for row in df.iter_rows(named=True):
        code = scheme_code if scheme_code is not None else _resolve_slug(row.get("schemeSlug") or "")
        if code is None:
            logger.warning("%s: no scheme_code for slug %r — skipping", build.__name__, row.get("schemeSlug"))
            continue
        session.add(build(row, code))


def _add_holding_rows(session, df: pl.DataFrame, scheme_code: int | None = None) -> None:
    _stage_rows(session, df, _polars_row_to_holding, scheme_code)


def _add_sector_rows(session, df: pl.DataFrame, scheme_code: int | None = None) -> None:
    _stage_rows(session, df, _sector_row, scheme_code)


def _add_asset_rows(session, df: pl.DataFrame, scheme_code: int | None = None) -> None:
    _stage_rows(session, df, _asset_row, scheme_code)


def save_holdings(df: pl.DataFrame, *, scheme_code: int | None = None) -> None:
    if df.height == 0:
        return
    with get_session() as session:
        _add_holding_rows(session, df, scheme_code)
        session.commit()


def save_sectors(df: pl.DataFrame, *, scheme_code: int | None = None) -> None:
    if df.height == 0:
        return
    with get_session() as session:
        _add_sector_rows(session, df, scheme_code)
        session.commit()


def save_assets(df: pl.DataFrame, *, scheme_code: int | None = None) -> None:
    if df.height == 0:
        return
    with get_session() as session:
        _add_asset_rows(session, df, scheme_code)
        session.commit()


# ---- Load paths --------------------------------------------------------------------------
#
# Every loader returns only the LATEST `portfolio_date` per scheme, deduped by the natural
# key — old snapshots and duplicate rows from refresh cycles that didn't DELETE first would
# otherwise leak in, but callers always want the active portfolio.


def _latest_per_scheme(df: pl.DataFrame, dedup_keys: list[str]) -> pl.DataFrame:
    """Keep only the latest `portfolioDate` row per scheme, deduped by `dedup_keys`."""
    if df.is_empty() or "portfolioDate" not in df.columns:
        return df
    latest = df.group_by("schemeCode").agg(pl.col("portfolioDate").max().alias("_latest"))
    return (
        df.join(latest, on="schemeCode", how="inner")
        .filter(pl.col("portfolioDate") == pl.col("_latest"))
        .drop("_latest")
        .unique(subset=dedup_keys, keep="first")
    )


def holdings_count_by_scheme(scheme_names: list[str] | None = None) -> dict[str, int]:
    """Per-scheme holdings row count, keyed by scheme name. An aggregate for the Settings status
    tables — avoids loading every holding row just to count them."""
    with get_session() as session:
        stmt = (
            select(AmfiScheme.scheme_name, func.count(col(MfHolding.scheme_code)))
            .join(AmfiScheme, MfHolding.scheme_code == AmfiScheme.scheme_code)
            .group_by(col(AmfiScheme.scheme_name))
        )
        if scheme_names:
            stmt = stmt.where(col(AmfiScheme.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()
    return dict(rows)


def load_holdings(slugs: list[str] | None = None) -> pl.DataFrame:
    codes = _resolve_slugs(slugs) if slugs else None
    with get_session() as session:
        stmt = select(MfHolding, AmfiScheme.scheme_name).join(
            AmfiScheme, MfHolding.scheme_code == AmfiScheme.scheme_code
        )
        if codes:
            stmt = stmt.where(col(MfHolding.scheme_code).in_(codes))
        rows = session.exec(stmt).all()
    if not rows:
        return empty_df(HOLDINGS_SCHEMA)
    df = pl.DataFrame(
        [
            {
                "schemeCode": h.scheme_code,
                "schemeName": name,
                "schemeSlug": make_slug(name),
                "schemeCommon": None,
                "portfolioDate": h.portfolio_date,
                "instrumentName": h.instrument_name,
                "isin": h.isin,
                "issuerName": h.issuer_name,
                "assetClass": h.asset_class,
                "assetSubClass": h.asset_sub_class,
                "assetType": h.asset_type,
                "weight": h.weight,
                "value": h.value,
                "quantity": h.quantity,
                "industry": h.industry,
                "marketCapBucket": h.market_cap,
                "creditRating": h.credit_rating,
                "creditRatingEq": h.credit_rating_eq,
            }
            for h, name in rows
        ]
    )
    return _latest_per_scheme(df, dedup_keys=["schemeCode", "instrumentName", "isin"])


def load_sectors(slugs: list[str] | None = None) -> pl.DataFrame:
    codes = _resolve_slugs(slugs) if slugs else None
    with get_session() as session:
        stmt = select(MfSectorAllocation, AmfiScheme.scheme_name).join(
            AmfiScheme, MfSectorAllocation.scheme_code == AmfiScheme.scheme_code
        )
        if codes:
            stmt = stmt.where(col(MfSectorAllocation.scheme_code).in_(codes))
        rows = session.exec(stmt).all()
    if not rows:
        return empty_df(SECTOR_SCHEMA)
    df = pl.DataFrame(
        [
            {
                "schemeCode": s.scheme_code,
                "schemeName": name,
                "schemeSlug": make_slug(name),
                "portfolioDate": s.portfolio_date,
                "sector": s.sector,
                "weight": s.weight,
            }
            for s, name in rows
        ]
    )
    return _latest_per_scheme(df, dedup_keys=["schemeCode", "sector"])


def load_assets(slugs: list[str] | None = None) -> pl.DataFrame:
    codes = _resolve_slugs(slugs) if slugs else None
    with get_session() as session:
        stmt = select(MfAssetAllocation, AmfiScheme.scheme_name).join(
            AmfiScheme, MfAssetAllocation.scheme_code == AmfiScheme.scheme_code
        )
        if codes:
            stmt = stmt.where(col(MfAssetAllocation.scheme_code).in_(codes))
        rows = session.exec(stmt).all()
    if not rows:
        return empty_df(ASSET_SCHEMA)
    df = pl.DataFrame(
        [
            {
                "schemeCode": a.scheme_code,
                "schemeName": name,
                "schemeSlug": make_slug(name),
                "portfolioDate": a.portfolio_date,
                "assetClass": a.asset_class,
                "weight": a.weight,
            }
            for a, name in rows
        ]
    )
    return _latest_per_scheme(df, dedup_keys=["schemeCode", "assetClass"])


# ---- delete helpers ---------------------------------------------------------------------


def delete_holdings_for_slugs(slugs: list[str]) -> int:
    """Delete every holdings/sector/asset row for the given slugs. Returns count of
    resolved scheme_codes (0 if none resolved).
    """
    if not slugs:
        return 0
    codes = _resolve_slugs(slugs)
    if not codes:
        return 0
    with get_session() as session:
        session.exec(delete(MfHolding).where(col(MfHolding.scheme_code).in_(codes)))
        session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_code).in_(codes)))
        session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_code).in_(codes)))
        session.commit()
    return len(codes)


def replace_holdings_atomic(
    slug: str,
    holdings: pl.DataFrame,
    sectors: pl.DataFrame,
    assets: pl.DataFrame,
    *,
    scheme_code: int | None = None,
) -> None:
    """Delete + re-insert one fund's holdings/sector/asset rows in a single transaction,
    so a failed insert never leaves a fund with holdings but no sectors/assets.

    Pass `scheme_code` whenever the caller knows it: plan/option variants of a fund share a
    slug, so resolving the slug alone can file the rows under a sibling scheme.
    """
    code = scheme_code
    if code is None:
        codes = _resolve_slugs([slug])
        if not codes:
            logger.warning("replace_holdings_atomic: no scheme_code for slug %r — skipping", slug)
            return
        code = codes[0]
    with get_session() as session:
        session.exec(delete(MfHolding).where(col(MfHolding.scheme_code) == code))
        session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_code) == code))
        session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_code) == code))
        _add_holding_rows(session, holdings, code)
        _add_sector_rows(session, sectors, code)
        _add_asset_rows(session, assets, code)
        session.commit()


# ---- ensure / refresh --------------------------------------------------------------------


def _name_from_source_url(url: str | None) -> str | None:
    """The scheme name an earlier AdvisorKhoj scrape requested — AMFI's pre-2025 spelling, which
    AdvisorKhoj still keys by. None for non-AdvisorKhoj URLs."""
    if not url or "advisorkhoj.com" not in url:
        return None
    tail = unquote(url.rstrip("/").rsplit("/", 1)[-1]).strip()
    return tail or None


def _returned_same_fund(row: dict, scheme_code: int, our_base: str) -> bool:
    """Whether an AdvisorKhoj portfolio row belongs to our fund (any plan/option variant of it —
    variants share one portfolio). AdvisorKhoj fuzzy-matches unknown slugs onto *other* funds,
    so every hit is checked before it is stored."""
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
    """Find the AdvisorKhoj slug for a scheme and return (slug, portfolio payload).

    Tries, in order: the slug stored from an earlier success, the spelling an earlier
    metadata scrape used, then the generated candidates from `name_candidates`. A hit counts
    only if the returned rows belong to the same fund; the winning slug is persisted on
    `mf_registry` so later refreshes are one request. Raises `SourceNotFound` when nothing
    verifies.
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
    slugs = [stored] if stored else []
    slugs += [make_slug(n) for n in name_candidates(our_base, plan, option, known=[old_name] if old_name else None)]

    tried: set[str] = set()
    for slug in slugs:
        if slug in tried:
            continue
        tried.add(slug)
        try:
            resp = fetch_portfolio_by_slug(slug)
        except SourceNotFound:
            continue
        rows = (resp.get("schemePortfolioAnalysisResponse") or {}).get("schemePortfolioList") or []
        if not rows:
            continue
        if _returned_same_fund(rows[0], scheme_code, our_base):
            if slug != stored:
                _store_advisorkhoj_slug(scheme_code, slug)
            return slug, resp
        logger.debug(
            "AdvisorKhoj slug %r answered with scheme %s, not %s — rejected",
            slug,
            rows[0].get("scheme_code"),
            scheme_code,
        )
    raise SourceNotFound("AdvisorKhoj portfolio", name)


def resolve_advisorkhoj_slug(scheme_code: int) -> str | None:
    """The verified AdvisorKhoj slug for a scheme, or None when nothing on AdvisorKhoj matches.
    Served from `mf_registry.advisorkhoj_slug` after the first success."""
    with get_session() as session:
        reg = session.get(MfRegistry, scheme_code)
        if reg is not None and reg.advisorkhoj_slug:
            return reg.advisorkhoj_slug
    try:
        slug, _ = resolve_and_fetch_portfolio(scheme_code)
    except SourceNotFound:
        return None
    return slug


def fetch_holdings_for_scheme(scheme_code: int) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Resolve + fetch + normalise one scheme's holdings. Frames carry the scheme's *own*
    slug (`make_slug(scheme_name)`) regardless of which AdvisorKhoj spelling resolved, so the
    existing slug-keyed loaders keep working; save with `scheme_code=` to avoid the slug map."""
    _, resp = resolve_and_fetch_portfolio(scheme_code)
    with get_session() as session:
        own_slug = make_slug(session.get(AmfiScheme, scheme_code).scheme_name)
    return (
        normalize_holdings(resp, own_slug),
        normalize_sector_allocation(resp, own_slug),
        normalize_asset_allocation(resp, own_slug),
    )


def fetch_holdings_frames(slug: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Fetch and normalize one fund's holdings payload by raw slug (no resolution)."""
    resp = fetch_portfolio_by_slug(slug)
    return (
        normalize_holdings(resp, slug),
        normalize_sector_allocation(resp, slug),
        normalize_asset_allocation(resp, slug),
    )


def ensure_holdings_data(slugs: list[str]) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    holdings = load_holdings(slugs)
    sectors = load_sectors(slugs)
    assets = load_assets(slugs)

    existing = set(holdings["schemeSlug"].unique().to_list()) if holdings.height else set()
    missing = set(slugs) - existing

    if missing:
        code_map = _slug_to_code_map_cached()
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_code = {
                pool.submit(fetch_holdings_for_scheme, code_map[slug]): code_map[slug]
                for slug in missing
                if slug in code_map
            }
            for future in as_completed(future_to_code):
                code = future_to_code[future]
                try:
                    h, s, a = future.result()
                    save_holdings(h, scheme_code=code)
                    save_sectors(s, scheme_code=code)
                    save_assets(a, scheme_code=code)
                except Exception as e:
                    logger.error("Failed to fetch holdings for scheme %s: %s", code, e)

        holdings = load_holdings(slugs)
        sectors = load_sectors(slugs)
        assets = load_assets(slugs)

    return holdings, sectors, assets


def refresh_holdings_data(slugs: list[str]) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Re-fetch holdings data for given slugs, replacing successful fetches only."""
    fetched: list[tuple[str, int, pl.DataFrame, pl.DataFrame, pl.DataFrame]] = []
    code_map = _slug_to_code_map_cached()

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_slug = {
            pool.submit(fetch_holdings_for_scheme, code_map[slug]): slug for slug in slugs if slug in code_map
        }
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                h, s, a = future.result()
                fetched.append((slug, code_map[slug], h, s, a))
            except Exception as e:
                logger.error("Failed to refresh holdings for %s: %s", slug, e)

    for slug, code, h, s, a in fetched:
        replace_holdings_atomic(slug, h, s, a, scheme_code=code)

    return load_holdings(slugs), load_sectors(slugs), load_assets(slugs)
