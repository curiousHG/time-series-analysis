from data.store.mutualfund import ensure_holdings_data, ensure_nav_data
from data.store.stock import ensure_stock_data
from data.fetchers.mutual_fund import search_advisorkhoj_schemes
from data.fetchers.stock import query_stocks
from mutual_funds.tradebook import load_tradebook, normalize_transactions
from datetime import datetime
import streamlit as st
import polars as pl
import pandas as pd


@st.cache_data(show_spinner=False)
def load_txn_data(path: str) -> pl.DataFrame:
    tradebook = load_tradebook(path)
    if tradebook.is_empty():
        return None
    return normalize_transactions(tradebook)


@st.cache_data(show_spinner=False)
def load_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    return ensure_nav_data(scheme_names)


@st.cache_data(show_spinner=True)
def load_nav_and_holdings(scheme_names, scheme_slugs):
    nav_df = ensure_nav_data(scheme_names)
    holdings_df, sectors_df, assets_df = ensure_holdings_data(scheme_slugs)
    return nav_df, holdings_df, sectors_df, assets_df


@st.cache_data(show_spinner=True)
def load_stock_open_close(
    symbols: list[str], start=datetime, end=datetime
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []

    for symbol in symbols:
        df = ensure_stock_data(symbol, start, end)

        # expected columns: date, open, close
        df = df.select(["Date", "Open", "Close", "High", "Low", "Volume"]).with_columns(
            pl.lit(symbol).alias("Symbol")
        )

        frames.append(df)

    if not frames:
        return pl.DataFrame(
            schema={
                "Date": pl.Date,
                "Symbol": pl.Utf8,
                "Open": pl.Float64,
                "High": pl.Float64,
                "Low": pl.Float64,
                "Close": pl.Float64,
            }
        )

    return pl.concat(frames)


@st.cache_data(ttl=24 * 3600)
def cached_search(query: str):
    return search_advisorkhoj_schemes(query)


@st.cache_data(ttl=24 * 3600)
def cached_search_stock(query: str) -> pd.DataFrame:
    return query_stocks(query)


def get_trade_symbols(trades_df: pl.DataFrame) -> list[str]:
    return trades_df.select("symbol").unique().sort("symbol").to_series().to_list()
