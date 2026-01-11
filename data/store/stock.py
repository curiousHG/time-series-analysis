from data.fetchers.stock import fetch_symbol_data, get_symbol_info, query_stocks
import pandas as pd
import polars as pl
from pathlib import Path
from datetime import datetime


def ensure_stock_data(symbol:str, start:str, end:str) -> pl.DataFrame:
    """
    Check if the symbol data is already present or not, if it is present check the date range,
    if the required data range is present return the data else fetch from yfinance and persist it.

    if the range is bigger than existing data, fetch the missing data and append it to existing data.

    """
    path = Path(f"data/parquet/stocks/{symbol}.parquet")
    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d")
    # return fetch_symbol_data(symbol, start=start, end=end)
    if path.exists():
        df = pl.read_parquet(path)
        print("Existing data found for", symbol, df.shape)
        date_series = df.select("Date").to_series()
        min_date = date_series.min()
        max_date = date_series.max()
        need_fetch = False
        fetch_start = None
        fetch_end = None
        if min_date > start_date:
            need_fetch = True
            fetch_start = start
            fetch_end = (min_date - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if max_date < end_date:
            need_fetch = True
            fetch_start = (max_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            fetch_end = end
        if need_fetch:
            new_data = fetch_symbol_data(symbol, start=fetch_start, end=fetch_end)
            if new_data:
                new_df = pl.from_pandas(new_data.reset_index())
                print(new_df.columns, df.columns)
                df = pl.concat([df, new_df])
                df.write_parquet(path)
        return df.filter((pl.col("Date") >= start_date) & (pl.col("Date") <= end_date))
    else:
        data = fetch_symbol_data(symbol, start=start, end=end)
        df = pl.from_pandas(data.reset_index())
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)
        return df