"""Resolve tradebook ISINs to AMFI scheme names — replaces the fund_mapping table."""

import logging

import polars as pl
from sqlmodel import select

from core.database import get_session
from core.models import AmfiScheme

logger = logging.getLogger("services.scheme_lookup")


def _amfi_isin_table() -> pl.DataFrame:
    """Load (isin_growth, scheme_name, scheme_code) for all AMFI schemes with an ISIN."""
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.isin_growth, AmfiScheme.scheme_name, AmfiScheme.scheme_code).where(
                AmfiScheme.isin_growth.is_not(None)
            )
        ).all()
    if not rows:
        return pl.DataFrame(schema={"isin": pl.Utf8, "scheme_name": pl.Utf8, "scheme_code": pl.Int64})
    return pl.DataFrame(
        {
            "isin": [r[0] for r in rows],
            "scheme_name": [r[1] for r in rows],
            "scheme_code": [r[2] for r in rows],
        }
    )


def resolve_tradebook(tb: pl.DataFrame) -> pl.DataFrame:
    """Add `scheme_name` and `scheme_code` columns to a tradebook by ISIN join.

    Rows whose ISIN is not present in `amfi_schemes.isin_growth` get nulls.
    """
    if tb.is_empty() or "isin" not in tb.columns:
        return tb.with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("scheme_name"),
            pl.lit(None, dtype=pl.Int64).alias("scheme_code"),
        )

    amfi = _amfi_isin_table()
    return tb.join(amfi, on="isin", how="left")


def resolve_isins(isins: list[str]) -> dict[str, tuple[str, int]]:
    """Map a list of ISINs to {isin: (scheme_name, scheme_code)} for matched ones."""
    if not isins:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.isin_growth, AmfiScheme.scheme_name, AmfiScheme.scheme_code).where(
                AmfiScheme.isin_growth.in_(isins)
            )
        ).all()
    return {r[0]: (r[1], r[2]) for r in rows}
