"""Portfolio analytics — XIRR, look-through exposure, and per-fund value helpers.

Pure computation over frames the caller loads (no Streamlit, no fetching), so every
function here is unit-testable with synthetic data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import polars as pl
from scipy.optimize import brentq

if TYPE_CHECKING:
    from datetime import date

# brentq bracket for the annual rate: -99.99% … +1000%.
_XIRR_LO, _XIRR_HI = -0.9999, 10.0


def compute_xirr(flows: pd.DataFrame, terminal_value: float, terminal_date: date) -> float | None:
    """Annualised money-weighted return (XIRR) of the portfolio.

    :param flows: columns `date`, `amount` — amount > 0 for money invested (buys),
        < 0 for withdrawals (sells). `get_signed_invested` output fits directly.
    :param terminal_value: current portfolio value (final positive cashflow).
    :param terminal_date: valuation date of `terminal_value`.
    :return: annual rate, or None when undefined (no flows, no sign change, no root).
    """
    if flows.empty or terminal_value < 0:
        return None

    dates = pd.to_datetime(flows["date"]).dt.normalize()
    t_end = pd.Timestamp(terminal_date)
    years = (t_end - dates).dt.days / 365.25
    if (years < 0).any():  # flows after the valuation date make the equation ill-posed
        return None

    # Investor-perspective cashflows: buys are outflows (−), terminal value an inflow (+).
    amounts = -flows["amount"].astype(float)

    def npv(rate: float) -> float:
        return float((amounts / (1 + rate) ** years).sum()) + terminal_value

    lo, hi = npv(_XIRR_LO), npv(_XIRR_HI)
    if lo * hi > 0:  # no sign change → no root in the bracket
        return None
    return float(brentq(npv, _XIRR_LO, _XIRR_HI))


def fund_values_from_nav(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame) -> dict[str, float]:
    """Current value per held fund: net units × latest NAV. Funds with ≤ 0 units drop out."""
    units = (
        mapped.group_by("schemeName")
        .agg(pl.col("signed_qty").sum().alias("units"))
        .filter(pl.col("units") > 1e-9)
    )
    latest_nav = (
        portfolio_nav.sort("date")
        .group_by("schemeName")
        .agg(pl.col("nav").last().alias("latest_nav"))
    )
    joined = units.join(latest_nav, on="schemeName", how="inner").with_columns(
        (pl.col("units") * pl.col("latest_nav")).alias("value")
    )
    return dict(zip(joined["schemeName"].to_list(), joined["value"].to_list(), strict=False))


def lookthrough_exposure(holdings_df: pl.DataFrame, fund_values: dict[str, float]) -> pl.DataFrame:
    """Portfolio-level look-through: what the user actually owns across all funds.

    exposure_pct (of portfolio) = Σ over funds of (fund weight in portfolio) × (instrument
    weight in fund, %). Instruments keyed on ISIN when present, else normalised name.

    :param holdings_df: `load_holdings` frame (schemeName, instrumentName, isin, weight %,
        industry, marketCapBucket, assetClass).
    :param fund_values: current ₹ value per held fund (`fund_values_from_nav`).
    :return: one row per instrument — instrument_name, isin, industry, market_cap,
        asset_class, exposure_pct, n_funds, funds — sorted by exposure_pct desc.
    """
    total = sum(fund_values.values())
    if total <= 0 or holdings_df.is_empty():
        return pl.DataFrame(
            schema={
                "instrument_name": pl.Utf8,
                "isin": pl.Utf8,
                "industry": pl.Utf8,
                "market_cap": pl.Utf8,
                "asset_class": pl.Utf8,
                "exposure_pct": pl.Float64,
                "n_funds": pl.UInt32,
                "funds": pl.List(pl.Utf8),
            }
        )

    weights = pl.DataFrame(
        {"schemeName": list(fund_values), "fund_weight": [v / total for v in fund_values.values()]}
    )
    h = (
        holdings_df.filter(pl.col("schemeName").is_in(list(fund_values)) & pl.col("weight").is_not_null())
        .join(weights, on="schemeName", how="inner")
        .with_columns(
            (pl.col("weight") * pl.col("fund_weight")).alias("contrib_pct"),
            # Group key: ISIN when present, else the trimmed lowercase instrument name.
            pl.when(pl.col("isin").is_not_null() & (pl.col("isin") != ""))
            .then(pl.col("isin"))
            .otherwise(pl.col("instrumentName").str.to_lowercase().str.strip_chars())
            .alias("instr_key"),
        )
    )
    return (
        h.group_by("instr_key")
        .agg(
            pl.col("instrumentName").first().alias("instrument_name"),
            pl.col("isin").first().alias("isin"),
            pl.col("industry").drop_nulls().first().alias("industry"),
            pl.col("marketCapBucket").drop_nulls().first().alias("market_cap"),
            pl.col("assetClass").drop_nulls().first().alias("asset_class"),
            pl.col("contrib_pct").sum().alias("exposure_pct"),
            pl.col("schemeName").n_unique().alias("n_funds"),
            pl.col("schemeName").unique().alias("funds"),
        )
        .drop("instr_key")
        .sort("exposure_pct", descending=True)
    )


def fund_weights(fund_values: dict[str, float]) -> dict[str, float]:
    """Fund → fraction of portfolio value."""
    total = sum(fund_values.values())
    if total <= 0:
        return {}
    return {name: v / total for name, v in fund_values.items()}
