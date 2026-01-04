import polars as pl
from src.mutualFunds.holdings import normalize_holdings, normalize_sector_allocation, normalize_asset_allocation
from src.mutualFunds.constants import ASSET_PATH, HOLDINGS_PATH, NAV_PATH, SECTOR_PATH
from src.mutualFunds.fetch_data import fetch_nav_from_advisorkhoj, fetch_portfolio_by_slug

from datetime import datetime

def nav_json_to_df(nav_json: list[list], scheme_name: str) -> pl.DataFrame:
    cleaned = [
        {
            "ts_ms": int(row[0]),   # epoch milliseconds
            "nav": float(row[1]),
        }
        for row in nav_json
        if row and row[1] is not None
    ]

    return (
        pl.DataFrame(cleaned)
        .with_columns(
            [
                pl.from_epoch(
                    pl.col("ts_ms"),
                    time_unit="ms"
                )
                .dt.date()
                .alias("date"),

                pl.col("nav").alias("nav"),

                pl.lit(scheme_name).alias("schemeName"),
            ]
        )
        .select("date", "nav", "schemeName")
        .sort("date")
        .unique(subset=["date", "schemeName"], keep="last")
    )

def ensure_nav_data(scheme_names: list[str]) -> pl.DataFrame:   

    """
    Ensures NAV data exists locally for given scheme NAMES
    using AdvisorKhoj NAV endpoint.
    """
    
    if NAV_PATH.exists():
        nav_df = pl.read_parquet(NAV_PATH)
        existing = (
            nav_df.select("schemeName")
                  .unique()
                  .to_series()
                  .to_list()
        )
    else:
        nav_df = pl.DataFrame(
            schema={
                "date": pl.Date,
                "nav": pl.Float64,
                "schemeName": pl.Utf8,
            }
        )
        existing = []

    missing = list(set(scheme_names) - set(existing))

    for scheme in missing:
        # print("Fetching NAV:", scheme)

        data = fetch_nav_from_advisorkhoj(scheme)
        df = nav_json_to_df(data["nav_data"], scheme)

        nav_df = pl.concat([nav_df, df])

    if missing:
        nav_df.write_parquet(NAV_PATH)

    return nav_df



def ensure_holdings_data(slugs: list[str]) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    holdings = pl.read_parquet(HOLDINGS_PATH) if HOLDINGS_PATH.exists() else pl.DataFrame()
    sectors = pl.read_parquet(SECTOR_PATH) if SECTOR_PATH.exists() else pl.DataFrame()
    assets = pl.read_parquet(ASSET_PATH) if ASSET_PATH.exists() else pl.DataFrame()

    existing = set(holdings["schemeSlug"].unique().to_list()) if holdings.height else set()
    missing = set(slugs) - existing

    for slug in missing:
        resp = fetch_portfolio_by_slug(slug)
        # print(resp)

        h = normalize_holdings(resp, slug)
        s = normalize_sector_allocation(resp, slug)
        a = normalize_asset_allocation(resp, slug)

        holdings = pl.concat([holdings, h]) if holdings.height and  h.height else h
        sectors = pl.concat([sectors, s]) if sectors.height else s
        assets = pl.concat([assets, a]) if assets.height else a

    if missing:
        holdings.write_parquet(HOLDINGS_PATH)
        sectors.write_parquet(SECTOR_PATH)
        assets.write_parquet(ASSET_PATH)

    return holdings, sectors, assets