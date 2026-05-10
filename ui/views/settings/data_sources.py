"""Settings → Data Sources reference table + chain-of-fetches expander."""

from __future__ import annotations

import pandas as pd
import streamlit as st

_DATA_SOURCES = pd.DataFrame(
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

_SCHEMA_TABLES = pd.DataFrame(
    [
        # ----- Dim tables -----
        {"Table": "amfi_schemes", "PK": "scheme_code (int)",
         "Holds": "Canonical scheme dim. Includes synthetic negative codes for tracked funds AMFI doesn't list.",
         "FK targets": "mf_amc.id, mf_category.id"},
        {"Table": "mf_amc", "PK": "id (serial)",
         "Holds": "Asset Management Companies (~50 distinct values). Replaces fund_house TEXT repeated 14k times before normalisation.",
         "FK targets": "—"},
        {"Table": "mf_category", "PK": "id (serial)",
         "Holds": "Scheme categories (~110 distinct values). Replaces category TEXT repeated 14k times before normalisation.",
         "FK targets": "—"},
        # ----- Scheme-keyed tables -----
        {"Table": "mf_nav", "PK": "(scheme_code, date)",
         "Holds": "Daily NAV per scheme (~2.3M rows). Phase 2 saved ~336 MB by switching from scheme_name TEXT PK to scheme_code int FK.",
         "FK targets": "amfi_schemes.scheme_code"},
        {"Table": "mf_metadata", "PK": "scheme_code",
         "Holds": "AdvisorKhoj-sourced metadata: AUM, TER, benchmark, fund manager, exit load, launch date, etc.",
         "FK targets": "amfi_schemes.scheme_code, mf_amc.id, mf_category.id"},
        {"Table": "mf_scheme_metrics", "PK": "scheme_code",
         "Holds": "31 pre-computed risk/return metrics per scheme. Backed by services.mf_metrics.recompute_metrics; auto-recomputes after every NAV save.",
         "FK targets": "amfi_schemes.scheme_code"},
        {"Table": "mf_registry", "PK": "scheme_code",
         "Holds": "Tracked-funds list with per-source nav/holdings/metadata status. Keyed on scheme_code with FK to amfi_schemes.",
         "FK targets": "amfi_schemes.scheme_code"},
        # ----- Holdings family (still slug-keyed, Phase 3 will FK) -----
        {"Table": "mf_holdings, mf_sector_allocation, mf_asset_allocation", "PK": "id (auto)",
         "Holds": "Per-fund holdings + sector + asset weights from AdvisorKhoj. Currently keyed on scheme_slug; Phase 3 switches to scheme_code FK.",
         "FK targets": "(planned) amfi_schemes.scheme_code"},
        # ----- Tradebook -----
        {"Table": "mf_tradebook", "PK": "trade_id (str)",
         "Holds": "Kite/Zerodha trade rows. Resolution to scheme_name happens live via mf_tradebook.isin = amfi_schemes.isin_growth.",
         "FK targets": "(planned) amfi_schemes.scheme_code"},
        # ----- Stock side -----
        {"Table": "stock_ohlcv", "PK": "(symbol, date)",
         "Holds": "Daily OHLCV bars for Indian stocks + benchmark indices (^NSEI etc).",
         "FK targets": "—"},
    ]
)


def render() -> None:
    st.divider()
    st.subheader("Data Sources")
    st.caption(
        "Where each kind of data comes from, what input is required to fetch it, and where it lands."
    )
    st.dataframe(_DATA_SOURCES, use_container_width=True, hide_index=True)

    st.markdown("**Schema map** — every MF table now FKs through `amfi_schemes.scheme_code`.")
    st.dataframe(_SCHEMA_TABLES, use_container_width=True, hide_index=True)

    with st.expander("How fetches chain together"):
        st.markdown(
            """
**Adding a fund** (Screener → *Fetch for top N filtered funds*) calls
`services.registry_service.backfill_missing(scheme_names=...)`, which:

1. Resolves each name → `scheme_code` against `amfi_schemes`. If no exact match, mints a
   synthetic negative code (rare — 75 of them today, all funds genuinely missing from AMFI master).
2. Inserts `mf_registry` row with status `pending` for every source.
3. Fans out NAV (MFAPI) + metadata (AdvisorKhoj) in parallel — 8 workers, 50ms submit delay.
4. NAV save triggers `recompute_metrics` for the affected schemes → fills `mf_scheme_metrics`.
5. Per-source statuses (`available` / `unavailable`) flip on the `mf_registry` row as each fetch resolves.

Holdings are excluded from the default backfill (heavier scrape, separate ceiling); use
**Settings → Update All Holdings** when ready.

**Tradebook upload** writes raw rows to **mf_tradebook**. Resolution to scheme names happens
live in memory via `mf_tradebook.isin = amfi_schemes.isin_growth` — no `fund_mapping` table.

**Phase 1 + Phase 2 normalisation** dropped ~340 MB:

- `fund_house` and `category` text columns extracted into `mf_amc` and `mf_category` dim tables
  (15.8 MB freed on `amfi_schemes`).
- `scheme_name` dropped from `mf_nav` / `mf_metadata` / `mf_scheme_metrics` / `mf_registry`;
  every MF table FKs into `amfi_schemes.scheme_code` instead (336 MB freed on `mf_nav` alone).
- `scheme_code_map` dropped — every name→code lookup goes through `amfi_schemes` directly.

**Synthetic negative codes**: ~141 minted during the Phase 2 backfill for funds whose
`scheme_name` didn't exact-match `amfi_schemes` (mostly case drift: `Bharat 22 ETF` vs
`BHARAT 22 ETF`). Run `uv run python scripts/dedupe_synthetic_codes.py --apply` to merge
the case-mismatch ones (`LOWER(name) = LOWER(name)`) into their real AMFI codes; the
remaining ~75 are funds genuinely absent from AMFI master.

**The glue**: every external system reaches every other through
`amfi_schemes.scheme_code ⇄ scheme_name ⇄ isin_growth`.
            """
        )
