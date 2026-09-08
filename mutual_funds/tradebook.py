"""Tradebook transformations. All functions consume `load_tradebook_from_db` output:
`symbol, isin, trade_date, trade_type, quantity, price, scheme_code, schemeName`."""

import polars as pl


def normalize_transactions(df: pl.DataFrame) -> pl.DataFrame:
    """Add `signed_qty` (negated for sells) and `trade_value`; project canonical columns."""
    return df.with_columns(
        pl.when(pl.col("trade_type").str.to_lowercase() == "buy")
        .then(pl.col("quantity"))
        .otherwise(-pl.col("quantity"))
        .alias("signed_qty"),
        (pl.col("quantity") * pl.col("price")).alias("trade_value"),
        pl.col("trade_date").cast(pl.Date),
    ).select(
        "symbol",
        "isin",
        "trade_date",
        "scheme_code",
        "schemeName",
        "signed_qty",
        "price",
        "trade_value",
    )


def signed_flow(alias: str = "amount") -> pl.Expr:
    """Cash flow of a normalised trade: trade_value for buys, negated for sells."""
    return pl.when(pl.col("signed_qty") > 0).then(pl.col("trade_value")).otherwise(-pl.col("trade_value")).alias(alias)


def signed_flows(txn_df: pl.DataFrame, scheme_name: str | None = None) -> pl.DataFrame:
    """(date, amount) cash flows, optionally for one scheme — the input every XIRR needs."""
    df = txn_df.filter(pl.col("schemeName") == scheme_name) if scheme_name is not None else txn_df
    return df.select(pl.col("trade_date").alias("date"), signed_flow("amount"))


def daily_net_invested(txn_df: pl.DataFrame) -> pl.DataFrame:
    """(date, invested, cum_invested): net cash in per trade date and its running total."""
    return (
        txn_df.group_by("trade_date")
        .agg(signed_flow("invested").sum())
        .sort("trade_date")
        .with_columns(pl.col("invested").cum_sum().alias("cum_invested"))
        .rename({"trade_date": "date"})
    )


def compute_daily_units(txn_df: pl.DataFrame) -> pl.DataFrame:
    """Daily cumulative units held per scheme, derived from signed-qty trades."""
    return (
        txn_df.group_by(["schemeName", "trade_date"])
        .agg(pl.col("signed_qty").sum())
        .sort("trade_date")
        .with_columns(pl.col("signed_qty").cum_sum().over("schemeName").alias("units"))
        .select(
            "schemeName",
            pl.col("trade_date").alias("date"),
            "units",
        )
    )
