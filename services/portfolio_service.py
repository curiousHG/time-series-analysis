"""Portfolio service — portfolio value computation."""

import pandas as pd
import polars as pl

from core.timing import timeit
from data.repositories.nav import load_nav_df
from mutual_funds.tradebook import compute_daily_units


@timeit("portfolio.get_mapped_data")
def get_mapped_data(txn_df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Return (mapped_txn, portfolio_nav) or None if unavailable.

    Trusts the `scheme_code` / `schemeName` columns produced at load time by
    `load_tradebook_from_db` (denormalised on import via ISIN→amfi_schemes resolution).
    """
    mapped = txn_df.filter(pl.col("schemeName").is_not_null() & (pl.col("schemeName") != ""))

    if mapped.is_empty():
        return None

    all_schemes = mapped.select("schemeName").unique().to_series().to_list()
    portfolio_nav = load_nav_df(all_schemes)

    return mapped, portfolio_nav


def build_portfolio_value_series(mapped: pl.DataFrame, nav_df: pl.DataFrame) -> pd.DataFrame | None:
    """Build a daily portfolio value DataFrame with columns: date, portfolio_value."""
    daily_units = compute_daily_units(mapped)
    portfolio_nav = nav_df.select(["date", "nav", "schemeName"])
    all_dates = portfolio_nav.select("date").unique().sort("date")

    unit_frames = []
    for scheme in daily_units.select("schemeName").unique().to_series().to_list():
        su = daily_units.filter(pl.col("schemeName") == scheme)
        sf = (
            all_dates.join(su, on="date", how="left")
            .with_columns(pl.lit(scheme).alias("schemeName"))
            .sort("date")
            .with_columns(pl.col("units").forward_fill().fill_null(0))
        )
        unit_frames.append(sf)

    if not unit_frames:
        return None

    all_units = pl.concat(unit_frames)

    # Forward-fill NAV for each scheme so weekends/holidays use the last known NAV
    nav_filled_frames = []
    for scheme in daily_units.select("schemeName").unique().to_series().to_list():
        scheme_nav = portfolio_nav.filter(pl.col("schemeName") == scheme)
        scheme_full = (
            all_dates.join(scheme_nav, on="date", how="left")
            .with_columns(pl.lit(scheme).alias("schemeName"))
            .sort("date")
            .with_columns(pl.col("nav").forward_fill())
        )
        nav_filled_frames.append(scheme_full)

    nav_filled = pl.concat(nav_filled_frames)

    pv = (
        all_units.join(nav_filled, on=["date", "schemeName"], how="inner")
        .filter(pl.col("nav").is_not_null())
        .with_columns((pl.col("units") * pl.col("nav")).alias("value"))
        .group_by("date")
        .agg(pl.sum("value").alias("portfolio_value"))
        .sort("date")
        .filter(pl.col("portfolio_value") > 0)
    )

    if pv.height == 0:
        return None

    return pv.to_pandas()


def build_portfolio_returns_series(
    mapped: pl.DataFrame,
    nav_df: pl.DataFrame,
) -> pd.Series:
    """Daily time-weighted return series for the portfolio (cash-flow-adjusted).

    Required for SIP-heavy portfolios — pct_change() of total value treats every SIP
    contribution as if it were a return, inflating CAGR. We also can't rely on
    build_portfolio_value_series here: that helper drops trade-day rows when the trade
    falls on a non-NAV day, which loses those units until the next NAV-day trade.

    This rebuilds units and flows from scratch, rolling every trade forward to the
    first NAV date >= trade_date, so V_t and f_t stay consistent.
    Daily TWR:  r_t = (V_t - f_t) / V_{t-1} - 1
    """
    if nav_df.is_empty() or mapped.is_empty():
        return pd.Series(dtype="float64")

    nav_pd = nav_df.select(["date", "schemeName", "nav"]).to_pandas()
    nav_pd["date"] = pd.to_datetime(nav_pd["date"])
    nav_pivot = (
        nav_pd.pivot_table(index="date", columns="schemeName", values="nav", aggfunc="last").sort_index().ffill()
    )
    nav_dates = nav_pivot.index

    trades = (
        mapped.with_columns(
            pl.when(pl.col("signed_qty") > 0)
            .then(pl.col("trade_value"))
            .otherwise(-pl.col("trade_value"))
            .alias("flow")
        )
        .group_by(["schemeName", "trade_date"])
        .agg(pl.sum("signed_qty").alias("delta_units"), pl.sum("flow").alias("flow"))
        .sort("trade_date")
        .to_pandas()
    )
    trades["trade_date"] = pd.to_datetime(trades["trade_date"])

    # Each trade_date → first NAV date >= trade_date (rolls Sat/Sun trades to next NAV day).
    pos = nav_dates.searchsorted(trades["trade_date"].values, side="left")
    in_range = pos < len(nav_dates)
    trades = trades.loc[in_range].copy()
    trades["aligned_date"] = nav_dates[pos[in_range]]

    delta_units = trades.groupby(["aligned_date", "schemeName"])["delta_units"].sum().unstack("schemeName").fillna(0.0)
    delta_units = delta_units.reindex(nav_dates).fillna(0.0)
    # Restrict NAV columns to the schemes the user actually trades.
    schemes = [c for c in delta_units.columns if c in nav_pivot.columns]
    if not schemes:
        return pd.Series(dtype="float64")
    units = delta_units[schemes].cumsum()
    nav_aligned = nav_pivot[schemes].reindex(nav_dates).ffill()
    pv = (units * nav_aligned).sum(axis=1)

    flows = trades.groupby("aligned_date")["flow"].sum().reindex(nav_dates).fillna(0.0)

    # Returns only meaningful once V_{t-1} > 0.
    valid = pv > 0
    pv_v = pv.where(valid)
    v_prev = pv_v.shift(1)
    r = (pv_v - flows) / v_prev - 1.0
    return r.dropna().rename("portfolio_return")


def get_signed_invested(mapped: pl.DataFrame) -> pd.DataFrame:
    """Build signed invested trades DataFrame (buys positive, sells negative)."""
    return (
        mapped.with_columns(
            pl.when(pl.col("signed_qty") > 0)
            .then(pl.col("trade_value"))
            .otherwise(-pl.col("trade_value"))
            .alias("signed_invested")
        )
        .select(["trade_date", "signed_invested"])
        .to_pandas()
        .rename(columns={"trade_date": "date"})
    )
