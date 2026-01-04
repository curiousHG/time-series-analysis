import polars as pl
from pathlib import Path
from datetime import datetime

HOLDINGS_PATH = Path("data/parquet/mf_holdings.parquet")


def normalize_holdings(resp: dict, slug: str) -> pl.DataFrame:
    items = resp["schemePortfolioAnalysisResponse"]["schemePortfolioList"]

    rows = []
    for h in items:
        if h["asset_class"] != "Equity":
            continue

        rows.append(
            {
                "schemeCode": h["scheme_code"],
                "schemeName": h["scheme_name"],
                "schemeSlug": slug,
                "schemeCommon": h["scheme_amfi_common"],
                "portfolioDate": datetime.strptime(
                    h["portfolio_date"], "%d-%m-%Y"
                ).date(),
                "stockName": h["instrument"],
                "industry": h["industry"],
                "marketCap": h.get("stocks"),
                "weight": float(h["holdings"]),
            }
        )

    return pl.DataFrame(rows)


def normalize_sector_allocation(resp: dict, slug: str) -> pl.DataFrame:
    base = resp["schemePortfolioAnalysisResponse"]
    sector_map = base["sectorAllocationMap"]

    rows = [
        {
            "schemeCode": base["schemePortfolioList"][0]["scheme_code"],
            "schemeName": base["schemePortfolioList"][0]["scheme_name"],
            "schemeSlug": slug,
            "portfolioDate": datetime.strptime(
                base["schemePortfolioList"][0]["portfolio_date"], "%d-%m-%Y"
            ).date(),
            "sector": k,
            "weight": v,
        }
        for k, v in sector_map.items()
    ]

    return pl.DataFrame(rows)


def normalize_asset_allocation(resp: dict, slug: str) -> pl.DataFrame:
    base = resp["schemePortfolioAnalysisResponse"]
    asset_map = base["assetAllocationMap"]

    rows = [
        {
            "schemeCode": base["schemePortfolioList"][0]["scheme_code"],
            "schemeName": base["schemePortfolioList"][0]["scheme_name"],
            "schemeSlug": slug,
            "portfolioDate": datetime.strptime(
                base["schemePortfolioList"][0]["portfolio_date"], "%d-%m-%Y"
            ).date(),
            "assetClass": k,
            "weight": v,
        }
        for k, v in asset_map.items()
    ]

    return pl.DataFrame(rows)
