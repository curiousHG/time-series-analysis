import polars as pl

def load_tradebook(csv_path: str) -> pl.DataFrame:
    return pl.read_csv(
        csv_path,
        try_parse_dates=True,
    )

def normalize_transactions(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df
        .with_columns([
            pl.when(pl.col("trade_type").str.to_lowercase() == "buy")
              .then(pl.col("quantity"))
              .otherwise(-pl.col("quantity"))
              .alias("signed_qty"),

            (pl.col("quantity") * pl.col("price")).alias("trade_value"),
        ])
        .with_columns(
            pl.col("trade_date").cast(pl.Date)
        )
        .select([
            "symbol",
            "isin",
            "trade_date",
            "signed_qty",
            "price",
            "trade_value",
        ])
    )

def compute_current_holdings(txn_df: pl.DataFrame) -> pl.DataFrame:
    return (
        txn_df
        .group_by(["isin"])
        .agg([
            pl.col("symbol").first().alias("symbol"),
            pl.col("signed_qty").sum().alias("units"),
            pl.col("trade_value").sum().alias("total_invested"),
        ])
        .filter(pl.col("units") > 0)
    )

