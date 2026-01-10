import polars as pl
from pathlib import Path
from datetime import datetime

from mutual_funds.tableSchema import ASSET_SCHEMA, HOLDINGS_SCHEMA, SECTOR_SCHEMA, empty_df


def normalize_holdings(resp: dict, slug: str) -> pl.DataFrame:
    base = resp["schemePortfolioAnalysisResponse"]
    if base == {}:
        return empty_df(HOLDINGS_SCHEMA)
    items = base["schemePortfolioList"]

    rows = []
    for h in items:
        rows.append(
            {
                "schemeCode": h["scheme_code"],
                "schemeName": h["scheme_name"],
                "schemeSlug": slug,
                "schemeCommon": h["scheme_amfi_common"],
                "portfolioDate": datetime.strptime(
                    h["portfolio_date"], "%d-%m-%Y"
                ).date(),

                "instrumentName": h["instrument"],
                "isin": h.get("isin") or "",
                "issuerName": h.get("issuer_name") or "",

                "assetClass": h["asset_class"],
                "assetSubClass": h.get("asset_subclass") or "",
                "assetType": h.get("asset_type") or "",

                "weight": float(h["holdings"]),
                "value": float(h["value"]) if h.get("value") else 0.0,
                "quantity": float(h["quantity"]) if h.get("quantity") else 0.0,

                "industry": h.get("industry") or "",
                "marketCapBucket": h.get("stocks") or "",
                "creditRating": h.get("rating") or "",
                "creditRatingEq": h.get("rating_eq") or "",
            }
        )

    if not rows:
        return empty_df(HOLDINGS_SCHEMA)

    return (
        pl.DataFrame(rows)
        .with_columns(
            [pl.col(c).cast(t, strict=False) for c, t in HOLDINGS_SCHEMA.items()]
        )
        .select(HOLDINGS_SCHEMA.keys())
    )


def normalize_sector_allocation(resp: dict, slug: str) -> pl.DataFrame:
    base = resp["schemePortfolioAnalysisResponse"]
    if not base:
        return empty_df(SECTOR_SCHEMA)
    items = base["schemePortfolioList"]
    sector_map = base.get("sectorAllocationMap", {})

    if not items or not sector_map:
        return empty_df(SECTOR_SCHEMA)

    rows = [
        {
            "schemeCode": items[0]["scheme_code"],
            "schemeName": items[0]["scheme_name"],
            "schemeSlug": slug,
            "portfolioDate": datetime.strptime(
                items[0]["portfolio_date"], "%d-%m-%Y"
            ).date(),
            "sector": k,
            "weight": float(v),
        }
        for k, v in sector_map.items()
    ]

    return (
        pl.DataFrame(rows)
        .with_columns(
            [pl.col(c).cast(t, strict=False) for c, t in SECTOR_SCHEMA.items()]
        )
        .select(SECTOR_SCHEMA.keys())
    )



def normalize_asset_allocation(resp: dict, slug: str) -> pl.DataFrame:
    base = resp["schemePortfolioAnalysisResponse"]
    if not base:
        return empty_df(ASSET_SCHEMA)
    items = base["schemePortfolioList"]
    asset_map = base.get("assetAllocationMap", {})

    if not items or not asset_map:
        return empty_df(ASSET_SCHEMA)

    rows = [
        {
            "schemeCode": items[0]["scheme_code"],
            "schemeName": items[0]["scheme_name"],
            "schemeSlug": slug,
            "portfolioDate": datetime.strptime(
                items[0]["portfolio_date"], "%d-%m-%Y"
            ).date(),
            "assetClass": k,
            "weight": float(v),
        }
        for k, v in asset_map.items()
    ]

    return (
        pl.DataFrame(rows)
        .with_columns(
            [pl.col(c).cast(t, strict=False) for c, t in ASSET_SCHEMA.items()]
        )
        .select(ASSET_SCHEMA.keys())
    )

