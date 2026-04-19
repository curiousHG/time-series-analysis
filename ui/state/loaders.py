from data.store.mutual_fund import (
    ensure_holdings_data,
    ensure_nav_data,
    refresh_nav_data as _refresh_nav,
    refresh_holdings_data as _refresh_holdings,
)
from data.store.stock import ensure_stock_data
from data.fetchers.mutual_fund import search_advisorkhoj_schemes
from data.fetchers.stock import query_stocks
from data.store.tradebook import load_tradebook_from_db
from mutual_funds.tradebook import normalize_transactions
from datetime import datetime
import streamlit as st
import polars as pl
import pandas as pd


@st.cache_data(show_spinner=False)
def load_txn_data() -> pl.DataFrame | None:
    tradebook = load_tradebook_from_db()
    if tradebook.is_empty():
        return None
    return normalize_transactions(tradebook)


@st.cache_data(show_spinner="Loading NAV data...", ttl=3600)
def load_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    return ensure_nav_data(scheme_names)


@st.cache_data(show_spinner="Loading holdings data...", ttl=3600)
def load_holdings_data(scheme_slugs: list[str]):
    return ensure_holdings_data(scheme_slugs)


@st.cache_data(show_spinner=True)
def load_stock_open_close(
    symbols: list[str], start: datetime = None, end: datetime = None
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
    return trades_df.select(["symbol"]).unique().sort("symbol").to_series().to_list()


def refresh_all_data(scheme_names: list[str], scheme_slugs: list[str]):
    """Force re-fetch NAV and holdings data, clearing caches."""
    load_nav_data.clear()
    load_holdings_data.clear()
    _refresh_nav(scheme_names)
    _refresh_holdings(scheme_slugs)
