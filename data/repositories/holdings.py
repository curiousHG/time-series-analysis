"""Holdings, sector, and asset allocation repository.

Phase 3: tables are now keyed on `scheme_code` (FK → amfi_schemes). Public APIs still take
`slugs: list[str]` for caller convenience — we resolve slug → scheme_code internally via a
cached `make_slug(amfi_scheme_name)` lookup. Output frames keep `schemeSlug` / `schemeName`
columns derived from the JOIN so downstream callers don't need to change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import polars as pl
from sqlmodel import col, delete, select

from core.database import get_session
from core.models import AmfiScheme, MfAssetAllocation, MfHolding, MfSectorAllocation
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


# Polars column → ORM field mapping (post-Phase-3 — only fields that still exist on MfHolding).
_HOLDINGS_FIELD_MAP = {
    "portfolioDate": "portfolio_date",
    "instrumentName": "instrument_name",
    "isin": "isin",
    "issuerName": "issuer_name",
    "assetClass": "asset_class",
    "assetSubClass": "asset_sub_class",
    "assetType": "asset_type",
    "weight": "weight",
    "value": "value",
    "quantity": "quantity",
    "industry": "industry",
    "marketCapBucket": "market_cap",
    "creditRating": "credit_rating",
    "creditRatingEq": "credit_rating_eq",
}


# ---- Slug ↔ scheme_code resolution -------------------------------------------------------


@lru_cache(maxsize=1)
def _slug_to_code_map_cached() -> dict[str, int]:
    """Build `{slug → scheme_code}` from amfi_schemes. Process-level cache (LRU size 1).

    Phase 3 dropped the persisted `scheme_slug` column, so this is the only way to
    reverse-resolve slug → code. Every code path that mutates `amfi_schemes` MUST
    call `clear_slug_cache()` after commit, otherwise newly inserted schemes are
    invisible to holdings save/load until process restart. Mutators today:

      • `data/repositories/amfi.py:sync_amfi_master` — bulk AMFI upsert.
      • `data/repositories/nav.py:save_nav_df` — synthetic-mint branch.
      • `data/repositories/metadata.py:save_metadata` — synthetic-mint branch.
      • `services/registry_service.py:_resolve_or_mint_code` — synthetic-mint.
      • `scripts/dedupe_synthetic_codes.py` — merges synthetic rows.
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
    fields = {_HOLDINGS_FIELD_MAP[k]: row.get(k) for k in _HOLDINGS_FIELD_MAP}
    return MfHolding(scheme_code=scheme_code, **fields)  # type: ignore[arg-type]


def save_holdings(df: pl.DataFrame) -> None:
    if df.height == 0:
        return
    rows: list[MfHolding] = []
    for row in df.iter_rows(named=True):
        slug = row.get("schemeSlug")
        code = _resolve_slug(slug) if slug else None
        if code is None:
            logger.warning("save_holdings: no scheme_code for slug %r — skipping", slug)
            continue
        rows.append(_polars_row_to_holding(row, code))
    if not rows:
        return
    with get_session() as session:
        session.add_all(rows)
        session.commit()


def save_sectors(df: pl.DataFrame) -> None:
    if df.height == 0:
        return
    with get_session() as session:
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
        session.commit()


def save_assets(df: pl.DataFrame) -> None:
    if df.height == 0:
        return
    with get_session() as session:
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
        session.commit()


# ---- Load paths --------------------------------------------------------------------------
#
# Public API still takes `slugs` for caller compat. Internally we resolve slugs → codes,
# read by code, and re-derive `schemeSlug` / `schemeName` from amfi_schemes for the output.
#
# Every loader returns the LATEST `portfolio_date` per scheme, deduplicated by the natural
# key. `mf_holdings` / `mf_sector_allocation` / `mf_asset_allocation` historically picked
# up duplicate rows from refresh cycles that didn't `DELETE` first, plus old snapshots
# from earlier `portfolio_date`s. Callers always want the active portfolio.


def _latest_per_scheme(df: pl.DataFrame, dedup_keys: list[str]) -> pl.DataFrame:
    """Keep only the latest `portfolioDate` row per scheme, deduped by `dedup_keys`."""
    if df.is_empty() or "portfolioDate" not in df.columns:
        return df
    latest = df.group_by("schemeCode").agg(
        pl.col("portfolioDate").max().alias("_latest")
    )
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
    """Delete every holdings/sector/asset row for the given slugs. Returns the number
    of slugs whose rows were targeted (0 if no slug resolved to a known scheme_code).
    Used by the sync service before a full refetch.
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


# ---- ensure / refresh --------------------------------------------------------------------


def ensure_holdings_data(slugs: list[str]) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    holdings = load_holdings(slugs)
    sectors = load_sectors(slugs)
    assets = load_assets(slugs)

    existing = set(holdings["schemeSlug"].unique().to_list()) if holdings.height else set()
    missing = set(slugs) - existing

    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_slug = {pool.submit(fetch_portfolio_by_slug, slug): slug for slug in missing}
            for future in as_completed(future_to_slug):
                slug = future_to_slug[future]
                try:
                    resp = future.result()
                    save_holdings(normalize_holdings(resp, slug))
                    save_sectors(normalize_sector_allocation(resp, slug))
                    save_assets(normalize_asset_allocation(resp, slug))
                except Exception as e:
                    logger.error("Failed to fetch holdings for %s: %s", slug, e)

        holdings = load_holdings(slugs)
        sectors = load_sectors(slugs)
        assets = load_assets(slugs)

    return holdings, sectors, assets


def refresh_holdings_data(slugs: list[str]) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Re-fetch holdings data for given slugs, replacing existing entries."""
    codes = _resolve_slugs(slugs)
    if codes:
        with get_session() as session:
            session.exec(delete(MfHolding).where(col(MfHolding.scheme_code).in_(codes)))
            session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_code).in_(codes)))
            session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_code).in_(codes)))
            session.commit()

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_slug = {pool.submit(fetch_portfolio_by_slug, slug): slug for slug in slugs}
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                resp = future.result()
                save_holdings(normalize_holdings(resp, slug))
                save_sectors(normalize_sector_allocation(resp, slug))
                save_assets(normalize_asset_allocation(resp, slug))
            except Exception as e:
                logger.error("Failed to refresh holdings for %s: %s", slug, e)

    return load_holdings(slugs), load_sectors(slugs), load_assets(slugs)
