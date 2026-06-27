"""UI-package constants: risk-free rate, screener filter defaults, chart modes, settings tables.

Grouped by the view/concern that owns each block. Large enough that a future split into
per-subpackage constants modules (views/, persistence/) is reasonable.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from mutual_funds.metric_catalog import DEFAULT_VISIBLE_METRICS

# Risk-free rate for UI-side risk metrics (portfolio + single-fund tabs). NOTE: differs from
# services.RISK_FREE_ANNUAL (0.06) used by mf_metrics — see audit; values are intentionally kept.
RISK_FREE = 0.065
RF_DAILY = RISK_FREE / 252

# File-based persistence of user selections (ui.persistence.selections).
SELECTIONS_PATH = Path("data/user/selections.json")

# Screener sidebar filter widget keys + fallback defaults (ui.views.mf_screener.filters).
# Persisted to selections.json so filter state survives a full browser refresh.
SCREENER_PERSIST_KEY = "screener_filters"
FILTER_DEFAULTS = {
    "screener_name_query": "",
    "screener_amcs": [],
    "screener_cats": [],
    "screener_sub_cats": [],
    "screener_plans": ["Direct"],
    "screener_options": ["Growth"],
    "screener_aum_min": 0,
    "screener_ter_max": 2.5,
    "screener_min_age": 0.0,
    "screener_only_untracked": False,
    "screener_has_nav": False,
    "screener_visible_metrics": list(DEFAULT_VISIBLE_METRICS),
}
# Risk sliders only exist when "Has NAV" is on, so they seed/persist separately.
SLIDER_DEFAULTS = {
    "screener_cagr_min": -50,
    "screener_sharpe_min": -2.0,
    "screener_dd_min": -100,
}

# Screener add-to-tracked control help (ui.views.mf_screener.backfill).
BACKFILL_HELP_TEXT = (
    "Fetch NAV + metadata for the top-N rows of the filtered view (in the sort order shown), "
    "then compute their risk/return metrics. Funds already loaded are skipped. Throttled to "
    "~3 req/s to respect upstream rate limits. Holdings aren't fetched here (slower scrape) — "
    "use Settings → *Update All Holdings*."
)

# Status-badge colour palette for the refresh table (ui.views.settings.refresh).
STATUS_STYLES = {
    "Fresh": "background-color: #86efac; color: #14532d",
    "Stale": "background-color: #fde68a; color: #78350f",
    "Missing": "background-color: #fca5a5; color: #7f1d1d",
    "Available": "background-color: #86efac; color: #14532d",
    "Pending": "background-color: #fde68a; color: #78350f",
    "Unavailable": "background-color: #fca5a5; color: #7f1d1d",
}

# Portfolio risk-vs-return scatter (ui.views.portfolio.risk_vs_return).
MODE_CAGR_VOL = "CAGR vs Volatility"
MODE_ALPHA_BETA = "Alpha vs Beta"
BUBBLE_SIZE = 14
PORTFOLIO_COLOUR = "#fbbf24"  # amber-400

# Settings → Data Sources reference tables (ui.views.settings.data_sources).
DATA_SOURCES_TABLE = pd.DataFrame(
    [
        # ----- External fetchers -----
        {
            "Source": "AMFI NAVAll.txt",
            "What it returns": "Master list of every Indian MF scheme (code, name, ISINs, latest NAV, AMC, category)",
            "Input": "None — single bulk download",
            "Example": "sync_amfi_master()",
            "Lands in": "amfi_schemes (+ mf_amc, mf_category dims)",
        },
        {
            "Source": "MFAPI (api.mfapi.in)",
            "What it returns": "Full historical NAV time series for one scheme",
            "Input": "scheme_code (int, AMFI-issued)",
            "Example": 'fetch_nav_from_mfapi("122639", "Parag Parikh Flexi Cap…")',
            "Lands in": "mf_nav",
        },
        {
            "Source": "AdvisorKhoj — portfolio page",
            "What it returns": "Holdings (each stock + weight + ISIN), sector allocation, asset allocation",
            "Input": "scheme_slug (computed from name via make_slug)",
            "Example": 'fetch_portfolio_by_slug("parag-parikh-flexi-cap-fund-…")',
            "Lands in": "mf_holdings, mf_sector_allocation, mf_asset_allocation",
        },
        {
            "Source": "AdvisorKhoj — overview page",
            "What it returns": "AUM, TER, benchmark, launch date, exit load, category, asset class, min investment, turnover",
            "Input": "scheme_name (slug derived internally)",
            "Example": 'fetch_fund_metadata("Parag Parikh Flexi Cap…")',
            "Lands in": "mf_metadata (+ mf_amc, mf_category dims for new AMCs/cats)",
        },
        {
            "Source": "AMFI fuzzy search (local DB, pg_trgm)",
            "What it returns": "Trigram-similarity ranked scheme names + code + ISIN (with AMC + category JOINed in)",
            "Input": "free-text query >= 2 chars",
            "Example": 'search_amfi("hdfc top 100")',
            "Lands in": "(query-time only)",
        },
        {
            "Source": "yfinance",
            "What it returns": "Daily OHLCV bars (global tickers, indices including ^NSEI)",
            "Input": "symbol + optional start/end dates",
            "Example": 'ensure_stock_data("^NSEI", date(2020,1,1), date(2026,5,9))',
            "Lands in": "stock_ohlcv",
        },
        {
            "Source": "jugaad-data (NSE bhavcopy)",
            "What it returns": "OHLCV from NSE for Indian symbols (tried before yfinance for .NS)",
            "Input": "symbol without .NS suffix, date range",
            "Example": 'ensure_stock_data("RELIANCE", …)',
            "Lands in": "stock_ohlcv",
        },
        {
            "Source": "Kite/Zerodha tradebook CSV",
            "What it returns": "One row per trade (trade_id, symbol, isin, date, qty, price, type)",
            "Input": "CSV bytes via file upload",
            "Example": "import_tradebook_bytes(uploaded.getvalue())",
            "Lands in": "mf_tradebook",
        },
        # ----- Derived / internal -----
        {
            "Source": "services.mf_metrics.recompute_metrics",
            "What it returns": "31 cached metrics per scheme (CAGR, Vol, Sharpe, Sortino, Calmar, VaR/CVaR, "
            "rolling 1Y/3Y/5Y, alpha/beta vs Nifty 50, holdings-derived comps, top-N concentration)",
            "Input": "scheme_names list (or all NAV-having schemes); Nifty 50 loaded once as benchmark",
            "Example": 'recompute_metrics(["Parag Parikh…"])',
            "Lands in": "mf_scheme_metrics",
        },
    ]
)

SCHEMA_TABLES = pd.DataFrame(
    [
        # ----- Dim tables -----
        {
            "Table": "amfi_schemes",
            "PK": "scheme_code (int)",
            "Holds": "Canonical scheme dim. Includes synthetic negative codes for tracked funds AMFI doesn't list.",
            "FK targets": "mf_amc.id, mf_category.id",
        },
        {
            "Table": "mf_amc",
            "PK": "id (serial)",
            "Holds": "Asset Management Companies (~50 distinct values). Replaces fund_house TEXT repeated 14k times before normalisation.",
            "FK targets": "—",
        },
        {
            "Table": "mf_category",
            "PK": "id (serial)",
            "Holds": "Scheme categories (~110 distinct values). Replaces category TEXT repeated 14k times before normalisation.",
            "FK targets": "—",
        },
        # ----- Scheme-keyed tables -----
        {
            "Table": "mf_nav",
            "PK": "(scheme_code, date)",
            "Holds": "Daily NAV per scheme (~2.3M rows). Phase 2 saved ~336 MB by switching from scheme_name TEXT PK to scheme_code int FK.",
            "FK targets": "amfi_schemes.scheme_code",
        },
        {
            "Table": "mf_metadata",
            "PK": "scheme_code",
            "Holds": "AdvisorKhoj-sourced metadata: AUM, TER, benchmark, fund manager, exit load, launch date, etc.",
            "FK targets": "amfi_schemes.scheme_code, mf_amc.id, mf_category.id",
        },
        {
            "Table": "mf_scheme_metrics",
            "PK": "scheme_code",
            "Holds": "31 pre-computed risk/return metrics per scheme. Backed by services.mf_metrics.recompute_metrics; auto-recomputes after every NAV save.",
            "FK targets": "amfi_schemes.scheme_code",
        },
        {
            "Table": "mf_registry",
            "PK": "scheme_code",
            "Holds": "Tracked-funds list with per-source nav/holdings/metadata status. Keyed on scheme_code with FK to amfi_schemes.",
            "FK targets": "amfi_schemes.scheme_code",
        },
        # ----- Holdings family -----
        {
            "Table": "mf_holdings, mf_sector_allocation, mf_asset_allocation",
            "PK": "id (auto)",
            "Holds": "Per-fund holdings + sector + asset weights from AdvisorKhoj. Slugs are derived at the API boundary; persisted rows FK by scheme_code.",
            "FK targets": "amfi_schemes.scheme_code",
        },
        # ----- Tradebook -----
        {
            "Table": "mf_tradebook",
            "PK": "trade_id (str)",
            "Holds": "Kite/Zerodha trade rows. Resolution to scheme_name happens live via mf_tradebook.isin = amfi_schemes.isin_growth.",
            "FK targets": "(planned) amfi_schemes.scheme_code",
        },
        # ----- Stock side -----
        {
            "Table": "stock_ohlcv",
            "PK": "(symbol, date)",
            "Holds": "Daily OHLCV bars for Indian stocks + benchmark indices (^NSEI etc).",
            "FK targets": "—",
        },
    ]
)
