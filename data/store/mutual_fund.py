import logging
import polars as pl
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlmodel import select, delete, col
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.database import get_session
from core.models import (
    MfNav,
    MfHolding,
    MfSectorAllocation,
    MfAssetAllocation,
    MfRegistry,
    SchemeCodeMap,
    FundMapping,
)
from data.fetchers.mutual_fund import (
    fetch_portfolio_by_slug,
    fetch_nav_from_mfapi,
    fetch_nav_from_advisorkhoj,
    resolve_mfapi_code,
)
from mutual_funds.holdings import (
    normalize_holdings,
    normalize_sector_allocation,
    normalize_asset_allocation,
)
from mutual_funds.table_schema import (
    HOLDINGS_SCHEMA,
    SECTOR_SCHEMA,
    ASSET_SCHEMA,
    empty_df,
)

logger = logging.getLogger("data.store.mutualfund")

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


# ---- Scheme code map ----


def _load_scheme_code_map() -> dict[str, str]:
    with get_session() as session:
        rows = session.execute(select(SchemeCodeMap)).scalars().all()
        return {r.scheme_name: r.scheme_code for r in rows}


def _save_scheme_code_map(code_map: dict[str, str]):
    with get_session() as session:
        for name, code in code_map.items():
            stmt = (
                pg_insert(SchemeCodeMap)
                .values(scheme_name=name, scheme_code=code)
                .on_conflict_do_update(
                    index_elements=["scheme_name"],
                    set_={"scheme_code": code},
                )
            )
            session.execute(stmt)
        session.commit()


def _get_or_resolve_scheme_code(
    scheme_name: str, code_map: dict[str, str]
) -> str | None:
    if scheme_name in code_map:
        return code_map[scheme_name]
    code = resolve_mfapi_code(scheme_name)
    if code:
        code_map[scheme_name] = code
    return code


# ---- Fund mapping ----


def persist_fund_mapping(fund_mapping: pd.DataFrame):
    if fund_mapping is None or fund_mapping.empty:
        return
    with get_session() as session:
        session.execute(delete(FundMapping))
        for _, row in fund_mapping.iterrows():
            session.add(
                FundMapping(
                    trade_symbol=row["Trade Symbol"],
                    mapped_nav_fund=row["Mapped NAV Fund"],
                )
            )
        session.commit()
    logger.info("Persisted %d fund mappings", len(fund_mapping))


def auto_map_tradebook() -> dict[str, str]:
    """
    Auto-map all tradebook ISINs to AMFI scheme names.
    Returns dict of {trade_symbol: scheme_name} for successfully mapped funds.
    Also saves mappings to DB and ensures NAV data is fetched.
    """
    from data.store.tradebook import load_tradebook_from_db
    from data.store.amfi import lookup_by_isin

    tb = load_tradebook_from_db()
    if tb.is_empty():
        return {}

    # Get unique (symbol, isin) pairs
    pairs = tb.select(["symbol", "isin"]).unique()
    mappings: dict[str, str] = {}
    nav_to_fetch: list[tuple[str, str]] = []  # (scheme_code, scheme_name)

    for row in pairs.iter_rows(named=True):
        symbol = row["symbol"]
        isin = row["isin"]

        scheme = lookup_by_isin(isin)
        if scheme:
            mappings[symbol] = scheme.scheme_name
            nav_to_fetch.append((str(scheme.scheme_code), scheme.scheme_name))
            logger.info("ISIN %s → %s (code %d)", isin, scheme.scheme_name, scheme.scheme_code)
        else:
            logger.warning("No AMFI scheme found for ISIN %s (symbol: %s)", isin, symbol)

    # Save mappings to fund_mapping table
    if mappings:
        with get_session() as session:
            for symbol, scheme_name in mappings.items():
                stmt = (
                    pg_insert(FundMapping)
                    .values(trade_symbol=symbol, mapped_nav_fund=scheme_name)
                    .on_conflict_do_update(
                        index_elements=["trade_symbol"],
                        set_={"mapped_nav_fund": scheme_name},
                    )
                )
                session.execute(stmt)
            session.commit()

        # Also add to registry
        save_to_registry(list(mappings.values()))

        # Fetch NAV for any schemes not yet in DB
        existing_nav = _load_nav_df(list(mappings.values()))
        existing_schemes = set(
            existing_nav.select("schemeName").unique().to_series().to_list()
        ) if existing_nav.height else set()

        for code, name in nav_to_fetch:
            if name not in existing_schemes:
                try:
                    df = fetch_nav_from_mfapi(code, name)
                    _save_nav_df(df)
                    logger.info("Fetched NAV for %s (%d rows)", name, df.height)
                except Exception as e:
                    logger.error("Failed to fetch NAV for %s: %s", name, e)

    logger.info("Auto-mapped %d/%d tradebook symbols", len(mappings), pairs.height)
    return mappings


def ensure_fund_mapping() -> pd.DataFrame | None:
    with get_session() as session:
        rows = session.execute(select(FundMapping)).scalars().all()
    if not rows:
        return None
    return pd.DataFrame(
        [{"Trade Symbol": r.trade_symbol, "Mapped NAV Fund": r.mapped_nav_fund} for r in rows]
    )


# ---- NAV data ----


def nav_json_to_df(nav_json: list[list], scheme_name: str) -> pl.DataFrame:
    cleaned = [
        {"ts_ms": int(row[0]), "nav": float(row[1])}
        for row in nav_json
        if row and row[1] is not None
    ]
    return (
        pl.DataFrame(cleaned)
        .with_columns(
            pl.from_epoch(pl.col("ts_ms"), time_unit="ms").dt.date().alias("date"),
            pl.col("nav").alias("nav"),
            pl.lit(scheme_name).alias("schemeName"),
        )
        .select("date", "nav", "schemeName")
        .sort("date")
        .unique(subset=["date", "schemeName"], keep="last")
    )


def _save_nav_df(df: pl.DataFrame):
    """Upsert NAV rows into the database."""
    if df.height == 0:
        return
    with get_session() as session:
        for row in df.iter_rows(named=True):
            stmt = (
                pg_insert(MfNav)
                .values(date=row["date"], nav=row["nav"], scheme_name=row["schemeName"])
                .on_conflict_do_update(
                    index_elements=["date", "scheme_name"],
                    set_={"nav": row["nav"]},
                )
            )
            session.execute(stmt)
        session.commit()
    logger.info("Saved %d NAV rows to database", df.height)


def _load_nav_df(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Load NAV data from database, optionally filtered by scheme names."""
    with get_session() as session:
        stmt = select(MfNav).order_by(col(MfNav.date))
        if scheme_names:
            stmt = stmt.where(col(MfNav.scheme_name).in_(scheme_names))
        rows = session.execute(stmt).scalars().all()

    if not rows:
        return pl.DataFrame(
            schema={"date": pl.Date, "nav": pl.Float64, "schemeName": pl.Utf8}
        )
    return pl.DataFrame(
        {
            "date": [r.date for r in rows],
            "nav": [r.nav for r in rows],
            "schemeName": [r.scheme_name for r in rows],
        }
    )


def ensure_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """Ensures NAV data exists in DB for given scheme names."""
    nav_df = _load_nav_df(scheme_names)
    existing = nav_df.select("schemeName").unique().to_series().to_list() if nav_df.height else []

    missing = list(set(scheme_names) - set(existing))
    if not missing:
        return nav_df

    new_frames = _fetch_nav_parallel(missing)
    for df in new_frames:
        _save_nav_df(df)

    return _load_nav_df(scheme_names)


def _fetch_single_nav(scheme_name: str, code_map: dict[str, str]) -> pl.DataFrame:
    """Fetch NAV for a single scheme. Tries MFAPI first, falls back to AdvisorKhoj."""
    scheme_code = _get_or_resolve_scheme_code(scheme_name, code_map)
    if scheme_code:
        try:
            return fetch_nav_from_mfapi(scheme_code, scheme_name)
        except Exception as e:
            logger.warning("MFAPI failed for %s (code=%s): %s", scheme_name, scheme_code, e)

    logger.info("Falling back to AdvisorKhoj for NAV: %s", scheme_name)
    data = fetch_nav_from_advisorkhoj(scheme_name)
    return nav_json_to_df(data["nav_data"], scheme_name)


def _fetch_nav_parallel(scheme_names: list[str]) -> list[pl.DataFrame]:
    """Fetch NAV data for multiple schemes in parallel."""
    code_map = _load_scheme_code_map()
    new_frames = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_scheme = {
            pool.submit(_fetch_single_nav, scheme, code_map): scheme
            for scheme in scheme_names
        }
        for future in as_completed(future_to_scheme):
            scheme = future_to_scheme[future]
            try:
                df = future.result()
                new_frames.append(df)
            except Exception as e:
                logger.error("Failed to fetch NAV for %s: %s", scheme, e)

    _save_scheme_code_map(code_map)
    return new_frames


# ---- Holdings data ----


def _polars_row_to_holding(row: dict) -> MfHolding:
    fields = {_HOLDINGS_FIELD_MAP[k]: row.get(k) for k in _HOLDINGS_FIELD_MAP}
    fields["scheme_slug"] = fields.get("scheme_slug") or ""
    return MfHolding(**fields)  # type: ignore[arg-type]


def _holding_to_dict(h: MfHolding) -> dict:
    inv = {v: k for k, v in _HOLDINGS_FIELD_MAP.items()}
    return {inv[c]: getattr(h, c) for c in inv}


def _save_holdings(df: pl.DataFrame):
    if df.height == 0:
        return
    with get_session() as session:
        session.add_all([_polars_row_to_holding(row) for row in df.iter_rows(named=True)])
        session.commit()


def _save_sectors(df: pl.DataFrame):
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


def _save_assets(df: pl.DataFrame):
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


def _load_holdings(slugs: list[str] | None = None) -> pl.DataFrame:
    with get_session() as session:
        stmt = select(MfHolding)
        if slugs:
            stmt = stmt.where(col(MfHolding.scheme_slug).in_(slugs))
        rows = session.execute(stmt).scalars().all()
    if not rows:
        return empty_df(HOLDINGS_SCHEMA)
    return pl.DataFrame([_holding_to_dict(r) for r in rows])


def _load_sectors(slugs: list[str] | None = None) -> pl.DataFrame:
    with get_session() as session:
        stmt = select(MfSectorAllocation)
        if slugs:
            stmt = stmt.where(col(MfSectorAllocation.scheme_slug).in_(slugs))
        rows = session.execute(stmt).scalars().all()
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


def _load_assets(slugs: list[str] | None = None) -> pl.DataFrame:
    with get_session() as session:
        stmt = select(MfAssetAllocation)
        if slugs:
            stmt = stmt.where(col(MfAssetAllocation.scheme_slug).in_(slugs))
        rows = session.execute(stmt).scalars().all()
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
    holdings = _load_holdings(slugs)
    sectors = _load_sectors(slugs)
    assets = _load_assets(slugs)

    existing = set(holdings["schemeSlug"].unique().to_list()) if holdings.height else set()
    missing = set(slugs) - existing

    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_slug = {
                pool.submit(fetch_portfolio_by_slug, slug): slug for slug in missing
            }
            for future in as_completed(future_to_slug):
                slug = future_to_slug[future]
                try:
                    resp = future.result()
                    _save_holdings(normalize_holdings(resp, slug))
                    _save_sectors(normalize_sector_allocation(resp, slug))
                    _save_assets(normalize_asset_allocation(resp, slug))
                except Exception as e:
                    logger.error("Failed to fetch holdings for %s: %s", slug, e)

        holdings = _load_holdings(slugs)
        sectors = _load_sectors(slugs)
        assets = _load_assets(slugs)

    return holdings, sectors, assets


# ---- Registry ----


def make_slug(name: str) -> str:
    return "-".join(
        c.strip("-")
        for c in name.replace("(", " ").replace(")", " ").split()
        if c and c != "-"
    ).lower()


def load_registry() -> pl.DataFrame:
    with get_session() as session:
        rows = session.execute(
            select(MfRegistry).order_by(col(MfRegistry.scheme_name))
        ).scalars().all()
    if not rows:
        return pl.DataFrame(
            schema={"schemeName": pl.Utf8, "schemeSlug": pl.Utf8, "source": pl.Utf8}
        )
    return pl.DataFrame(
        {
            "schemeName": [r.scheme_name for r in rows],
            "schemeSlug": [r.scheme_slug for r in rows],
            "source": [r.source for r in rows],
        }
    )


def save_to_registry(names: list[str]):
    if not names:
        return
    with get_session() as session:
        for name in names:
            stmt = (
                pg_insert(MfRegistry)
                .values(scheme_name=name, scheme_slug=make_slug(name), source="advisorkhoj")
                .on_conflict_do_nothing(index_elements=["scheme_name"])
            )
            session.execute(stmt)
        session.commit()
    logger.info("Saved %d schemes to registry", len(names))


def refresh_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """Re-fetch NAV data for given schemes, replacing existing entries."""
    with get_session() as session:
        session.execute(delete(MfNav).where(col(MfNav.scheme_name).in_(scheme_names)))
        session.commit()

    new_frames = _fetch_nav_parallel(scheme_names)
    for df in new_frames:
        _save_nav_df(df)

    return _load_nav_df(scheme_names)


def refresh_holdings_data(
    slugs: list[str],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Re-fetch holdings data for given slugs, replacing existing entries."""
    with get_session() as session:
        session.execute(delete(MfHolding).where(col(MfHolding.scheme_slug).in_(slugs)))
        session.execute(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_slug).in_(slugs)))
        session.execute(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_slug).in_(slugs)))
        session.commit()

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_slug = {
            pool.submit(fetch_portfolio_by_slug, slug): slug for slug in slugs
        }
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                resp = future.result()
                _save_holdings(normalize_holdings(resp, slug))
                _save_sectors(normalize_sector_allocation(resp, slug))
                _save_assets(normalize_asset_allocation(resp, slug))
            except Exception as e:
                logger.error("Failed to refresh holdings for %s: %s", slug, e)

    return _load_holdings(slugs), _load_sectors(slugs), _load_assets(slugs)
