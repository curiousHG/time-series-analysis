# Trading & Mutual Fund Analytics Platform

Streamlit-based platform for analysing Indian mutual funds and stocks. Pulls scheme NAVs, portfolio holdings, fund metadata, and stock OHLCV from public sources, persists everything in PostgreSQL, and renders interactive dashboards for screening, deep-dive analysis, portfolio tracking, and strategy backtesting.

## What's in the app

Five pages, accessible from the top navigation:

### 1. Mutual Fund Analysis (default page)
Single-fund deep dive. Pick any tracked fund from a sidebar with filters (AMC, category, plan, option, search-by-name, data-availability toggles).

- **Header card** — AMC, category, AUM, TER, plan (Direct/Regular), option (Growth/IDCW/Bonus/ETF/Other), benchmark, launch date.
- **NAV & Returns tab** — period returns (1M / 3M / 6M / 1Y / 3Y CAGR / 5Y CAGR), rebased growth chart vs Nifty 50 and the fund's own benchmark (when mappable), rolling-returns chart with selectable window (3M–5Y).
- **Risk tab** — 1Y / 3Y Sharpe, Sortino, volatility, max drawdown; Beta / Alpha / R² / tracking error vs the fund's actual benchmark; % from ATH; drawdown chart; daily-return histogram.
- **Holdings tab** — derived stats (% Equity / Debt / Cash, % Largecap / Midcap / Smallcap, top-3 / 5 / 10 concentration, credit-rating breakdown for debt slices) plus existing treemap + sector pie + asset pie.
- **Calendar Returns tab** — monthly heatmap (RdYlGn) and yearly bar chart.
- **About tab** — full metadata dump: scheme code, ISINs, fund manager, exit load, min investment, source URL, etc.

If a selected fund has missing NAV/holdings/metadata, the page auto-fetches with a spinner. Confirmed-unavailable sources show inline warnings rather than retry loops.

### 2. Portfolio
Drop-in Kite/Zerodha tradebook → resolved live against `amfi_schemes.isin_growth` (no mapping table). Sub-tabs:

- **Allocation** — fund-level allocation pie + invested-over-time chart.
- **Growth** — portfolio value vs Nifty vs FD benchmark.
- **Drawdown** — historical drawdown curve.
- **Risk Metrics** — quantstats-derived risk stats using time-weighted returns (cashflow-adjusted).
- **Risk vs Return** — 2D scatter, one bubble per active fund. X = 1Y volatility, Y = 1Y CAGR, bubble size = portfolio allocation %. Quadrant shading + median guides.
- **Fund Returns** — per-fund NAV growth (rebased) + monthly heatmap.
- **Overlap & Allocation** — sector-exposure heatmap and average sector pie across active funds.
- **Holdings** — treemap + sector + asset pie per fund.

### 3. Stock Analysis
TradingView-style candlestick chart with 37 TA-Lib indicators (overlays + panels) and a strategy backtester (RSI, MACD, Bollinger, SMA crossover) powered by vectorbt.

### 4. MF Screener
Browse the AMFI universe (~14,400 schemes). Sidebar filters: search-by-name (pg_trgm fuzzy + tokenised AND substring), AMC, category, plan, option, min AUM, max TER, "only my tracked", "has NAV history" → unlocks risk-metric sliders (1Y CAGR / Sharpe / Max-DD).

The data table includes computed risk metrics (CAGR 1Y/3Y/5Y/10Y, vol, Sharpe, Max DD, % from ATH) and derived holdings stats (% Equity / Debt / Cash / Top-10) for funds with data. Below the table, a throttled bulk-fetch (2 concurrent · 0.4 s submission delay) pulls NAV + metadata for the top-N rows of the current filter.

### 5. Settings
- **Tradebook upload** — Kite/Zerodha CSV with live ISIN-resolution preview.
- **AMFI Master sync** — bulk-downloads `NAVAll.txt` (14K+ schemes with ISIN, AMC, category).
- **Refresh tracked-fund data** — per-tracked-fund NAV / holdings status tables (Fresh / Stale / Missing colour-coded), Update All NAV / Holdings / Everything buttons with live progress bar + counter + rolling 8-line log, retry-unavailable per fund.
- **Data Sources reference** — table documenting every external source, the input it requires, and the DB table it lands in.
- **Database statistics** — total size, table-by-table row + byte counts.

## Architecture at a glance

```
ui/views/                         pages: portfolio.py / mutual_fund.py / stock_analysis.py /
                                  mf_screener.py / settings.py
ui/views/portfolio_tabs/          portfolio sub-tabs (allocation, growth, drawdown, risk_metrics,
                                  risk_vs_return, fund_returns)
ui/views/mf_tabs/                 MF analysis helper tabs (correlation, holdings, overlap, ...)
ui/views/stock_tabs/              stock chart + strategy backtest
ui/components/                    reusable widgets (fund_picker, freshness_banner, ...)
ui/charts/                        plotly chart builders + dark theme
ui/state/loaders.py               @st.cache_data wrappers
                ↓
services/                         business logic — no streamlit imports
  registry_service.py             single source of truth for tracked funds (mf_registry)
                                  · list_tracked / add_funds / backfill_missing /
                                    retry_unavailable / remove_fund
  scheme_lookup.py                tradebook ISIN → scheme_name live join
  mf_metrics.py                   per-fund quantstats: cagr_1y/3y/5y/10y, vol, sharpe, sortino,
                                  max_dd, pct_from_ath, tracking_error
  portfolio_service.py            tradebook → portfolio value series
  data_freshness.py               per-fund NAV / holdings staleness checks
  db_stats.py                     PostgreSQL stats for Settings page
                ↓
core/
  database.py                     SQLModel engine + idempotent migrations
                                  (mf_registry status columns, drop fund_mapping, pg_trgm)
  models/                         SQLModel classes split by domain (mutual_fund, stock, trading)
  config.py                       pydantic settings
  logging_config.py               rotating file logs (logs/app.log, data.log, ui.log)
                                  Idempotent across Streamlit hot reloads via handler markers.
indicators/                       37 TA-Lib indicators (registry + overlays + panels)
strategies/                       backtest strategies (RSI, MACD, Bollinger, SMA crossover)
mutual_funds/
  display.py                      short_scheme_name, make_slug, detect_plan, detect_option
  holdings_stats.py               derivable stats from holdings tables
                                  (asset / market-cap / concentration / credit breakdowns)
  analytics.py                    rolling returns, sector exposure
                ↓
data/fetchers/                    external HTTP only, no DB
  mutual_fund.py                  MFAPI, AdvisorKhoj (overview + portfolio), AMFI master
  stock.py                        yfinance + jugaad-data with fallback
data/repositories/                DB-first; ensures fetch only the gap
  amfi.py                         AMFI sync, ISIN lookup, fuzzy search (pg_trgm)
  nav.py                          NAV load/save/upsert/fetch
  holdings.py                     holdings/sector/asset CRUD
  metadata.py                     fund metadata (AUM, TER, etc.)
  tradebook.py                    Kite CSV import with dedup
  stock.py                        OHLCV smart-cache
```

## Database schema (PostgreSQL)

Connection string: `DATABASE_URL` env var (default `postgresql://harshit@localhost:5432/trading`).

| Table | Purpose | Key |
|---|---|---|
| `amfi_schemes` | AMFI master (~14K schemes with ISIN + AMC + category) | `scheme_code` |
| `mf_registry` | Tracked funds (single source of truth). Status columns per source: `nav_status`, `holdings_status`, `metadata_status` ∈ `{pending, available, unavailable}` | `scheme_name` |
| `mf_nav` | Daily NAV per scheme | `(date, scheme_name)` |
| `mf_metadata` | AUM, TER, benchmark, launch date, exit load, fund manager, etc. | `scheme_name` |
| `mf_holdings` | Per-fund portfolio holdings with weight, ISIN, market-cap bucket, credit rating | `scheme_slug` |
| `mf_sector_allocation` / `mf_asset_allocation` | Aggregated weights by sector / asset class | `scheme_slug` |
| `mf_tradebook` | Kite/Zerodha trades, deduped on `trade_id` | `trade_id` |
| `scheme_code_map` | scheme_name → MFAPI code cache | `scheme_name` |
| `stock_ohlcv` | Daily OHLCV | `(date, symbol)` |
| `stock_registry` | Stock metadata | `symbol` |
| `bots`, `trades`, `orders` | Trading-bot framework (state machine + orders) | `id` |

The `pg_trgm` Postgres extension is enabled on first app start for fuzzy AMFI search.

## Data fetching policy

**DB-first, fetch only the gap.** Every external data source is fetched only for the range or keys that are NOT already in the database. The `data/repositories/*ensure_*` functions follow this pattern: query DB → compute missing → fetch only the subset → save → return from DB.

When adding a fund anywhere in the app (Screener bulk fetch, MF Analysis fund picker, etc.), `services.registry_service.add_funds` upserts the registry row with `pending` statuses, runs NAV + holdings + metadata fetches in parallel, and writes back `available` / `unavailable` per source. Funds confirmed unavailable are not auto-retried — use the per-fund "Retry unavailable" button in Settings.

## Data sources

| Source | What it returns | Input | Lands in |
|---|---|---|---|
| AMFI `NAVAll.txt` | Master scheme list | none (bulk) | `amfi_schemes` |
| MFAPI (`api.mfapi.in/mf/<code>`) | Full historical NAV | `scheme_code` (int) | `mf_nav` |
| AdvisorKhoj — portfolio page | Holdings + sector + asset allocation | scheme slug (computed) | `mf_holdings`, `mf_sector_allocation`, `mf_asset_allocation` |
| AdvisorKhoj — overview page | AUM, TER, benchmark, launch date, exit load, etc. | scheme name | `mf_metadata` |
| AMFI fuzzy search (local DB, pg_trgm) | Trigram-similarity ranked schemes | free text ≥ 2 chars | (query-time only) |
| yfinance | Global OHLCV (incl. `^NSEI`, `^BSESN`, sector/cap indices) | symbol + date range | `stock_ohlcv` |
| jugaad-data (NSE bhavcopy) | Indian-stock OHLCV (tried before yfinance) | symbol + date range | `stock_ohlcv` |
| Kite/Zerodha tradebook CSV | Trade rows | uploaded bytes | `mf_tradebook` |

## Setup

Prerequisites: PostgreSQL ≥ 14, Python 3.11+, [uv](https://github.com/astral-sh/uv).

```bash
# 1. Database
createdb trading
export DATABASE_URL=postgresql://$USER@localhost:5432/trading   # adjust if needed

# 2. Install
uv sync
uv pip install -e .

# 3. Migrate any pre-existing parquet snapshots (idempotent; skipped if files absent)
uv run python scripts/migrate_parquet_to_db.py

# 4. Run
streamlit run main.py
```

On first run the app creates all tables, applies idempotent migrations (incl. `pg_trgm` setup), and renders the navigation. From the **Settings** page, click **Sync AMFI Master** to populate `amfi_schemes`. Then go to **MF Screener**, filter to a fund family of interest, and **Fetch for top N filtered funds** to bring NAV + metadata in.

## Common commands

```bash
# Dev
streamlit run main.py
uv run ruff check . --exclude notebooks/        # lint
uv run ruff format . --exclude notebooks/       # format
uv run pytest                                   # tests

# Data ops (CLI alternatives to Settings buttons)
uv run python -c "from data.repositories.amfi import sync_amfi_master; sync_amfi_master()"

# Logs (rotating, 5MB × 3 backups in logs/)
tail -f logs/app.log
lnav logs/                                      # interactive log viewer (recommended)
```

A custom `lnav` format file lives at `~/.lnav/formats/installed/trading_app_log.json` so the pipe-delimited log columns (timestamp / module / level / body) parse into typed fields, enabling SQL queries inside lnav.

## Key dependencies

| Package | Purpose |
|---|---|
| `streamlit` | Web framework |
| `polars` / `pandas` | DataFrames (polars in repos, pandas where TA-Lib / quantstats / plotly require) |
| `sqlmodel` | ORM (SQLAlchemy + Pydantic) |
| `psycopg2-binary` | PostgreSQL driver |
| `plotly` | Charts |
| `streamlit-lightweight-charts` | TradingView candlesticks |
| `ta-lib` | 37 technical indicators (C-based) |
| `quantstats` | Risk metrics (Sharpe, Sortino, max DD, etc.) |
| `vectorbt` | Strategy backtesting |
| `yfinance` / `jugaad-data` | Stock data |
| `httpx` / `beautifulsoup4` | AdvisorKhoj scraping |
| `scipy` | Hierarchical clustering for correlation tab |
| `pydantic-settings` | Configuration |

## Project conventions

- **Polars in the data layer**, pandas where libraries demand it. Returns from repositories are polars DataFrames; UI converts to pandas only when feeding plotly / quantstats / Streamlit Stylers.
- **Services layer** has no Streamlit imports — callable from CLI, scripts, or future bots.
- **Repository pattern** — `data/repositories/` for DB CRUD, `data/fetchers/` for HTTP.
- **Strategy registry** — `@register_strategy` decorator, `STRATEGY_REGISTRY` dict.
- **Indicator registry** — `@register` in `indicators/`; the UI reads `INDICATOR_REGISTRY`.
- **Ruff** for both lint and format (config in `pyproject.toml`).
- **Selections persistence** — file-based at `data/user/selections.json`.
- **Logs** — three rotating files (`logs/app.log`, `logs/data.log`, `logs/ui.log`); the setup is idempotent across Streamlit hot reloads (handler-marker check), so log lines never duplicate.

## License

MIT.
