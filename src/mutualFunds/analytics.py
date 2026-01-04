import polars as pl
import pandas as pd

# fund overlap
def compute_overlap(holdings_df: pl.DataFrame, fund_a_slug: str, fund_b_slug: str) -> float:
    df_a = (
        holdings_df
        .filter(pl.col("schemeSlug") == fund_a_slug)
        .select("stockName", pl.col("weight").alias("w_a"))
    )

    df_b = (
        holdings_df
        .filter(pl.col("schemeSlug") == fund_b_slug)
        .select("stockName", pl.col("weight").alias("w_b"))
    )

    return (
        df_a
        .join(df_b, on="stockName", how="inner")
        .select(pl.min_horizontal("w_a", "w_b").alias("overlap"))
        .select(pl.sum("overlap"))
        .item()
    )

def overlap_matrix(holdings_df: pl.DataFrame, fund_slugs: list[str]):
    data = []

    for a in fund_slugs:
        row = []
        for b in fund_slugs:
            val = compute_overlap(holdings_df, a, b)
            row.append(val)
        data.append(row)

    return pd.DataFrame(data, index=fund_slugs, columns=fund_slugs).round(2)



# sector overlap
def sector_exposure(sector_df: pl.DataFrame, fund_slugs: list[str]) -> pl.DataFrame:
    return (
        sector_df
        .filter(pl.col("schemeSlug").is_in(fund_slugs))
        .group_by(["schemeSlug", "sector"])
        .agg(pl.sum("weight").alias("weight"))
    )

def missing_sectors(sector_df: pl.DataFrame, base_fund: str, compare_fund: str):
    base = (
        sector_df
        .filter(pl.col("schemeSlug") == base_fund)
        .select("sector")
    )

    compare = (
        sector_df
        .filter(pl.col("schemeSlug") == compare_fund)
        .select("sector", "weight")
    )

    return (
        compare
        .join(base, on="sector", how="anti")
        .sort("weight", descending=True)
    )



## rolling returns related
def rolling_return_summary(rr_df: pd.DataFrame):
    return pd.DataFrame({
        "Mean Return %": rr_df.mean() * 100,
        "Min Return %": rr_df.min() * 100,
        "Max Return %": rr_df.max() * 100,
        "Latest %": rr_df.iloc[-1] * 100,
        "Periods": rr_df.count()
    }).round(2)

def rolling_returns(nav_pd: pd.DataFrame, window: int):
    pivot = nav_pd.pivot(
        index="date",
        columns="schemeName",
        values="nav"
    )

    return pivot.pct_change(window, fill_method=None)