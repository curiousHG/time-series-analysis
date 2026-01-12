import polars as pl


def empty_df(schema: dict) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


HOLDINGS_SCHEMA = {
    # scheme
    "schemeCode": pl.Utf8,
    "schemeName": pl.Utf8,
    "schemeSlug": pl.Utf8,
    "schemeCommon": pl.Utf8,
    "portfolioDate": pl.Date,
    # instrument
    "instrumentName": pl.Utf8,
    "isin": pl.Utf8,
    "issuerName": pl.Utf8,
    # classification
    "assetClass": pl.Utf8,
    "assetSubClass": pl.Utf8,
    "assetType": pl.Utf8,
    # values
    "weight": pl.Float64,
    "value": pl.Float64,
    "quantity": pl.Float64,
    # optional
    "industry": pl.Utf8,
    "marketCapBucket": pl.Utf8,
    "creditRating": pl.Utf8,
    "creditRatingEq": pl.Utf8,
}

SECTOR_SCHEMA = {
    "schemeCode": pl.Utf8,
    "schemeName": pl.Utf8,
    "schemeSlug": pl.Utf8,
    "portfolioDate": pl.Date,
    "sector": pl.Utf8,
    "weight": pl.Float64,
}

ASSET_SCHEMA = {
    "schemeCode": pl.Utf8,
    "schemeName": pl.Utf8,
    "schemeSlug": pl.Utf8,
    "portfolioDate": pl.Date,
    "assetClass": pl.Utf8,
    "weight": pl.Float64,
}
