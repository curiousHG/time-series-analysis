import polars as pl
from pathlib import Path
from src.mf.nav import fetch_nav

NAV_PATH = Path("data/parquet/mf_nav.parquet")


def ensure_nav_data(scheme_codes: list[str]) -> pl.DataFrame:
    """
    Ensures NAV data exists locally for given scheme_codes.
    Downloads missing schemes if needed.
    """

    if NAV_PATH.exists():
        nav_df = pl.read_parquet(NAV_PATH)
        existing = nav_df.select("schemeCode").unique().to_series().to_list()
    else:
        nav_df = pl.DataFrame(
            schema={
                "date": pl.Date,
                "nav": pl.Float64,
                "schemeCode": pl.Utf8,
            }
        )
        existing = []

    missing = list(set(scheme_codes) - set(existing))

    for code in missing:
        df = fetch_nav(code)
        print(df.tail())
        nav_df = pl.concat([nav_df, df])

    # Persist once after all fetches
    if missing:
        nav_df.write_parquet(NAV_PATH)

    return nav_df
