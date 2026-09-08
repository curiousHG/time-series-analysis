from datetime import datetime

import polars as pl

from mutual_funds.constants import ISIN_RE
from mutual_funds.table_schema import (
    ASSET_SCHEMA,
    HOLDINGS_SCHEMA,
    SECTOR_SCHEMA,
    empty_df,
)


def _clean_isin(value: str | None) -> str:
    """Valid ISIN, else `""`. AdvisorKhoj abuses the field for arbitrage funds (`Short`/`Long`,
    handled via assetSubClass), so drop fakes rather than leak them into `mf_holdings.isin`."""
    if not value:
        return ""
    v = str(value).strip().upper()
    return v if ISIN_RE.match(v) else ""


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
                "portfolioDate": datetime.strptime(h["portfolio_date"], "%d-%m-%Y").date(),
                "instrumentName": h["instrument"],
                "isin": _clean_isin(h.get("isin")),
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
        .with_columns([pl.col(c).cast(t, strict=False) for c, t in HOLDINGS_SCHEMA.items()])
        .select(HOLDINGS_SCHEMA.keys())
    )


def _normalize_weight_map(resp: dict, slug: str, *, map_key: str, column: str, schema: dict) -> pl.DataFrame:
    """Sector and asset allocations share one shape: a {label: weight} map keyed off the first
    portfolio row's scheme identity and date."""
    base = resp["schemePortfolioAnalysisResponse"]
    items = base["schemePortfolioList"] if base else None
    weights = base.get(map_key, {}) if base else {}
    if not items or not weights:
        return empty_df(schema)
    first = items[0]
    rows = [
        {
            "schemeCode": first["scheme_code"],
            "schemeName": first["scheme_name"],
            "schemeSlug": slug,
            "portfolioDate": datetime.strptime(first["portfolio_date"], "%d-%m-%Y").date(),
            column: label,
            "weight": float(weight),
        }
        for label, weight in weights.items()
    ]
    return (
        pl.DataFrame(rows)
        .with_columns([pl.col(c).cast(t, strict=False) for c, t in schema.items()])
        .select(schema.keys())
    )


def normalize_sector_allocation(resp: dict, slug: str) -> pl.DataFrame:
    return _normalize_weight_map(resp, slug, map_key="sectorAllocationMap", column="sector", schema=SECTOR_SCHEMA)


def normalize_asset_allocation(resp: dict, slug: str) -> pl.DataFrame:
    return _normalize_weight_map(resp, slug, map_key="assetAllocationMap", column="assetClass", schema=ASSET_SCHEMA)
