from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd
import polars as pl
import streamlit as st

from core.enums import SourceStatus
from core.timing import timeit
from data.fetchers.stock import search_stocks
from data.repositories.amfi import (
    get_latest_amfi_nav_date,
    get_scheme_count,
    get_scheme_details_by_name,
    load_amfi_df,
    search_amfi,
    sync_amfi_master,
)
from data.repositories.holdings import (
    ensure_holdings_data,
    holdings_count_by_scheme,
    load_assets,
    load_holdings,
    load_latest_asset_allocation,
    load_latest_holdings,
    load_sectors,
)
from data.repositories.holdings import (
    refresh_holdings_data as _refresh_holdings,
)
from data.repositories.metadata import load_metadata
from data.repositories.nav import load_nav_df, nav_record_stats
from data.repositories.stock_fundamentals import ensure_stock_fundamentals, fundamentals_status_of
from data.repositories.stock_ohlcv import ensure_stock_data, register_stock
from data.repositories.tradebook import (
    TradebookStats,
    get_tradebook_stats,
    import_tradebook_bytes,
    load_tradebook_from_db,
)
from mutual_funds.display import unique_short_names
from mutual_funds.tradebook import normalize_transactions
from services.benchmarks import daily_returns_from_ohlcv
from services.mf.nav_sources import ensure_nav_data
from services.mf.nav_sources import refresh_nav_data as _refresh_nav
from services.mf.screener import build_screener_df
from stocks.constants import to_bare_symbol

if TYPE_CHECKING:
    from data.repositories.index_ohlcv import IndexValuation
    from services.stock.sync import AddedStocks

TTL_FAST = 300
TTL_INTRADAY = 900
TTL_HOURLY = 3600
TTL_DAILY = 24 * 3600


@dataclass(frozen=True, slots=True)
class AmfiStatus:
    """Size and freshness of the AMFI master."""

    scheme_count: int
    latest_nav_date: date | None


@dataclass(frozen=True, slots=True)
class FundHoldingsDetail:
    """Sector and asset allocation plus the newest holdings and asset-allocation snapshots of one fund."""

    sectors: pl.DataFrame
    assets: pl.DataFrame
    latest_holdings: pl.DataFrame
    latest_allocation: list[tuple]


@st.cache_data(show_spinner=False)
def load_txn_data() -> pl.DataFrame | None:
    tradebook = load_tradebook_from_db()
    if tradebook.is_empty():
        return None
    return normalize_transactions(tradebook)


@st.cache_data(show_spinner="Loading NAV data...", ttl=TTL_HOURLY)
def load_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    return ensure_nav_data(scheme_names)


@st.cache_data(show_spinner="Loading holdings data...", ttl=TTL_HOURLY)
def load_holdings_data(scheme_slugs: list[str]):
    return ensure_holdings_data(scheme_slugs)


@st.cache_data(ttl=TTL_HOURLY, show_spinner="Loading benchmark…")
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
            continue

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
    from data.repositories.index_ohlcv import ensure_index_data  # noqa: PLC0415 — defer heavy import off boot

    df = ensure_index_data(symbol, start, end)
    if df.is_empty():
        return df
    return df.select(["Date", "Open", "Close", "High", "Low", "Volume"]).with_columns(pl.lit(symbol).alias("Symbol"))


@st.cache_data(ttl=TTL_DAILY)
def cached_search(query: str) -> pl.DataFrame:
    """Fuzzy-search AMFI schemes by name. Returns a DataFrame with schemeName + metadata."""
    return search_amfi(query)


@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def load_metadata_cached(scheme_names: tuple[str, ...]) -> pl.DataFrame:
    return load_metadata(list(scheme_names))


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def get_short_names(scheme_names: tuple[str, ...]) -> dict[str, str]:
    return unique_short_names(scheme_names)


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
@timeit("loaders.load_screener_df")
def load_screener_df_cached() -> pl.DataFrame:
    """Cached wrapper around build_screener_df; assembly lives in the service module."""
    return build_screener_df()


@st.cache_data(ttl=TTL_HOURLY, show_spinner="Loading risk metrics…")
@timeit("loaders.load_metrics_cached")
def load_metrics_cached() -> pl.DataFrame:
    """Read pre-computed metrics from mf_scheme_metrics (recomputed on every NAV save)."""
    from services.mf.metrics import load_cached_metrics  # noqa: PLC0415 — defers quantstats off the app-boot path

    return load_cached_metrics()


@st.cache_data(ttl=TTL_DAILY)
def cached_search_stock(query: str) -> pd.DataFrame:
    return search_stocks(query)


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
@timeit("loaders.load_stock_screener_df")
def load_stock_screener_df_cached() -> pl.DataFrame:
    """Cached wrapper around build_stock_screener_df (registry ⨝ stock_metrics + alpha tag)."""
    from services.stock.screener import build_stock_screener_df  # noqa: PLC0415 — defer heavy import off boot

    return build_stock_screener_df()


@st.cache_data(ttl=TTL_INTRADAY, show_spinner="Scanning for alerts…")
def load_overview_alerts_cached():
    """Cached alert scan for the Overview page (underperformance, concentration, staleness…)."""
    from services.overview.alerts import build_alerts  # noqa: PLC0415 — defer heavy import off boot

    return build_alerts()


@st.cache_data(ttl=TTL_INTRADAY, show_spinner="Loading market data…")
def load_market_pulse_cached() -> pl.DataFrame:
    from services.overview.market import market_pulse  # noqa: PLC0415 — defer heavy import off boot

    return market_pulse()


@st.cache_data(ttl=TTL_INTRADAY, show_spinner="Loading sector performance…")
def load_index_performance_cached() -> pl.DataFrame:
    from services.overview.market import index_performance  # noqa: PLC0415 — defer heavy import off boot

    return index_performance()


@st.cache_data(ttl=TTL_INTRADAY, show_spinner="Loading international indices…")
def load_international_pulse_cached() -> pl.DataFrame:
    from services.overview.market import INTERNATIONAL_PULSE_SYMBOLS, market_pulse  # noqa: PLC0415

    return market_pulse(INTERNATIONAL_PULSE_SYMBOLS)


@st.cache_data(show_spinner="Loading index…")
def load_index_chart_ohlcv(symbol: str, start: datetime | None = None, end: datetime | None = None) -> pl.DataFrame:
    """Chart-ready OHLCV for any index straight from index_ohlcv (bhavcopy or yfinance-sourced)."""
    from data.repositories import index_ohlcv  # noqa: PLC0415 — defer off boot

    return index_ohlcv.load_index_ohlcv(symbol, start, end)


@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def load_index_valuation_cached(symbol: str) -> "IndexValuation | None":
    """Latest P/E, P/B, Div Yield, turnover (₹ cr) for an index (from the bhavcopy)."""
    from data.repositories.index_ohlcv import get_latest_index_valuation  # noqa: PLC0415 — defer off boot

    return get_latest_index_valuation(symbol)


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def load_index_constituents_cached(index_name: str) -> list[str]:
    """Constituent stock symbols of an NSE index (via screener.in), best-effort + long-cached."""
    from services.stock.market_data import index_constituents  # noqa: PLC0415 — defer off boot

    return index_constituents(index_name)


@st.cache_data(ttl=6 * TTL_HOURLY, show_spinner=False)
def load_analysis_catalog_cached() -> list[dict]:
    """Unified picker universe (stocks + ETFs + indices), each tagged with its kind. Long-cached; the
    ETF-classification sync (Settings) clears it."""
    from services.stock.market_data import analysis_catalog  # noqa: PLC0415 — defer off boot

    return analysis_catalog()


@st.cache_data(ttl=TTL_HOURLY, show_spinner=False)
def load_etf_metadata_cached() -> dict:
    """Live NSE ETF metadata keyed by symbol (underlying, NAV, 52-week range, 30d/1Y perf)."""
    from services.stock.market_data import etf_metadata  # noqa: PLC0415 — defer off boot

    return etf_metadata()


@st.cache_data(ttl=600, show_spinner=False)
def load_global_search_cached(query: str) -> list[dict]:
    """Yahoo-wide ticker search results for the add-international flow (short-cached per query)."""
    from services.stock.market_data import global_ticker_search  # noqa: PLC0415 — defer off boot

    return global_ticker_search(query)


@st.cache_data(ttl=TTL_DAILY, show_spinner=False)
def load_global_fundamentals_cached(symbol: str) -> dict | None:
    """yfinance fundamentals snapshot for an international ticker (live source, day-cached —
    mirrors the ETF-metadata pattern; not persisted to the screener tables, which are NSE/INR-based)."""
    from services.stock.market_data import global_fundamentals  # noqa: PLC0415 — defer off boot

    return global_fundamentals(symbol)


@st.cache_data(ttl=TTL_INTRADAY, show_spinner=False)
def load_fund_movers_cached() -> pl.DataFrame:
    from services.overview.market import fund_movers  # noqa: PLC0415 — defer heavy import off boot

    return fund_movers()


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_amfi_status_cached() -> AmfiStatus:
    """AMFI master scheme count and latest NAV date; cleared by sync_amfi."""
    return AmfiStatus(scheme_count=get_scheme_count(), latest_nav_date=get_latest_amfi_nav_date())


@st.cache_data(ttl=TTL_HOURLY, show_spinner=False)
def load_amfi_df_cached() -> pl.DataFrame:
    """All AMFI schemes with AMC and category names; cleared by sync_amfi."""
    return load_amfi_df()


@st.cache_data(ttl=TTL_HOURLY, show_spinner=False)
def load_scheme_details_cached(scheme_name: str) -> dict | None:
    """One scheme's AMFI master row plus fund house and category text; cleared by sync_amfi."""
    return get_scheme_details_by_name(scheme_name)


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_nav_df_cached(scheme_names: tuple[str, ...]) -> pl.DataFrame:
    """Stored NAV for the schemes (all when empty) without fetching; cleared by clear_fund_data_caches."""
    return load_nav_df(list(scheme_names))


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_nav_record_stats_cached(scheme_names: tuple[str, ...]) -> dict[str, tuple[int, object]]:
    """{scheme_name: (NAV record count, earliest date)}; cleared by clear_fund_data_caches."""
    return nav_record_stats(list(scheme_names))


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_holdings_db_cached(slugs: tuple[str, ...]) -> pl.DataFrame:
    """Stored holdings for the slugs (all when empty) without fetching; cleared by clear_fund_data_caches."""
    return load_holdings(list(slugs))


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_fund_holdings_detail_cached(slug: str) -> FundHoldingsDetail:
    """Stored sector/asset allocation and newest snapshots of one fund; cleared by clear_fund_data_caches."""
    return FundHoldingsDetail(
        sectors=load_sectors([slug]),
        assets=load_assets([slug]),
        latest_holdings=load_latest_holdings(slug),
        latest_allocation=load_latest_asset_allocation(slug),
    )


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_holdings_counts_cached(scheme_names: tuple[str, ...]) -> dict[str, int]:
    """{scheme_name: holdings row count}; cleared by clear_fund_data_caches."""
    return holdings_count_by_scheme(list(scheme_names))


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_tradebook_cached() -> pl.DataFrame:
    """Stored tradebook with scheme names joined; cleared by import_tradebook and sync_amfi."""
    return load_tradebook_from_db()


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_tradebook_stats_cached() -> TradebookStats:
    """Tradebook counts and date span; cleared by import_tradebook."""
    return get_tradebook_stats()


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_fundamentals_status_cached(symbol: str) -> SourceStatus | None:
    """Fundamentals scrape status of a bare symbol; cleared by clear_stock_data_caches."""
    return fundamentals_status_of(symbol)


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_bhavcopy_index_names_cached() -> list[str]:
    """NSE indices published in the recent bhavcopy; cleared by clear_stock_data_caches and clear_market_caches."""
    from data.repositories.index_ohlcv import list_bhavcopy_index_names  # noqa: PLC0415

    return list_bhavcopy_index_names()


def clear_fund_data_caches() -> None:
    """Drop every cache fed by NAV, holdings, metadata and metrics rows; call after any flow that writes them."""
    for loader in (
        load_nav_data,
        load_holdings_data,
        load_metadata_cached,
        load_metrics_cached,
        load_screener_df_cached,
        load_nav_df_cached,
        load_nav_record_stats_cached,
        load_holdings_db_cached,
        load_fund_holdings_detail_cached,
        load_holdings_counts_cached,
    ):
        loader.clear()


def clear_amfi_caches() -> None:
    """Drop every cache derived from the AMFI master."""
    for loader in (
        load_amfi_status_cached,
        load_amfi_df_cached,
        load_scheme_details_cached,
        cached_search,
        load_screener_df_cached,
        load_tradebook_cached,
        load_tradebook_stats_cached,
        load_txn_data,
    ):
        loader.clear()


def clear_stock_data_caches() -> None:
    """Drop every cache fed by stock and index OHLCV, registry and fundamentals rows."""
    for loader in (
        load_stock_open_close,
        load_stock_screener_df_cached,
        load_analysis_catalog_cached,
        load_fundamentals_status_cached,
        load_bhavcopy_index_names_cached,
    ):
        loader.clear()


def clear_market_caches() -> None:
    """Drop the Overview board caches and the price caches a market-data refresh invalidates."""
    for loader in (
        load_market_pulse_cached,
        load_index_performance_cached,
        load_international_pulse_cached,
        load_overview_alerts_cached,
        load_fund_movers_cached,
        load_bhavcopy_index_names_cached,
        load_stock_open_close,
    ):
        loader.clear()


def sync_amfi() -> int:
    """Sync the AMFI master and clear the caches derived from it. Returns the scheme count."""
    count = sync_amfi_master()
    clear_amfi_caches()
    return count


def import_tradebook(file_bytes: bytes) -> tuple[int, int]:
    """Import a tradebook CSV; clears the tradebook caches when trades were added. Returns (new, skipped)."""
    new_count, skipped = import_tradebook_bytes(file_bytes)
    if new_count > 0:
        load_txn_data.clear()
        load_tradebook_cached.clear()
        load_tradebook_stats_cached.clear()
    return new_count, skipped


def sync_fundamentals(symbol: str) -> None:
    """Scrape screener.in fundamentals for a bare symbol; the screener and status caches are cleared even if it raises."""
    try:
        ensure_stock_fundamentals([symbol])
    finally:
        load_stock_screener_df_cached.clear()
        load_fundamentals_status_cached.clear()


def register_global_stock(symbol: str, *, name: str, exchange: str, quote_type: str) -> None:
    """Register a stock outside the NSE master and clear the picker catalog."""
    register_stock(symbol, name=name, exchange=exchange, quote_type=quote_type)
    load_analysis_catalog_cached.clear()


def add_stocks(picks: list[tuple[str, str, str]]) -> "AddedStocks":
    """Add (symbol, name, exchange) picks with full history and fundamentals, then clear the stock caches."""
    from services.stock.sync import add_stocks as _add_stocks  # noqa: PLC0415

    try:
        return _add_stocks(picks)
    finally:
        clear_stock_data_caches()


def add_index(symbol: str) -> None:
    """Pull an index's full history into index_ohlcv and clear the index chart caches."""
    from services.stock.sync import ensure_index_history  # noqa: PLC0415

    try:
        ensure_index_history(symbol)
    finally:
        load_index_ohlcv.clear()
        load_index_chart_ohlcv.clear()
        load_bhavcopy_index_names_cached.clear()


def get_trade_symbols(trades_df: pl.DataFrame) -> list[str]:
    return trades_df.select(["symbol"]).unique().sort("symbol").to_series().to_list()


def refresh_all_data(scheme_names: list[str], scheme_slugs: list[str]):
    """Force re-fetch NAV and holdings data, clearing caches."""
    load_nav_data.clear()
    load_holdings_data.clear()
    _refresh_nav(scheme_names)
    _refresh_holdings(scheme_slugs)


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_backtest_runs_cached() -> pl.DataFrame:
    """Every stored Backtest Lab run, newest first — id/name/status/config plus the summary metrics."""
    from services.backtest.runs import list_runs  # noqa: PLC0415 — pulls the engine + quantstats in

    return list_runs()


@st.cache_data(ttl=TTL_FAST, show_spinner="Loading run…")
def load_backtest_run_cached(run_id: int):
    """One run in full: config, metrics, trades, equity, benchmark and the per-symbol/exit splits."""
    from services.backtest.runs import load_run  # noqa: PLC0415 — pulls the engine + quantstats in

    return load_run(run_id)


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_optimization_cached(optimization_id: int):
    """One optimisation with its persisted trials (never a live Optuna study)."""
    from services.backtest.runs import load_optimization  # noqa: PLC0415 — pulls the engine in

    return load_optimization(optimization_id)


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_strategy_catalog_cached() -> dict:
    """Discoverable basket strategies with their declared parameters; short TTL so new strategy files appear."""
    from services.backtest.runs import strategy_catalog  # noqa: PLC0415 — pulls the engine in

    return strategy_catalog()


@st.cache_data(ttl=TTL_FAST, show_spinner=False)
def load_strategy_health_cached() -> dict:
    """Strategy files that failed to import, name clashes, and where the directory lives."""
    from services.backtest.runs import (  # noqa: PLC0415 — pulls the engine in
        strategy_directory,
        strategy_load_errors,
        strategy_warnings,
    )

    return {"errors": strategy_load_errors(), "warnings": strategy_warnings(), "directory": strategy_directory()}


def reload_strategies() -> None:
    """Re-scan the strategy directory on the next read."""
    load_strategy_catalog_cached.clear()
    load_strategy_health_cached.clear()


@st.cache_data(ttl=TTL_HOURLY, show_spinner=False)
def load_universe_cached(kind: str, name: str, symbols: tuple[str, ...], watchlist: tuple[str, ...] = ()) -> list[str]:
    """Bare NSE symbols a universe spec resolves to: index constituents, the watchlist, or a list."""
    from services.backtest.config import UniverseKind, UniverseSpec  # noqa: PLC0415 — keeps the service off page boot
    from services.backtest.universe import resolve_universe  # noqa: PLC0415

    return resolve_universe(
        UniverseSpec(kind=UniverseKind(kind), name=name, symbols=tuple(symbols)), watchlist=list(watchlist)
    )


def clear_backtest_caches() -> None:
    """Drop the run caches after a create/launch/edit/delete. The strategy catalog and resolved
    universes are left alone — they don't change with a run and re-resolving an index costs a fetch."""
    load_backtest_runs_cached.clear()
    load_backtest_run_cached.clear()
    load_optimization_cached.clear()
