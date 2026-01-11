from data.store.mutualfund import ensure_holdings_data, ensure_nav_data
from data.fetchers.mutual_fund import search_advisorkhoj_schemes
from mutual_funds.tradebook import load_tradebook, normalize_transactions
import streamlit as st
import polars as pl


@st.cache_data(show_spinner=False)
def load_txn_data(path: str)->pl.DataFrame:
    tradebook = load_tradebook(path)
    if not tradebook:
        return None
    return normalize_transactions(tradebook)
    

@st.cache_data(show_spinner=False)
def load_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    return ensure_nav_data(scheme_names)

@st.cache_data(show_spinner=False)
def load_nav_and_holdings(scheme_names, scheme_slugs):
    nav_df = ensure_nav_data(scheme_names)
    holdings_df, sectors_df, assets_df = ensure_holdings_data(scheme_slugs)
    return nav_df, holdings_df, sectors_df, assets_df

@st.cache_data(ttl=24 * 3600)
def cached_search(query: str):
    return search_advisorkhoj_schemes(query)

@st.cache_data(ttl=24*3600)
def cachec_search_stock(query:str):
    return 

def get_trade_symbols(trades_df: pl.DataFrame) -> list[str]:
    return (
        trades_df
        .select("symbol")
        .unique()
        .sort("symbol")
        .to_series()
        .to_list()
    )