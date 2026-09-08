"""Datatype → data-source routing registry — the single documented truth for WHICH external source
serves WHICH kind of data, and why. Established by live-probing every source (July 2026); the
Settings → Reference table renders straight from this so the docs can't drift from the code.

Routing principles:
- One PRICE BASIS per table: equities store yfinance auto-adjusted OHLCV only. Mixing raw daily
  appends (bhavcopy) onto adjusted history breaks the series at every split/dividend.
- The NSE index bhavcopy is kept for indices ONLY — it's the sole source covering all 160+ indices
  (incl. factor/strategy) plus valuation (P/E·P/B·Div-Yield), and index LEVELS need no adjustment.
- Fundamentals follow the listing market: screener.in for NSE (far richer for India), yfinance
  .info for international. CAPM benchmarks likewise: ^NSEI vs ^GSPC.

Retired sources (do not reintroduce):
- niftyindices.com — endpoint died ~2026-07-02; froze every series it fed. Bhavcopy names replace it.
- jugaad per-symbol quotes — IST-midnight timestamps shifted dates (+1); the original corruption vector.
- NSE stock bhavcopy daily append — raw basis onto adjusted history (see principle 1).
"""

from __future__ import annotations

# Rendered by ui/constants.py into the Settings → Reference table. Keys: what/source/fallback/
# returns/lands_in/why. Keep entries one-line-readable; details live in the owning module.
SOURCE_ROUTING: tuple[dict[str, str], ...] = (
    # ---------------- stocks ----------------
    {
        "what": "Equity OHLCV — daily refresh (NSE + international)",
        "source": "yfinance (batched yf.download, auto-adjusted)",
        "fallback": "—",
        "returns": "Trailing ~7d OHLCV for every tracked symbol in a few chunked calls",
        "lands_in": "stock_ohlcv",
        "why": "Same adjusted basis as stored history — daily rows can never drift after splits/dividends",
    },
    {
        "what": "Equity OHLCV — history / on-demand / full re-fetch",
        "source": "yfinance (per-symbol, auto-adjusted)",
        "fallback": "—",
        "returns": "Full daily history since 2000 (zero-close rows dropped, one-day glitches despiked)",
        "lands_in": "stock_ohlcv",
        "why": "Split/dividend-adjusted + correctly dated; registry exchange decides .NS vs as-is symbol",
    },
    {
        "what": "NSE index OHLCV + valuation",
        "source": "NSE index bhavcopy (ind_close_all via jugaad)",
        "fallback": "—",
        "returns": "160+ indices/day incl. factor/strategy + P/E, P/B, Div Yield, turnover",
        "lands_in": "index_ohlcv",
        "why": "Only source with the full index set + valuation; index levels need no adjustment",
    },
    {
        "what": "Foreign index / FX series",
        "source": "yfinance (^GSPC, ^NDX, INR=X, URTH…)",
        "fallback": "—",
        "returns": "Daily OHLC (on-demand, gap-fetched)",
        "lands_in": "index_ohlcv",
        "why": "Literal-symbol route; NSE-name indices are bulk-maintained and never fetched per-name",
    },
    {
        "what": "Index constituents",
        "source": "NSE ind_<name>list.csv",
        "fallback": "screener.in company page",
        "returns": "Official constituent symbol list (Midcap 150 → 150 names)",
        "lands_in": "(query-time, 24h cache)",
        "why": "NSE publishes complete lists for broad/sectoral; screener covers sectoral + Nifty 50",
    },
    {
        "what": "ETF classification + live metadata",
        "source": "NSE /api/etf",
        "fallback": "—",
        "returns": "All 328 NSE ETFs: underlying, NAV, price, 52w range, 30d/1Y perf",
        "lands_in": "stock_registry.quote_type + (query-time, 1h cache)",
        "why": "Authoritative one-call ETF universe — drives the 🧺 badge and NAV/premium header",
    },
    {
        "what": "NSE equity fundamentals",
        "source": "screener.in (consolidated → standalone fallback)",
        "fallback": "—",
        "returns": "Ratios, quarterly P&L, shareholding (promoter holding)",
        "lands_in": "stock_metrics, stock_quarterly",
        "why": "Far richer than yfinance for Indian companies; blank-page guard protects good rows",
    },
    {
        "what": "International equity fundamentals",
        "source": "yfinance .info + quarterly_income_stmt",
        "fallback": "—",
        "returns": "Valuation/quality ratios in listing currency + quarterly revenue/profit/EPS",
        "lands_in": "(query-time, 24h cache)",
        "why": "screener.in is India-only; not persisted into the INR-based screener tables",
    },
    {
        "what": "Global ticker search (add-international flow)",
        "source": "yfinance Search",
        "fallback": "yfinance Lookup",
        "returns": "(symbol, name, exchange, quote type) across world exchanges",
        "lands_in": "stock_registry (on add)",
        "why": "Registers exchange so OHLCV/fundamentals/benchmark all route correctly afterwards",
    },
    {
        "what": "CAPM benchmark series",
        "source": "^NSEI for NSE listings · ^GSPC for international",
        "fallback": "—",
        "returns": "Benchmark daily returns for alpha/beta/R²",
        "lands_in": "stock_metrics (alpha_1y, beta_1y, r2_1y)",
        "why": "A US stock's alpha vs an INR index mixes currencies and market regimes",
    },
    {
        "what": "NSE equity master",
        "source": "NSE EQUITY_L.csv",
        "fallback": "—",
        "returns": "Every listed symbol + name + ISIN + series (~2.4K)",
        "lands_in": "stock_registry",
        "why": "The pickable universe; rows that leave the master (delistings) keep history, skip refresh",
    },
    # ---------------- mutual funds ----------------
    {
        "what": "MF NAV history",
        "source": "MFAPI (api.mfapi.in)",
        "fallback": "—",
        "returns": "Full NAV time series for one scheme code",
        "lands_in": "mf_nav",
        "why": "Free, complete history; incremental saves filter to date > last stored",
    },
    {
        "what": "MF master + latest NAV",
        "source": "AMFI NAVAll.txt",
        "fallback": "—",
        "returns": "All ~14K schemes: code, ISINs, latest NAV, AMC, category",
        "lands_in": "amfi_schemes (+ mf_amc, mf_category)",
        "why": "The canonical scheme dimension every MF table FKs through",
    },
    {
        "what": "MF holdings / sector / asset allocation",
        "source": "AdvisorKhoj portfolio page",
        "fallback": "—",
        "returns": "Instrument-level holdings with weights/ISINs + sector & asset splits",
        "lands_in": "mf_holdings, mf_sector_allocation, mf_asset_allocation",
        "why": "Only free instrument-level source; arbitrage funds legitimately sum ≠ 100% (short legs)",
    },
    {
        "what": "MF metadata (AUM/TER/benchmark/manager)",
        "source": "AdvisorKhoj overview page",
        "fallback": "Kuvera",
        "returns": "AUM, TER, benchmark name, managers, exit load, launch date",
        "lands_in": "mf_metadata",
        "why": "Kuvera fills AdvisorKhoj 404s/slug misses",
    },
    {
        "what": "Trades",
        "source": "Kite/Zerodha tradebook CSV (upload)",
        "fallback": "—",
        "returns": "One row per trade (trade_id, ISIN, qty, price)",
        "lands_in": "mf_tradebook",
        "why": "Deduped by trade_id; ISIN → scheme via amfi_schemes",
    },
    # ---------------- derived ----------------
    {
        "what": "MF risk/return metrics (derived)",
        "source": "services.mf_metrics.recompute_metrics",
        "fallback": "—",
        "returns": "31 cached metrics/scheme (CAGR, Sharpe, VaR, rolling, alpha/beta vs category benchmark)",
        "lands_in": "mf_scheme_metrics",
        "why": "Precomputed so the screener filters without touching 7.8M NAV rows",
    },
    {
        "what": "Stock price metrics (derived)",
        "source": "services.stock_metrics.recompute_price_metrics",
        "fallback": "—",
        "returns": "1Y return/vol + CAPM alpha/beta/R² vs the exchange-routed benchmark",
        "lands_in": "stock_metrics",
        "why": "Powers the alpha-vs-beta screener scatter",
    },
)
