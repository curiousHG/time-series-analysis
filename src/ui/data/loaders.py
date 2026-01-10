from mutualFunds.data_store import ensure_holdings_data, ensure_nav_data
from mutualFunds.tradebook import load_tradebook, normalize_transactions
import streamlit as st
import polars as pl


@st.cache_data(show_spinner=False)
def load_txn_data(path: str)->pl.DataFrame:
    tradebook = load_tradebook(path)
    return normalize_transactions(tradebook)


@st.cache_data(show_spinner=False)
def load_nav_and_holdings(scheme_names, scheme_slugs):
    nav_df = ensure_nav_data(scheme_names)
    holdings_df, sectors_df, assets_df = ensure_holdings_data(scheme_slugs)
    return nav_df, holdings_df, sectors_df, assets_df

def get_trade_symbols(trades_df: pl.DataFrame) -> list[str]:
    return (
        trades_df
        .select("symbol")
        .unique()
        .sort("symbol")
        .to_series()
        .to_list()
    )