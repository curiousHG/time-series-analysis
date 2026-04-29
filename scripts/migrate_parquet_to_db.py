"""One-time migration: load existing Parquet files into PostgreSQL via ORM."""

import json
from pathlib import Path

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.database import get_session, init_schema
from core.models import (
    MfAssetAllocation,
    MfHolding,
    MfNav,
    MfRegistry,
    MfSectorAllocation,
    SchemeCodeMap,
    StockOhlcv,
)

PARQUET_DIR = Path("data/parquet")

# Polars → ORM field maps
HOLDINGS_MAP = {
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


def migrate():
    print("Initializing database schema...")
    init_schema()

    _migrate_nav()
    _migrate_registry()
    _migrate_holdings()
    _migrate_sectors()
    _migrate_assets()
    _migrate_stocks()
    _migrate_scheme_codes()
    _migrate_tradebook()

    print("\nMigration complete!")


def _migrate_nav():
    path = PARQUET_DIR / "mf_nav.parquet"
    if not path.exists():
        print("No NAV parquet found, skipping")
        return
    df = pl.read_parquet(path)
    with get_session() as session:
        for row in df.iter_rows(named=True):
            stmt = (
                pg_insert(MfNav)
                .values(date=row["date"], nav=row["nav"], scheme_name=row["schemeName"])
                .on_conflict_do_update(index_elements=["date", "scheme_name"], set_={"nav": row["nav"]})
            )
            session.exec(stmt)
        session.commit()
    print(f"Migrated {df.height} NAV rows")


def _migrate_registry():
    path = PARQUET_DIR / "advisorkhoj_registry.parquet"
    if not path.exists():
        print("No registry parquet found, skipping")
        return
    df = pl.read_parquet(path)
    with get_session() as session:
        for row in df.iter_rows(named=True):
            stmt = (
                pg_insert(MfRegistry)
                .values(
                    scheme_name=row["schemeName"],
                    scheme_slug=row["schemeSlug"],
                    source=row.get("source", "advisorkhoj"),
                )
                .on_conflict_do_nothing(index_elements=["scheme_name"])
            )
            session.exec(stmt)
        session.commit()
    print(f"Migrated {df.height} registry entries")


def _migrate_holdings():
    path = PARQUET_DIR / "mf_holdings.parquet"
    if not path.exists():
        print("No holdings parquet found, skipping")
        return
    df = pl.read_parquet(path)
    with get_session() as session:
        for row in df.iter_rows(named=True):
            obj = MfHolding(**{HOLDINGS_MAP[k]: row.get(k) for k in HOLDINGS_MAP if k in df.columns})
            session.add(obj)
        session.commit()
    print(f"Migrated {df.height} holdings rows")


def _migrate_sectors():
    path = PARQUET_DIR / "mf_sector_allocation.parquet"
    if not path.exists():
        print("No sector parquet found, skipping")
        return
    df = pl.read_parquet(path)
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
    print(f"Migrated {df.height} sector rows")


def _migrate_assets():
    path = PARQUET_DIR / "mf_asset_allocation.parquet"
    if not path.exists():
        print("No asset parquet found, skipping")
        return
    df = pl.read_parquet(path)
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
    print(f"Migrated {df.height} asset rows")


def _migrate_stocks():
    stocks_dir = PARQUET_DIR / "stocks"
    if not stocks_dir.exists():
        print("No stocks dir found, skipping")
        return
    for parquet_file in stocks_dir.glob("*.parquet"):
        symbol = parquet_file.stem
        df = pl.read_parquet(parquet_file)
        if df.height == 0:
            continue
        with get_session() as session:
            for row in df.iter_rows(named=True):
                stmt = (
                    pg_insert(StockOhlcv)
                    .values(
                        date=row["Date"],
                        symbol=symbol,
                        open=row.get("Open"),
                        high=row.get("High"),
                        low=row.get("Low"),
                        close=row.get("Close"),
                        volume=row.get("Volume"),
                    )
                    .on_conflict_do_nothing(index_elements=["date", "symbol"])
                )
                session.exec(stmt)
            session.commit()
        print(f"Migrated {df.height} OHLCV rows for {symbol}")


def _migrate_scheme_codes():
    path = Path("data/user/scheme_code_map.json")
    if not path.exists():
        print("No scheme code map found, skipping")
        return
    with open(path) as f:
        code_map = json.load(f)
    with get_session() as session:
        for name, code in code_map.items():
            stmt = (
                pg_insert(SchemeCodeMap)
                .values(scheme_name=name, scheme_code=code)
                .on_conflict_do_nothing(index_elements=["scheme_name"])
            )
            session.exec(stmt)
        session.commit()
    print(f"Migrated {len(code_map)} scheme code mappings")


def _migrate_tradebook():
    path = Path("data/user/tradebook-MF.csv")
    if not path.exists():
        print("No tradebook CSV found, skipping")
        return
    from data.repositories.tradebook import import_tradebook_csv

    new_count, skipped = import_tradebook_csv(str(path))
    print(f"Migrated tradebook: {new_count} new, {skipped} duplicates skipped")


if __name__ == "__main__":
    migrate()
