"""Holdings, sector and asset allocation repository; public APIs take scheme slugs, tables are keyed on scheme_code."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import polars as pl
from sqlmodel import col, delete, func, select

from core.database import get_session
from core.models import AmfiScheme, MfAssetAllocation, MfHolding, MfSectorAllocation
from data.constants import HOLDINGS_FIELD_MAP
from data.repositories.advisorkhoj_keys import fetch_holdings_for_scheme
from mutual_funds.display import make_slug
from mutual_funds.table_schema import (
    ASSET_SCHEMA,
    HOLDINGS_SCHEMA,
    SECTOR_SCHEMA,
    empty_df,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _slug_to_code_map_cached() -> dict[str, int]:
    """`{slug → scheme_code}` from amfi_schemes, cached per process; callers that mutate amfi_schemes must
    `clear_slug_cache()` after commit."""
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
    """Stage one ORM row per frame row on `session` without committing; rows with an unresolvable slug are skipped."""
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
    """{scheme_name: holdings row count} aggregated in SQL."""
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


def delete_holdings_for_slugs(slugs: list[str]) -> int:
    """Delete every holdings/sector/asset row for the given slugs. Returns the number of resolved scheme_codes."""
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
    """Delete and re-insert one fund's holdings/sector/asset rows in a single transaction.

    Pass `scheme_code` when known; plan/option variants can share a slug.
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
