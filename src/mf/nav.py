import httpx
import polars as pl

BASE_URL = "https://api.mfapi.in/mf"


def fetch_nav(scheme_code: str) -> pl.DataFrame:
    url = f"{BASE_URL}/{scheme_code}"

    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()

    raw = resp.json()

    df = pl.DataFrame(raw["data"])

    return (
        df.with_columns(
            pl.col("date").str.strptime(pl.Date, "%d-%m-%Y"),
            pl.col("nav").cast(pl.Float64),
            pl.lit(scheme_code).alias("schemeCode"),
        )
        .sort("date")
    )
