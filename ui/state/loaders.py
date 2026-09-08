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
from data.repositories.metadata import load_metadata
from data.repositories.nav import ensure_nav_data
from data.repositories.nav import refresh_nav_data as _refresh_nav
from data.repositories.stock import ensure_stock_data, search_stock_symbols
from data.repositories.tradebook import load_tradebook_from_db
from mutual_funds.display import unique_short_names
from mutual_funds.tradebook import normalize_transactions
from services.benchmarks import daily_returns_from_ohlcv
from services.screener_service import build_screener_df
from stocks.constants import to_bare_symbol

# Instrumentation note: only the heavy loaders carry @timeit (screener / metrics / stock
# bulk loads). @st.cache_data is the outermost decorator, so cache hits skip @timeit and
# anything in logs/perf.log is a real cache miss — work that actually ran.


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


@st.cache_data(ttl=3600, show_spinner="Loading benchmark…")
def load_benchmark_returns(symbol: str, start: datetime, end: datetime) -> pd.Series:
    """Daily percent-change series for `symbol`. Raises on fetch/parse failure;
    empty Series only when the fetcher legitimately yields no rows."""
    return daily_returns_from_ohlcv(ensure_stock_data(symbol, start, end), symbol)


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
        if df.is_empty():
            continue  # dropped symbols (no data) fall out; the page auto-removes confirmed-dead ones

        # Label with the canonical bare symbol so the frame matches how OHLCV is now keyed.
        bare = to_bare_symbol(symbol)
        df = df.select(["Date", "Open", "Close", "High", "Low", "Volume"]).with_columns(pl.lit(bare).alias("Symbol"))

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


@st.cache_data(show_spinner="Loading index…")
def load_index_ohlcv(symbol: str, start: datetime | None = None, end: datetime | None = None) -> pl.DataFrame:
    """Single-index OHLCV (index_ohlcv), shaped like load_stock_open_close for the chart."""
    from data.repositories.stock import ensure_index_data  # noqa: PLC0415 — defer heavy import off boot

    df = ensure_index_data(symbol, start, end)
    if df.is_empty():
        return df
    return df.select(["Date", "Open", "Close", "High", "Low", "Volume"]).with_columns(pl.lit(symbol).alias("Symbol"))


@st.cache_data(ttl=24 * 3600)
def cached_search(query: str) -> pl.DataFrame:
    """Fuzzy-search AMFI schemes by name. Returns a DataFrame with schemeName + metadata."""
    return search_amfi(query)


@st.cache_data(ttl=900, show_spinner=False)
def load_metadata_cached(scheme_names: tuple[str, ...]) -> pl.DataFrame:
    return load_metadata(list(scheme_names))


@st.cache_data(ttl=86400, show_spinner=False)
def get_short_names(scheme_names: tuple[str, ...]) -> dict[str, str]:
    return unique_short_names(scheme_names)


@st.cache_data(ttl=300, show_spinner=False)
@timeit("loaders.load_screener_df")
def load_screener_df_cached() -> pl.DataFrame:
    """Cached wrapper around build_screener_df; assembly lives in the service module."""
    return build_screener_df()


@st.cache_data(ttl=3600, show_spinner="Loading risk metrics…")
@timeit("loaders.load_metrics_cached")
def load_metrics_cached() -> pl.DataFrame:
    """Read pre-computed metrics from mf_scheme_metrics (recomputed on every NAV save)."""
    from services.mf_metrics import load_cached_metrics  # noqa: PLC0415 — defers quantstats off the app-boot path

    return load_cached_metrics()


@st.cache_data(ttl=24 * 3600)
def cached_search_stock(query: str) -> pd.DataFrame:
    return search_stock_symbols(query)


@st.cache_data(ttl=300, show_spinner=False)
@timeit("loaders.load_stock_screener_df")
def load_stock_screener_df_cached() -> pl.DataFrame:
    """Cached wrapper around build_stock_screener_df (registry ⨝ stock_metrics + alpha tag)."""
    from services.stock_screener_service import build_stock_screener_df  # noqa: PLC0415 — defer heavy import off boot

    return build_stock_screener_df()


@st.cache_data(ttl=900, show_spinner="Scanning for alerts…")
def load_overview_alerts_cached():
    """Cached alert scan for the Overview page (underperformance, concentration, staleness…)."""
    from services.insights_service import build_alerts  # noqa: PLC0415 — defer heavy import off boot

    return build_alerts()


@st.cache_data(ttl=900, show_spinner="Loading market data…")
def load_market_pulse_cached() -> pl.DataFrame:
    from services.insights_service import market_pulse  # noqa: PLC0415 — defer heavy import off boot

    return market_pulse()


@st.cache_data(ttl=900, show_spinner="Loading sector performance…")
def load_index_performance_cached() -> pl.DataFrame:
    from services.insights_service import index_performance  # noqa: PLC0415 — defer heavy import off boot

    return index_performance()


@st.cache_data(ttl=900, show_spinner="Loading international indices…")
def load_international_pulse_cached() -> pl.DataFrame:
    from services.insights_service import INTERNATIONAL_PULSE_SYMBOLS, market_pulse  # noqa: PLC0415

    return market_pulse(INTERNATIONAL_PULSE_SYMBOLS)


@st.cache_data(show_spinner="Loading index…")
def load_index_chart_ohlcv(symbol: str, start: datetime | None = None, end: datetime | None = None) -> pl.DataFrame:
    """Chart-ready OHLCV for any index straight from index_ohlcv (bhavcopy or yfinance-sourced)."""
    from data.repositories.stock import read_index_ohlcv  # noqa: PLC0415 — defer off boot

    return read_index_ohlcv(symbol, start, end)


@st.cache_data(ttl=900, show_spinner=False)
def load_index_valuation_cached(symbol: str) -> dict | None:
    """Latest P/E, P/B, Div Yield, turnover (₹ cr) for an index (from the bhavcopy)."""
    from data.repositories.stock import latest_index_valuation  # noqa: PLC0415 — defer off boot

    return latest_index_valuation(symbol)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_index_constituents_cached(index_name: str) -> list[str]:
    """Constituent stock symbols of an NSE index (via screener.in), best-effort + long-cached."""
    from services.market_data_service import index_constituents  # noqa: PLC0415 — defer off boot

    return index_constituents(index_name)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_analysis_catalog_cached() -> list[dict]:
    """Unified picker universe (stocks + ETFs + indices), each tagged with its kind. Long-cached; the
    ETF-classification sync (Settings) clears it."""
    from services.market_data_service import analysis_catalog  # noqa: PLC0415 — defer off boot

    return analysis_catalog()


@st.cache_data(ttl=3600, show_spinner=False)
def load_etf_metadata_cached() -> dict:
    """Live NSE ETF metadata keyed by symbol (underlying, NAV, 52-week range, 30d/1Y perf)."""
    from services.market_data_service import etf_metadata  # noqa: PLC0415 — defer off boot

    return etf_metadata()


@st.cache_data(ttl=600, show_spinner=False)
def load_global_search_cached(query: str) -> list[dict]:
    """Yahoo-wide ticker search results for the add-international flow (short-cached per query)."""
    from services.market_data_service import global_ticker_search  # noqa: PLC0415 — defer off boot

    return global_ticker_search(query)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_global_fundamentals_cached(symbol: str) -> dict | None:
    """yfinance fundamentals snapshot for an international ticker (live source, day-cached —
    mirrors the ETF-metadata pattern; not persisted to the screener tables, which are NSE/INR-based)."""
    from services.market_data_service import global_fundamentals  # noqa: PLC0415 — defer off boot

    return global_fundamentals(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def load_fund_movers_cached() -> pl.DataFrame:
    from services.insights_service import fund_movers  # noqa: PLC0415 — defer heavy import off boot

    return fund_movers()


def get_trade_symbols(trades_df: pl.DataFrame) -> list[str]:
    return trades_df.select(["symbol"]).unique().sort("symbol").to_series().to_list()


def refresh_all_data(scheme_names: list[str], scheme_slugs: list[str]):
    """Force re-fetch NAV and holdings data, clearing caches."""
    load_nav_data.clear()
    load_holdings_data.clear()
    _refresh_nav(scheme_names)
    _refresh_holdings(scheme_slugs)
