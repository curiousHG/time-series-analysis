"""Active portfolio schemes derived from the tradebook (net qty > 0)."""

import logging

import polars as pl

from data.repositories.tradebook import load_tradebook_from_db
from mutual_funds.tradebook import normalize_transactions
from services.scheme_lookup import resolve_tradebook

logger = logging.getLogger("services.portfolio_holdings")


def get_active_portfolio_schemes() -> list[str]:
    """Return scheme names with net positive units (held, not fully sold).

    Tradebook ISINs are resolved live via amfi_schemes; no fund_mapping table needed.
    """
    tb = load_tradebook_from_db()
    if tb.is_empty():
        return []

    normalized = normalize_transactions(tb)
    mapped = resolve_tradebook(normalized).rename({"scheme_name": "schemeName"})

    holdings = (
        mapped.filter(pl.col("schemeName").is_not_null() & (pl.col("schemeName") != ""))
        .group_by("schemeName")
        .agg(pl.col("signed_qty").sum().alias("units"))
        .filter(pl.col("units") > 0)
        .sort("schemeName")
    )

    return holdings["schemeName"].to_list()
