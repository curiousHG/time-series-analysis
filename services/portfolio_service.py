"""Portfolio service — portfolio value computation and fund mapping."""

import pandas as pd
import polars as pl

from data.repositories.fund_mapping import ensure_fund_mapping
from data.repositories.nav import load_nav_df
from mutual_funds.tradebook import apply_fund_mapping, compute_daily_units


def get_mapped_data(txn_df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Return (mapped_txn, portfolio_nav) or None if unavailable."""
    fund_mapping_df = ensure_fund_mapping()
    if fund_mapping_df is None or fund_mapping_df.empty:
        return None

    mapped = apply_fund_mapping(txn_df, fund_mapping_df)
    mapped = mapped.filter(pl.col("schemeName").is_not_null() & (pl.col("schemeName") != ""))

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
