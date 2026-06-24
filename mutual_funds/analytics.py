import pandas as pd
import polars as pl


def overlap_matrix(holdings_df: pl.DataFrame, fund_slugs: list[str]) -> pd.DataFrame:
    """NxN heatmap of pairwise holdings overlap (% of weight in common).

    Expects the latest-snapshot deduped view from `load_holdings`. Cells capped at 100 —
    arbitrage funds report gross long+short >100, which would blow up the colour scale.
    """
    n = len(fund_slugs)

    fund_holdings = {
        slug: holdings_df.filter(pl.col("schemeSlug") == slug).select("instrumentName", "weight") for slug in fund_slugs
    }

    data = [[0.0] * n for _ in range(n)]
    for i in range(n):
        data[i][i] = min(float(fund_holdings[fund_slugs[i]]["weight"].sum()), 100.0)
        for j in range(i + 1, n):
            df_a = fund_holdings[fund_slugs[i]].rename({"weight": "w_a"})
            df_b = fund_holdings[fund_slugs[j]].rename({"weight": "w_b"})
            val = (
                df_a.join(df_b, on="instrumentName", how="inner")
                .select(pl.min_horizontal("w_a", "w_b").alias("overlap"))
                .select(pl.sum("overlap"))
                .item()
            )
            capped = min(val, 100.0)
            data[i][j] = capped
            data[j][i] = capped

    return pd.DataFrame(data, index=fund_slugs, columns=fund_slugs).round(2)


# sector overlap
def sector_exposure(sector_df: pl.DataFrame, fund_slugs: list[str]) -> pl.DataFrame:
    return (
        sector_df.filter(pl.col("schemeSlug").is_in(fund_slugs))
        .group_by(["schemeSlug", "sector"])
        .agg(pl.sum("weight").alias("weight"))
    )


def sector_exposure_average(sector_df: pl.DataFrame, fund_slugs: list[str]) -> pl.DataFrame:
    """Average sector weight across funds. Each fund sums to ~100, so this is an
    equal-weighted, normalized portfolio-level read of sector exposure."""
    n = max(len(fund_slugs), 1)
    return (
        sector_df.filter(pl.col("schemeSlug").is_in(fund_slugs))
        .group_by("sector")
        .agg(
            (pl.sum("weight") / n).alias("avg_weight"),
            pl.col("schemeSlug").n_unique().alias("fund_count"),
        )
        .sort("avg_weight", descending=True)
    )


def missing_sectors(sector_df: pl.DataFrame, base_fund: str, compare_fund: str):
    base = sector_df.filter(pl.col("schemeSlug") == base_fund).select("sector")

    compare = sector_df.filter(pl.col("schemeSlug") == compare_fund).select("sector", "weight")

    return compare.join(base, on="sector", how="anti").sort("weight", descending=True)


## rolling returns related
def rolling_return_summary(rr_df: pd.DataFrame):
    return pd.DataFrame(
        {
            "Mean Return %": rr_df.mean() * 100,
            "Min Return %": rr_df.min() * 100,
            "Max Return %": rr_df.max() * 100,
            "Latest %": rr_df.iloc[-1] * 100,
            "Periods": rr_df.count(),
        }
    ).round(2)


def rolling_returns(nav_pd: pd.DataFrame, window: int):
    pivot = nav_pd.pivot(index="date", columns="schemeName", values="nav")

    return pivot.pct_change(window, fill_method=None)
