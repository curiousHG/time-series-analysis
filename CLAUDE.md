# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
streamlit run main.py

# Install dependencies (uses UV)
uv sync
uv pip install -e .

# Lint and format (ruff handles both)
uv run ruff check . --exclude notebooks/
uv run ruff check --fix . --exclude notebooks/
uv run ruff format . --exclude notebooks/

# Tests
uv run pytest

# Database setup
createdb trading

# Schema migrations — Alembic owns all schema deltas. Run manually after pulling model
# changes (kept off Streamlit boot: ALTER COLUMN TYPE / GIN index builds can be slow).
# Idempotent — safe to re-run; on an existing pre-Alembic DB just run upgrade head.
uv run alembic upgrade head

# Sync AMFI master data (14K+ mutual fund schemes with ISIN codes)
# Also available via "Sync AMFI Master" button in Settings
uv run python -c "from data.repositories.amfi import sync_amfi_master; sync_amfi_master()"
```

## Architecture

**Trading analysis platform** for Indian markets (NSE) — Streamlit UI with strategy backtesting, portfolio analytics, and mutual-fund/stock research.

### Pages

1. **Overview** (default) — market pulse, portfolio snapshot (XIRR/TWR), automated alerts (services/insights_service.py), fund movers
2. **Portfolio** — PM cockpit tabs: Overview (positions + contribution), Positions (look-through exposure, overlap, holdings), Risk (quantstats, drawdown, risk/return, correlation), Flows
3. **Mutual Fund Analysis** — single-fund deep dive tabs: Performance (incl. rolling consistency), vs Benchmark (excess return, rolling alpha, alerts), Risk, Holdings (incl. portfolio overlap), Calendar, About
4. **MF Screener** — AMFI universe filters, risk/return metrics, bulk fetch for tracked funds
5. **Stock Analysis** — TradingView candlestick charts, 37 TA-Lib indicators, Fundamentals (momentum + screener.in percentiles + quarterly trend), strategy backtesting
6. **Stock Screener** — screener.in fundamentals + CAPM alpha/beta categorisation, AgGrid table
7. **Settings** — AMFI sync, tradebook CSV upload, NAV/holdings refresh, metrics cache, DB stats

### Layer Structure

```
main.py → ui/app.py (multi-page router, init_schema, setup_logging)
              ↓
         ui/views/
           overview/                   # Landing desk: snapshot, market pulse, alerts, movers
           portfolio/page.py           # Portfolio cockpit entry (tabs: overview/positions/risk/flows)
           portfolio/                  # Tab modules composing section renderers (allocation, growth, …)
           mutual_fund/page.py         # Single-fund deep dive (slim orchestrator over FundContext)
           mutual_fund/                # selector + per-tab modules (performance, benchmark, risk, …)
           mf_screener/page.py         # AMFI universe screener
           stock_analysis/page.py      # Stock page entry point
           stock_analysis/             # Stock chart + fundamentals + strategy backtest
           stock_screener/             # screener.in fundamentals + alpha/beta screener
           settings/page.py            # Data/source/settings page entry point
           settings/                   # AMFI, tradebook, refresh, metrics cache, DB stats
         ui/components/                # Reusable sidebar widgets
         ui/charts/                    # Plotly chart builders + dark theme
         ui/state/loaders.py           # @st.cache_data wrapped data loaders
         ui/persistence/selections.py  # File-based state (data/user/selections.json)
              ↓
         services/                     # Business logic layer (no Streamlit imports)
           backtest_service.py         # run_backtest(), compute_metrics()
           portfolio_service.py        # get_mapped_data(), build_portfolio_value_series()
           registry_service.py         # tracked-fund registry + source statuses
           sync_service.py             # safe data refresh orchestration
           screener_service.py         # MF screener DataFrame assembly + filters
           mf_metrics.py               # NAV-derived risk/return metrics
              ↓
         core/
           database.py                 # SQLModel engine + Session factory
           models/                     # SQLModel ORM models (split by domain)
             mutual_fund.py            # MfNav, MfHolding, MfRegistry, etc.
             stock.py                  # StockOhlcv, StockRegistry
           logging_config.py           # Rotating file log setup (logs/)
         indicators/                   # 37 TA-Lib indicators (split package)
           registry.py                 # @register decorator, INDICATOR_REGISTRY, compute_indicators
           overlays.py                 # 15 overlay indicators (SMA, EMA, BB, SAR, etc.)
           panels.py                   # 22 panel indicators (RSI, MACD, ATR, etc.)
         strategies/                   # Trading strategy framework
           base.py                     # Strategy ABC (indicators, signals, stoploss)
           rsi.py, macd.py, bollinger.py, sma_crossover.py
         mutual_funds/                 # MF domain logic
           analytics.py, tradebook.py, holdings.py, table_schema.py
              ↓
         data/fetchers/                # External API clients (HTTP only, no DB)
           mutual_fund.py              # MFAPI, AdvisorKhoj, AMFI
           stock.py                    # yfinance + jugaad-data (NSE fallback)
         data/repositories/            # DB CRUD (one file per aggregate)
           nav.py                      # NAV load/save/upsert/fetch
           holdings.py                 # Holdings/sectors/assets CRUD
           metadata.py                 # AdvisorKhoj metadata CRUD
           scheme_metrics.py           # cached MF metrics CRUD
           screener.py                 # DB view for screener assembly
           amfi.py                     # AMFI master sync + ISIN lookup
           stock.py                    # Stock OHLCV CRUD + smart caching
           tradebook.py                # Tradebook import with dedup
```

### Database (PostgreSQL)

All data is stored in PostgreSQL via SQLModel ORM. Models in `core/models/`:

| Table | Purpose | Key |
|-------|---------|-----|
| `mf_nav` | Daily NAV per scheme | (scheme_code, date) |
| `mf_holdings` | Fund portfolio holdings | id, scheme_code FK |
| `mf_sector_allocation` | Sector weights | id, scheme_code FK |
| `mf_asset_allocation` | Asset class weights | id, scheme_code FK |
| `mf_registry` | Tracked funds + source statuses | scheme_code |
| `mf_metadata` | AdvisorKhoj/Kuvera fund metadata | scheme_code |
| `mf_scheme_metrics` | Cached NAV-derived risk/return metrics | scheme_code |
| `mf_amc` | AMC dimension | id (name unique) |
| `mf_category` | Fund category dimension | id (name unique) |
| `amfi_schemes` | AMFI master (14K schemes with ISIN) | scheme_code |
| `stock_ohlcv` | Daily equity OHLCV (bare symbols) | (date, symbol) |
| `stock_registry` | Stock metadata + OHLCV/fundamentals watermarks | symbol |
| `stock_metrics` | screener.in fundamentals + CAPM alpha/beta snapshot | symbol |
| `stock_quarterly` | Quarterly P&L per stock | (symbol, period_end) |
| `index_ohlcv` | Index/FX OHLCV (NSE bhavcopy names + `^` for foreign indices) | (date, symbol) |
| `index_registry` | Index registry + backfill floor | symbol |
| `mf_tradebook` | Kite/Zerodha trades (deduped by trade_id) | trade_id |

Legacy tables `stock_ohlcv_status`, `scheme_code_map`, `bots`, `orders`, `trades` were dropped
(unused/superseded — status now lives in `stock_registry.ohlcv_*` columns).

Connection: `DATABASE_URL` env var (default: `postgresql://harshit@localhost:5432/trading`).

### Data Sources

| Source | What | Used For |
|--------|------|----------|
| **MFAPI** (`api.mfapi.in`) | Historical NAV by scheme code | Primary NAV source |
| **AMFI** (`amfiindia.com/spages/NAVAll.txt`) | Master scheme list with ISIN codes | ISIN → scheme_code lookup, auto-mapping |
| **AdvisorKhoj** | Portfolio holdings, sector/asset allocation | Holdings data (scraping) |
| **yfinance** | Global OHLCV data | Stock data, Nifty 50 benchmark |
| **jugaad-data** | NSE bhavcopy | Indian stock fallback (tries before yfinance for .NS) |

### Key Design Decisions

- **Polars** for data processing, **Pandas** where required (TA-Lib, Plotly, quantstats)
- **PostgreSQL** via **SQLModel** (Pydantic + SQLAlchemy)
- **Services layer** — business logic separated from UI; callable by bot, CLI, and Streamlit
- **Repository pattern** — `data/repositories/` for DB CRUD, `data/fetchers/` for HTTP
- **Strategy registry** — `@register_strategy` decorator, `STRATEGY_REGISTRY` dict
- **Indicator registry** — `@register` decorator in `indicators/`, UI reads from `INDICATOR_REGISTRY`
- **Ruff** for both linting and formatting (config in `pyproject.toml`)
- **File-based selections** (`data/user/selections.json`) for UI state persistence
- **Rotating logs** — `logs/app.log`, `logs/data.log`, `logs/ui.log` (5MB, 3 backups)

### Data fetching policy (DB-first, fetch only the gap)

**Rule**: external data is fetched only for the range or keys that are NOT already in the database. Always check the DB first, compute what's missing, fetch only the missing part, save it back, then return the result from the DB.

This is the project's hard rule for any new data-source integration. Examples already following it:
- `data/repositories/stock.py:ensure_stock_data` — looks up `(min, max)` date range per symbol in `stock_ohlcv`; fetches only forward and backward gaps. Skips fetches under `MIN_FETCH_DAYS=5` to avoid weekend/holiday noise.
- `data/repositories/nav.py:ensure_nav_data` — fetches only schemes not already present in `mf_nav`.
- `data/repositories/holdings.py:ensure_holdings_data` — fetches only slugs not already in `mf_holdings`.

**When adding a new data source**:
1. Define the DB table and its keys (date+symbol, scheme_name, slug, ISIN, etc.).
2. Write `load_*` and `save_*` repo functions first.
3. Write `ensure_*(keys, [start, end])` that: (a) queries DB for what's already cached, (b) computes the missing keys/range, (c) calls the fetcher only for that subset, (d) saves the result, (e) returns the loaded DB rows.
4. The Streamlit/UI layer always calls `ensure_*` — never the fetcher directly.

**Anti-patterns to avoid**:
- Calling a fetcher unconditionally and relying on `@st.cache_data` TTL alone — that re-downloads after cache expiry even when the data hasn't moved.
- Fetching the full history when only the tail is needed — see `data_manager.py` NAV update which now filters fetched rows to `date > last_known` before saving.
- Adding a new "refresh" button that wipes-and-refills — prefer incremental upsert via `ON CONFLICT DO UPDATE`.

### Package Structure

Project is installed in editable mode (`uv pip install -e .`). All packages have `__init__.py` files. Run scripts with `uv run python scripts/foo.py`.

### Key Dependencies

| Package | Purpose |
|---------|---------|
| `streamlit` | Web framework |
| `polars` / `pandas` | DataFrames |
| `plotly` | Charts (MF analytics, portfolio) |
| `streamlit-lightweight-charts` | TradingView candlestick charts |
| `sqlmodel` | ORM (SQLAlchemy + Pydantic) |
| `psycopg2-binary` | PostgreSQL driver |
| `ta-lib` | 37 technical indicators (C-based) |
| `quantstats` | Portfolio risk metrics (Sharpe, Sortino, CAGR, etc.) |
| `vectorbt` | Strategy backtesting |
| `yfinance` | Stock data |
| `jugaad-data` | NSE Indian stock data |
| `httpx` | HTTP client (API calls) |
| `beautifulsoup4` | HTML scraping (AdvisorKhoj) |

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)

## Cursor and agents

- **AGENTS.md** — short task-routing and “definition of done” for coding agents (verification, layer boundaries).
- **`.cursor/rules/*.mdc`** — Cursor project rules (always-on + file-scoped). **`.cursorignore`** trims index noise (venv, notebooks, logs, etc.).
