"""Active portfolio schemes derived from the tradebook (net qty > 0)."""

import logging

import polars as pl

from data.repositories.fund_mapping import ensure_fund_mapping
from data.repositories.tradebook import load_tradebook_from_db
from mutual_funds.tradebook import apply_fund_mapping, normalize_transactions

logger = logging.getLogger("services.portfolio_holdings")


def get_active_portfolio_schemes() -> list[str]:
    """Return scheme names with net positive units (held, not fully sold).

    Sources tradebook from DB → applies persisted fund_mapping → groups by
    schemeName → keeps schemes where total signed_qty > 0.
    """
    tb = load_tradebook_from_db()
    if tb.is_empty():
        return []

    mapping = ensure_fund_mapping()
    if mapping is None or mapping.empty:
        return []

    normalized = normalize_transactions(tb)
    mapped = apply_fund_mapping(normalized, mapping)

    holdings = (
        mapped.filter(pl.col("schemeName").is_not_null() & (pl.col("schemeName") != ""))
        .group_by("schemeName")
        .agg(pl.col("signed_qty").sum().alias("units"))
        .filter(pl.col("units") > 0)
        .sort("schemeName")
    )

    return holdings["schemeName"].to_list()
