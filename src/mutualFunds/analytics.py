import polars as pl
import pandas as pd

def rolling_return_summary(rr_df: pd.DataFrame):
    return pd.DataFrame({
        "Mean Return %": rr_df.mean() * 100,
        "Min Return %": rr_df.min() * 100,
        "Max Return %": rr_df.max() * 100,
        "Latest %": rr_df.iloc[-1] * 100,
    }).round(2)


def compute_overlap(holdings_df:pd.DataFrame, fund_a, fund_b):
    df_a = (
        holdings_df
        .filter(pl.col("scheme_name") == fund_a)
        .select("isin", pl.col("weight").alias("w_a"))
    )

    df_b = (
        holdings_df
        .filter(pl.col("scheme_name") == fund_b)
        .select("isin", pl.col("weight").alias("w_b"))
    )

    merged = df_a.join(df_b, on="isin", how="inner")

    return (
        merged
        .with_columns(pl.min_horizontal(["w_a", "w_b"]).alias("overlap"))
        .select(pl.sum("overlap"))
        .item()
    )

def overlap_matrix(holdings_df, funds):
    matrix = pd.DataFrame(
        index=funds,
        columns=funds,
        dtype=float
    )

    for a in funds:
        for b in funds:
            matrix.loc[a, b] = compute_overlap(
                holdings_df, a, b
            )

    return matrix.round(2)



def rolling_returns(nav_pd: pd.DataFrame, window: int):
    pivot = nav_pd.pivot(
        index="date",
        columns="schemeName",
        values="nav"
    )

    return pivot.pct_change(window, fill_method=None)