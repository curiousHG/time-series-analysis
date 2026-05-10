"""Tradebook transformations.

Input contract: every function here consumes the output of
`data.repositories.tradebook.load_tradebook_from_db`, which returns columns:
`symbol, isin, trade_date, trade_type, quantity, price, scheme_code, schemeName`.
"""

import polars as pl


def normalize_transactions(df: pl.DataFrame) -> pl.DataFrame:
    """Add `signed_qty` (negated for sells) and `trade_value`, cast trade_date,
    project the canonical columns used by downstream portfolio code."""
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
