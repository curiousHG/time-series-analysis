from datetime import datetime

import pandas as pd
import polars as pl
import streamlit as st

from core.timing import timeit
from data.repositories.amfi import search_amfi
from data.repositories.holdings import (
    ensure_holdings_data,
)
from data.repositories.holdings import (
    refresh_holdings_data as _refresh_holdings,
)
from data.repositories.nav import ensure_nav_data
from data.repositories.nav import refresh_nav_data as _refresh_nav
from data.repositories.stock import ensure_stock_data, search_stock_symbols
from data.repositories.tradebook import load_tradebook_from_db
from mutual_funds.tradebook import normalize_transactions

# Instrumentation note: @st.cache_data is the outermost decorator on every loader so cache
# hits return without entering @timeit. Anything that lands in logs/perf.log here is a real
# cache miss — i.e. work that actually ran.


@st.cache_data(show_spinner=False)
@timeit("loaders.load_txn_data")
def load_txn_data() -> pl.DataFrame | None:
    tradebook = load_tradebook_from_db()
    if tradebook.is_empty():
        return None
    return normalize_transactions(tradebook)


@st.cache_data(show_spinner="Loading NAV data...", ttl=3600)
@timeit("loaders.load_nav_data")
def load_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    return ensure_nav_data(scheme_names)


@st.cache_data(show_spinner="Loading holdings data...", ttl=3600)
@timeit("loaders.load_holdings_data")
def load_holdings_data(scheme_slugs: list[str]):
    return ensure_holdings_data(scheme_slugs)


@st.cache_data(ttl=3600, show_spinner="Loading benchmark…")
@timeit("loaders.load_benchmark_returns")
def load_benchmark_returns(symbol: str, start: datetime, end: datetime) -> pd.Series:
    """Daily percent-change series for a benchmark `symbol`.

    Raises on fetch/parse failure — the caller is expected to surface the error.
    Returns an empty Series only when the underlying fetcher legitimately yields no rows.
    """
    df = ensure_stock_data(symbol, start, end)
    if df.is_empty():
        return pd.Series(dtype="float64", name=symbol)
    pdf = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()
    return pdf["Close"].pct_change().dropna().rename(symbol)


def load_nifty_returns(start: datetime, end: datetime) -> pd.Series:
    """Daily Nifty 50 percent-change series — thin wrapper over load_benchmark_returns."""
    return load_benchmark_returns("^NSEI", start, end).rename("nifty")


@st.cache_data(show_spinner=True)
@timeit("loaders.load_stock_open_close")
def load_stock_open_close(
    symbols: list[str], start: datetime | None = None, end: datetime | None = None
) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []

    for symbol in symbols:
        df = ensure_stock_data(symbol, start, end)

        # expected columns: date, open, close
        df = df.select(["Date", "Open", "Close", "High", "Low", "Volume"]).with_columns(pl.lit(symbol).alias("Symbol"))

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
@timeit("loaders.cached_search_amfi")
def cached_search(query: str) -> pl.DataFrame:
    """Fuzzy-search AMFI schemes by name. Returns a DataFrame with schemeName + metadata."""
    return search_amfi(query)


@st.cache_data(ttl=900, show_spinner=False)
@timeit("loaders.load_metadata_cached")
def load_metadata_cached(scheme_names: tuple[str, ...]) -> pl.DataFrame:
    from data.repositories.metadata import load_metadata

    return load_metadata(list(scheme_names))


@st.cache_data(ttl=86400, show_spinner=False)
@timeit("loaders.get_short_names")
def get_short_names(scheme_names: tuple[str, ...]) -> dict[str, str]:
    from mutual_funds.display import short_scheme_name

    return {n: short_scheme_name(n) for n in scheme_names}


@st.cache_data(ttl=300, show_spinner=False)
@timeit("loaders.load_screener_df")
def load_screener_df_cached() -> pl.DataFrame:
    """Streamlit-cached wrapper around `services.screener_service.build_screener_df`.

    Pure data assembly lives in the service module; this wrapper just layers Streamlit's
    in-memory cache + the perf-log decoration.
    """
    from services.screener_service import build_screener_df

    return build_screener_df()


@st.cache_data(ttl=3600, show_spinner="Loading risk metrics…")
@timeit("loaders.load_metrics_cached")
def load_metrics_cached() -> pl.DataFrame:
    """Read pre-computed metrics from mf_scheme_metrics.

    Recompute happens automatically after every NAV save (see data/repositories/nav.py)
    and can be triggered manually via Settings → Recompute metrics, or
    `uv run python scripts/compute_metrics.py`.
    """
    from services.mf_metrics import load_cached_metrics

    return load_cached_metrics()


@st.cache_data(ttl=24 * 3600)
@timeit("loaders.cached_search_stock")
def cached_search_stock(query: str) -> pd.DataFrame:
    return search_stock_symbols(query)


def get_trade_symbols(trades_df: pl.DataFrame) -> list[str]:
    return trades_df.select(["symbol"]).unique().sort("symbol").to_series().to_list()


def refresh_all_data(scheme_names: list[str], scheme_slugs: list[str]):
    """Force re-fetch NAV and holdings data, clearing caches."""
    load_nav_data.clear()
    load_holdings_data.clear()
    _refresh_nav(scheme_names)
    _refresh_holdings(scheme_slugs)
