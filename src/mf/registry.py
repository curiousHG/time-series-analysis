import httpx
import polars as pl

MF_REGISTRY_URL = "https://api.mfapi.in/mf"


def fetch_scheme_registry() -> pl.DataFrame:
    """
    Fetch all mutual fund scheme codes and names from AMFI
    """
    resp = httpx.get(MF_REGISTRY_URL, timeout=30)
    resp.raise_for_status()

    data = resp.json()

    df = pl.DataFrame(data).select(
        pl.col("schemeCode").cast(pl.Utf8),
        pl.col("schemeName").cast(pl.Utf8),
    )

    return df

