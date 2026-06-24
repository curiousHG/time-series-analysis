"""Holdings, sector, and asset allocation repository.

Tables are keyed on `scheme_code` (FK → amfi_schemes), but public APIs still take
`slugs: list[str]` — resolved to scheme_code via a cached `make_slug(scheme_name)` map.
Output frames re-derive `schemeSlug` / `schemeName` so downstream callers don't change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import polars as pl
from sqlmodel import col, delete, select

from core.database import get_session
from core.models import AmfiScheme, MfAssetAllocation, MfHolding, MfSectorAllocation
from data.constants import HOLDINGS_FIELD_MAP
from data.fetchers.mutual_fund import fetch_portfolio_by_slug
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


def _add_holding_rows(session, df: pl.DataFrame) -> None:
    """Stage MfHolding rows on `session` (no commit). Rows with unknown slugs are skipped."""
    if df.height == 0:
        return
    for row in df.iter_rows(named=True):
        slug = row.get("schemeSlug")
        code = _resolve_slug(slug) if slug else None
        if code is None:
            logger.warning("save_holdings: no scheme_code for slug %r — skipping", slug)
            continue
        session.add(_polars_row_to_holding(row, code))


def _add_sector_rows(session, df: pl.DataFrame) -> None:
    """Stage MfSectorAllocation rows on `session` (no commit)."""
    if df.height == 0:
        return
    for row in df.iter_rows(named=True):
        slug = row.get("schemeSlug")
        code = _resolve_slug(slug) if slug else None
        if code is None:
            continue
        session.add(
            MfSectorAllocation(
                scheme_code=code,
                portfolio_date=row.get("portfolioDate"),
                sector=row.get("sector"),
                weight=row.get("weight"),
            )
        )


def _add_asset_rows(session, df: pl.DataFrame) -> None:
    """Stage MfAssetAllocation rows on `session` (no commit)."""
    if df.height == 0:
        return
    for row in df.iter_rows(named=True):
        slug = row.get("schemeSlug")
        code = _resolve_slug(slug) if slug else None
        if code is None:
            continue
        session.add(
            MfAssetAllocation(
                scheme_code=code,
                portfolio_date=row.get("portfolioDate"),
                asset_class=row.get("assetClass"),
                weight=row.get("weight"),
            )
        )


def save_holdings(df: pl.DataFrame) -> None:
    if df.height == 0:
        return
    with get_session() as session:
        _add_holding_rows(session, df)
        session.commit()


def save_sectors(df: pl.DataFrame) -> None:
    if df.height == 0:
        return
    with get_session() as session:
        _add_sector_rows(session, df)
        session.commit()


def save_assets(df: pl.DataFrame) -> None:
    if df.height == 0:
        return
    with get_session() as session:
        _add_asset_rows(session, df)
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
) -> None:
    """Delete + re-insert one fund's holdings/sector/asset rows in a single transaction,
    so a failed insert never leaves a fund with holdings but no sectors/assets.
    """
    codes = _resolve_slugs([slug])
    if not codes:
        logger.warning("replace_holdings_atomic: no scheme_code for slug %r — skipping", slug)
        return
    code = codes[0]
    with get_session() as session:
        session.exec(delete(MfHolding).where(col(MfHolding.scheme_code) == code))
        session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_code) == code))
        session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_code) == code))
        _add_holding_rows(session, holdings)
        _add_sector_rows(session, sectors)
        _add_asset_rows(session, assets)
        session.commit()


# ---- ensure / refresh --------------------------------------------------------------------


def fetch_holdings_frames(slug: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Fetch and normalize one fund's holdings payload."""
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
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_slug = {pool.submit(fetch_holdings_frames, slug): slug for slug in missing}
            for future in as_completed(future_to_slug):
                slug = future_to_slug[future]
                try:
                    h, s, a = future.result()
                    save_holdings(h)
                    save_sectors(s)
                    save_assets(a)
                except Exception as e:
                    logger.error("Failed to fetch holdings for %s: %s", slug, e)

        holdings = load_holdings(slugs)
        sectors = load_sectors(slugs)
        assets = load_assets(slugs)

    return holdings, sectors, assets


def refresh_holdings_data(slugs: list[str]) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Re-fetch holdings data for given slugs, replacing successful fetches only."""
    fetched: list[tuple[str, pl.DataFrame, pl.DataFrame, pl.DataFrame]] = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_slug = {pool.submit(fetch_holdings_frames, slug): slug for slug in slugs}
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                h, s, a = future.result()
                fetched.append((slug, h, s, a))
            except Exception as e:
                logger.error("Failed to refresh holdings for %s: %s", slug, e)

    for slug, h, s, a in fetched:
        replace_holdings_atomic(slug, h, s, a)

    return load_holdings(slugs), load_sectors(slugs), load_assets(slugs)
