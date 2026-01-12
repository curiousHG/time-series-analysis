from data.fetchers.stock import fetch_symbol_data, get_symbol_info, query_stocks
import pandas as pd
import polars as pl
from pathlib import Path
import pathlib
from datetime import datetime, date


def _to_date(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    raise TypeError(f"Unsupported date type: {type(d)}")


def ensure_stock_data(
    symbol: str, start_date: datetime, end_date: datetime
) -> pl.DataFrame:
    """
    Check if the symbol data is already present or not, if it is present check the date range,
    if the required data range is present return the data else fetch from yfinance and persist it.

    if the range is bigger than existing data, fetch the missing data and append it to existing data.

    """
    path = Path(f"data/parquet/stocks/{symbol}.parquet")
    start_date = _to_date(start_date)
    end_date = _to_date(end_date)
    # return fetch_symbol_data(symbol, start=start, end=end)
    if path.exists():
        df = pl.read_parquet(path)
        date_series = df.select("Date").to_series().cast(pl.Date)
        min_date = date_series.min()
        max_date = date_series.max()
        need_fetch = False
        fetch_start = None
        fetch_end = None
        if min_date > start_date:
            need_fetch = True
            fetch_start = start_date
            fetch_end = (min_date - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if max_date < end_date:
            need_fetch = True
            fetch_start = (max_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            fetch_end = end_date
        if need_fetch and fetch_start != fetch_end:
            print("fetch_start!=fetch_end", fetch_start != fetch_end)
            new_data = fetch_symbol_data(symbol, start=fetch_start, end=fetch_end)
            if not new_data.empty:
                new_df = pl.from_pandas(new_data.reset_index())
                print(new_df.columns, df.columns)
                df = pl.concat([df, new_df])
                df.write_parquet(path)
        return df.filter((pl.col("Date") >= start_date) & (pl.col("Date") <= end_date))
    else:
        data = fetch_symbol_data(symbol, start=start_date, end=end_date)
        df = pl.from_pandas(data.reset_index())
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)
        return df


STOCK_REGISTRY_PATH = Path("data/parquet/stock_registry.parquet")


def load_stock_registry():

    if STOCK_REGISTRY_PATH.exists():
        return pl.read_parquet(STOCK_REGISTRY_PATH)
    pathlib.Path(STOCK_REGISTRY_PATH).parent.mkdir(parents=True, exist_ok=True)
    return pl.DataFrame(
        schema={
            "stockName": pl.Utf8,
            "symbol": pl.Utf8,
            "exchange": pl.Utf8,
            "quoteType": pl.Utf8,
        }
    )


def save_to_stock_registry():
    pass
