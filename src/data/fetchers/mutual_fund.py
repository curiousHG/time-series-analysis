import httpx
import polars as pl
from datetime import datetime


BASE_URL = "https://api.mfapi.in/mf"


def fetch_nav(scheme_code: str) -> pl.DataFrame:
    """
    Fetch historical NAV for a mutual fund scheme
    """
    url = f"{BASE_URL}/{scheme_code}"

    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()

    raw = resp.json()

    data = raw["data"]

    df = pl.DataFrame(data)

    df = (
        df.with_columns(
            pl.col("date").str.strptime(pl.Date, "%d-%m-%Y"),
            pl.col("nav").cast(pl.Float64),
            pl.lit(scheme_code).alias("scheme_code"),
        )
        .sort("date")
    )

    return df
