import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger("data.fetchers.stock")


def query_stocks(query: str) -> pd.DataFrame:
    """Return list of stock symbols matching the query."""
    query = query.lower()
    df = yf.Lookup(query).all
    filtered = df[(df["exchange"] == "NSI") & (df["quoteType"] == "equity")]
    return filtered


def fetch_symbol_data(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame | None:
    """Fetch historical data for a given stock symbol using yfinance."""
    try:
        data = yf.download(symbol, start=start, end=end, interval=interval, multi_level_index=False)
        return data
    except Exception as e:
        logger.error(f"Error fetching data for symbol {symbol}: {e}")
        return None


def fetch_symbol_data_jugaad(symbol: str, start, end) -> pd.DataFrame | None:
    """
    Fetch historical data from NSE via jugaad-data.
    Symbol should be WITHOUT .NS suffix (e.g., 'RELIANCE' not 'RELIANCE.NS').
    """
    try:
        from jugaad_data.nse import stock_df

        nse_symbol = symbol.replace(".NS", "").replace(".BO", "")
        df = stock_df(symbol=nse_symbol, from_date=start, to_date=end, series="EQ")
        if df.empty:
            return None

        # Rename columns to match yfinance format
        df = df.rename(
            columns={
                "DATE": "Date",
                "OPEN": "Open",
                "HIGH": "High",
                "LOW": "Low",
                "CLOSE": "Close",
                "VOLUME": "Volume",
            }
        )
        return df[["Date", "Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.debug(f"jugaad-data failed for {symbol}: {e}")
        return None


def get_symbol_info(symbol: str):
    """Fetch symbol info from yfinance."""
    ticker = yf.Ticker(symbol)
    return ticker.info
