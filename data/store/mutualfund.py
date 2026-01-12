import polars as pl
import pandas as pd
import json
import pathlib
from data.fetchers.mutual_fund import fetch_portfolio_by_slug
from mutual_funds.holdings import (
    normalize_holdings,
    normalize_sector_allocation,
    normalize_asset_allocation,
)
from mutual_funds.constants import (
    ASSET_PATH,
    HOLDINGS_PATH,
    NAV_PATH,
    RAW_DIR,
    REGISTRY_PATH,
    SECTOR_PATH,
    FUND_MAPPING_PATH,
)
from data.fetchers.mutual_fund import (
    fetch_nav_from_advisorkhoj,
)

from mutual_funds.tableSchema import (
    ASSET_SCHEMA,
    HOLDINGS_SCHEMA,
    SECTOR_SCHEMA,
    empty_df,
)


def persist_fund_mapping(fund_mapping: pd.DataFrame):
    if fund_mapping is None or fund_mapping.empty:
        return
    fund_mapping.to_csv(FUND_MAPPING_PATH, index=False)


def ensure_fund_mapping():
    if FUND_MAPPING_PATH.exists():
        return pd.read_csv(FUND_MAPPING_PATH)
    return None


def nav_json_to_df(nav_json: list[list], scheme_name: str) -> pl.DataFrame:
    cleaned = [
        {
            "ts_ms": int(row[0]),  # epoch milliseconds
            "nav": float(row[1]),
        }
        for row in nav_json
        if row and row[1] is not None
    ]

    return (
        pl.DataFrame(cleaned)
        .with_columns(
            [
                pl.from_epoch(pl.col("ts_ms"), time_unit="ms").dt.date().alias("date"),
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
        existing = nav_df.select("schemeName").unique().to_series().to_list()
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


def ensure_holdings_data(
    slugs: list[str],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:

    holdings = (
        pl.read_parquet(HOLDINGS_PATH)
        if HOLDINGS_PATH.exists()
        else empty_df(HOLDINGS_SCHEMA)
    )

    sectors = (
        pl.read_parquet(SECTOR_PATH)
        if SECTOR_PATH.exists()
        else empty_df(SECTOR_SCHEMA)
    )

    assets = (
        pl.read_parquet(ASSET_PATH) if ASSET_PATH.exists() else empty_df(ASSET_SCHEMA)
    )

    existing = (
        set(holdings["schemeSlug"].unique().to_list()) if holdings.height else set()
    )

    missing = set(slugs) - existing

    for slug in missing:
        raw_path = RAW_DIR / f"{slug}.json"
        if raw_path.exists():
            with open(raw_path, "r", encoding="utf-8") as f:
                resp = json.load(f)
        else:
            pathlib.Path(RAW_DIR).mkdir(parents=True, exist_ok=True)
            resp = fetch_portfolio_by_slug(slug)
            tmp = raw_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(resp, f, indent=2, ensure_ascii=False)
            tmp.replace(raw_path)

        h = normalize_holdings(resp, slug)
        s = normalize_sector_allocation(resp, slug)
        a = normalize_asset_allocation(resp, slug)

        if h.height:
            holdings = holdings.vstack(h)
        if s.height:
            sectors = sectors.vstack(s)
        if a.height:
            assets = assets.vstack(a)

    if missing:
        holdings.write_parquet(HOLDINGS_PATH)
        sectors.write_parquet(SECTOR_PATH)
        assets.write_parquet(ASSET_PATH)

    return holdings, sectors, assets


def make_slug(name: str) -> str:
    return "-".join(
        c.strip("-")
        for c in name.replace("(", " ").replace(")", " ").split()
        if c and c != "-"
    ).lower()


def load_registry() -> pl.DataFrame:
    if REGISTRY_PATH.exists():
        return pl.read_parquet(REGISTRY_PATH)
    pathlib.Path(REGISTRY_PATH).parent.mkdir(parents=True, exist_ok=True)
    return pl.DataFrame(
        schema={
            "schemeName": pl.Utf8,
            "schemeSlug": pl.Utf8,
            "source": pl.Utf8,
        }
    )


def save_to_registry(names: list[str]):
    if not names:
        return

    df = load_registry()

    new = pl.DataFrame(
        {
            "schemeName": names,
            "schemeSlug": [make_slug(n) for n in names],
            "source": ["advisorkhoj"] * len(names),
        }
    )
    # print(new, df)

    df = pl.concat([df, new]).unique(subset=["schemeName"]).sort("schemeName")

    df.write_parquet(REGISTRY_PATH)
