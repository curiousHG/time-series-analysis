import polars as pl
import pandas as pd


def load_tradebook(csv_path: str):
    try:
        return pl.read_csv(
            csv_path,
            try_parse_dates=True,
        )
    except Exception as e:
        return None


def normalize_transactions(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df.with_columns(
            [
                pl.when(pl.col("trade_type").str.to_lowercase() == "buy")
                .then(pl.col("quantity"))
                .otherwise(-pl.col("quantity"))
                .alias("signed_qty"),
                (pl.col("quantity") * pl.col("price")).alias("trade_value"),
            ]
        )
        .with_columns(pl.col("trade_date").cast(pl.Date))
        .select(
            [
                "symbol",
                "isin",
                "trade_date",
                "signed_qty",
                "price",
                "trade_value",
            ]
        )
    )


def compute_current_holdings(txn_df: pl.DataFrame) -> pl.DataFrame:
    return (
        txn_df.group_by(["isin"])
        .agg(
            [
                pl.col("symbol").first().alias("symbol"),
                pl.col("signed_qty").sum().alias("units"),
                pl.col("trade_value").sum().alias("total_invested"),
            ]
        )
        .filter(pl.col("units") > 0)
    )


def apply_fund_mapping(
    txn_df: pl.DataFrame,
    mapping_df: pd.DataFrame,
) -> pl.DataFrame:
    mapping_df_pl = (
        pl.from_pandas(mapping_df)
        .rename(
            {
                "Trade Symbol": "symbol",
                "Mapped NAV Fund": "schemeName",
            }
        )
        .filter(pl.col("schemeName").is_not_null() & (pl.col("schemeName") != ""))
    )
    txn_mapped = txn_df.join(mapping_df_pl, on="symbol", how="left")

    # st.dataframe(
    #     txn_mapped.select(["symbol", "schemeName", "signed_qty", "trade_date"])
    # )

    return txn_mapped


def compute_daily_units(txn_df: pl.DataFrame) -> pl.DataFrame:
    return (
        txn_df.group_by(["schemeName", "trade_date"])
        .agg(pl.col("signed_qty").sum())
        .sort("trade_date")
        .with_columns(pl.col("signed_qty").cum_sum().over("schemeName").alias("units"))
        .select(
            [
                "schemeName",
                pl.col("trade_date").alias("date"),
                "units",
            ]
        )
    )
