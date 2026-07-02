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
    # Flows dated after the valuation date happen when NAV is stale: their units are in
    # `terminal_value` at the last known NAV, so treat them as invested at t_end (no
    # growth attributed) rather than excluding them or compounding negatively.
    years = ((t_end - dates).dt.days / 365.25).clip(lower=0.0)

    # Investor-perspective cashflows: buys are outflows (-), terminal value an inflow (+).
    # `years` measures flow date → valuation date, so flows are compounded forward to the
    # valuation date (future-value form of the XIRR equation).
    amounts = -flows["amount"].astype(float)

    def npv(rate: float) -> float:
        return float((amounts * (1 + rate) ** years).sum()) + terminal_value

    lo, hi = npv(_XIRR_LO), npv(_XIRR_HI)
    if lo * hi > 0:  # no sign change → no root in the bracket
        return None
    return float(brentq(npv, _XIRR_LO, _XIRR_HI))


def fund_values_from_nav(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame) -> dict[str, float]:
    """Current value per held fund: net units x latest NAV. Funds with ≤ 0 units drop out."""
    units = mapped.group_by("schemeName").agg(pl.col("signed_qty").sum().alias("units")).filter(pl.col("units") > 1e-9)
    latest_nav = portfolio_nav.sort("date").group_by("schemeName").agg(pl.col("nav").last().alias("latest_nav"))
    joined = units.join(latest_nav, on="schemeName", how="inner").with_columns(
        (pl.col("units") * pl.col("latest_nav")).alias("value")
    )
    return dict(zip(joined["schemeName"].to_list(), joined["value"].to_list(), strict=False))


def lookthrough_exposure(holdings_df: pl.DataFrame, fund_values: dict[str, float]) -> pl.DataFrame:
    """Portfolio-level look-through: what the user actually owns across all funds.

    exposure_pct (of portfolio) = Σ over funds of (fund weight in portfolio) x (instrument
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

    weights = pl.DataFrame({"schemeName": list(fund_values), "fund_weight": [v / total for v in fund_values.values()]})
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


def contribution_analysis(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame, window_rows: int = 252) -> pd.DataFrame:
    """Per-fund contribution to the portfolio's return over the trailing NAV window.

    Money-based attribution: fund P&L in the window (value change minus net flows into the
    fund) divided by the portfolio's start value. With no intra-window flows the
    contributions sum exactly to the portfolio return; flows make it a close approximation.

    :param window_rows: trailing NAV rows (~trading days) in the window.
    :return: pandas frame — fund, weight_start_pct, pnl, contribution_pct, fund_return_pct —
        sorted by contribution desc. Empty frame when history is shorter than the window.
    """
    empty = pd.DataFrame(columns=["fund", "weight_start_pct", "pnl", "contribution_pct", "fund_return_pct"])
    if mapped.is_empty() or portfolio_nav.is_empty():
        return empty

    nav_pd = portfolio_nav.select(["date", "schemeName", "nav"]).to_pandas()
    nav_pd["date"] = pd.to_datetime(nav_pd["date"])
    nav_pivot = nav_pd.pivot_table(index="date", columns="schemeName", values="nav", aggfunc="last").sort_index()
    nav_pivot = nav_pivot.ffill()
    if len(nav_pivot) < window_rows + 1:
        return empty
    nav_dates = nav_pivot.index

    trades = (
        mapped.group_by(["schemeName", "trade_date"])
        .agg(
            pl.col("signed_qty").sum().alias("delta_units"),
            pl.when(pl.col("signed_qty") > 0)
            .then(pl.col("trade_value"))
            .otherwise(-pl.col("trade_value"))
            .sum()
            .alias("flow"),
        )
        .to_pandas()
    )
    trades["trade_date"] = pd.to_datetime(trades["trade_date"])
    pos = nav_dates.searchsorted(trades["trade_date"].values, side="left")
    trades = trades.loc[pos < len(nav_dates)].copy()
    trades["aligned_date"] = nav_dates[pos[pos < len(nav_dates)]]

    schemes = [c for c in nav_pivot.columns if c in set(trades["schemeName"])]
    if not schemes:
        return empty
    delta_units = trades.groupby(["aligned_date", "schemeName"])["delta_units"].sum().unstack("schemeName").fillna(0.0)
    units = delta_units.reindex(nav_dates).fillna(0.0)[schemes].cumsum()
    values = units * nav_pivot[schemes]

    t0, t1 = nav_dates[-(window_rows + 1)], nav_dates[-1]
    start_vals = values.loc[t0]
    end_vals = values.loc[t1]
    total_start = float(start_vals.sum())
    if total_start <= 0:
        return empty

    in_window = trades[(trades["aligned_date"] > t0) & (trades["aligned_date"] <= t1)]
    window_flows = in_window.groupby("schemeName")["flow"].sum()

    rows = []
    for scheme in schemes:
        v0, v1 = float(start_vals.get(scheme, 0.0)), float(end_vals.get(scheme, 0.0))
        flow = float(window_flows.get(scheme, 0.0))
        pnl = v1 - v0 - flow
        denom = v0 + max(flow, 0.0)
        rows.append(
            {
                "fund": scheme,
                "weight_start_pct": v0 / total_start * 100,
                "pnl": pnl,
                "contribution_pct": pnl / total_start * 100,
                "fund_return_pct": (pnl / denom * 100) if denom > 0 else None,
            }
        )
    return pd.DataFrame(rows).sort_values("contribution_pct", ascending=False).reset_index(drop=True)
