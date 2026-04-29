"""Holdings, sector, and asset allocation repository."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl
from sqlmodel import col, delete, select

from core.database import get_session
from core.models import MfAssetAllocation, MfHolding, MfSectorAllocation
from data.fetchers.mutual_fund import fetch_portfolio_by_slug
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

# Polars column → ORM field mapping
_HOLDINGS_FIELD_MAP = {
    "schemeCode": "scheme_code",
    "schemeName": "scheme_name",
    "schemeSlug": "scheme_slug",
    "schemeCommon": "scheme_common",
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


def _polars_row_to_holding(row: dict) -> MfHolding:
    fields = {_HOLDINGS_FIELD_MAP[k]: row.get(k) for k in _HOLDINGS_FIELD_MAP}
    fields["scheme_slug"] = fields.get("scheme_slug") or ""
    return MfHolding(**fields)  # type: ignore[arg-type]


def _holding_to_dict(h: MfHolding) -> dict:
    inv = {v: k for k, v in _HOLDINGS_FIELD_MAP.items()}
    return {inv[c]: getattr(h, c) for c in inv}


def save_holdings(df: pl.DataFrame):
    if df.height == 0:
        return
    with get_session() as session:
        session.add_all([_polars_row_to_holding(row) for row in df.iter_rows(named=True)])
        session.commit()


def save_sectors(df: pl.DataFrame):
    if df.height == 0:
        return
    with get_session() as session:
        for row in df.iter_rows(named=True):
            session.add(
                MfSectorAllocation(
                    scheme_code=row.get("schemeCode"),
                    scheme_name=row.get("schemeName"),
                    scheme_slug=row.get("schemeSlug"),
                    portfolio_date=row.get("portfolioDate"),
                    sector=row.get("sector"),
                    weight=row.get("weight"),
                )
            )
        session.commit()


def save_assets(df: pl.DataFrame):
    if df.height == 0:
        return
    with get_session() as session:
        for row in df.iter_rows(named=True):
            session.add(
                MfAssetAllocation(
                    scheme_code=row.get("schemeCode"),
                    scheme_name=row.get("schemeName"),
                    scheme_slug=row.get("schemeSlug"),
                    portfolio_date=row.get("portfolioDate"),
                    asset_class=row.get("assetClass"),
                    weight=row.get("weight"),
                )
            )
        session.commit()


def load_holdings(slugs: list[str] | None = None) -> pl.DataFrame:
    with get_session() as session:
        stmt = select(MfHolding)
        if slugs:
            stmt = stmt.where(col(MfHolding.scheme_slug).in_(slugs))
        rows = session.exec(stmt).all()
    if not rows:
        return empty_df(HOLDINGS_SCHEMA)
    return pl.DataFrame([_holding_to_dict(r) for r in rows])


def load_sectors(slugs: list[str] | None = None) -> pl.DataFrame:
    with get_session() as session:
        stmt = select(MfSectorAllocation)
        if slugs:
            stmt = stmt.where(col(MfSectorAllocation.scheme_slug).in_(slugs))
        rows = session.exec(stmt).all()
    if not rows:
        return empty_df(SECTOR_SCHEMA)
    return pl.DataFrame(
        [
            {
                "schemeCode": r.scheme_code,
                "schemeName": r.scheme_name,
                "schemeSlug": r.scheme_slug,
                "portfolioDate": r.portfolio_date,
                "sector": r.sector,
                "weight": r.weight,
            }
            for r in rows
        ]
    )


def load_assets(slugs: list[str] | None = None) -> pl.DataFrame:
    with get_session() as session:
        stmt = select(MfAssetAllocation)
        if slugs:
            stmt = stmt.where(col(MfAssetAllocation.scheme_slug).in_(slugs))
        rows = session.exec(stmt).all()
    if not rows:
        return empty_df(ASSET_SCHEMA)
    return pl.DataFrame(
        [
            {
                "schemeCode": r.scheme_code,
                "schemeName": r.scheme_name,
                "schemeSlug": r.scheme_slug,
                "portfolioDate": r.portfolio_date,
                "assetClass": r.asset_class,
                "weight": r.weight,
            }
            for r in rows
        ]
    )


def ensure_holdings_data(
    slugs: list[str],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
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


def refresh_holdings_data(
    slugs: list[str],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Re-fetch holdings data for given slugs, replacing existing entries."""
    with get_session() as session:
        session.exec(delete(MfHolding).where(col(MfHolding.scheme_slug).in_(slugs)))
        session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_slug).in_(slugs)))
        session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_slug).in_(slugs)))
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
