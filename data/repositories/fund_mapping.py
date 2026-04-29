"""Fund mapping repository — maps tradebook symbols to NAV scheme names."""

import logging

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import delete, select

from core.database import get_session
from core.models import FundMapping
from data.fetchers.mutual_fund import fetch_nav_from_mfapi

logger = logging.getLogger("data.repositories.fund_mapping")


def persist_fund_mapping(fund_mapping: pd.DataFrame):
    if fund_mapping is None or fund_mapping.empty:
        return
    with get_session() as session:
        session.exec(delete(FundMapping))
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
    from data.repositories.amfi import lookup_by_isin
    from data.repositories.nav import load_nav_df, save_nav_df
    from data.repositories.registry import save_to_registry
    from data.repositories.tradebook import load_tradebook_from_db

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
                session.exec(stmt)
            session.commit()

        # Also add to registry
        save_to_registry(list(mappings.values()))

        # Fetch NAV for any schemes not yet in DB
        existing_nav = load_nav_df(list(mappings.values()))
        existing_schemes = (
            set(existing_nav.select("schemeName").unique().to_series().to_list()) if existing_nav.height else set()
        )

        for code, name in nav_to_fetch:
            if name not in existing_schemes:
                try:
                    df = fetch_nav_from_mfapi(code, name)
                    save_nav_df(df)
                    logger.info("Fetched NAV for %s (%d rows)", name, df.height)
                except Exception as e:
                    logger.error("Failed to fetch NAV for %s: %s", name, e)

    logger.info("Auto-mapped %d/%d tradebook symbols", len(mappings), pairs.height)
    return mappings


def ensure_fund_mapping() -> pd.DataFrame | None:
    with get_session() as session:
        rows = session.exec(select(FundMapping)).all()
    if not rows:
        return None
    return pd.DataFrame([{"Trade Symbol": r.trade_symbol, "Mapped NAV Fund": r.mapped_nav_fund} for r in rows])
