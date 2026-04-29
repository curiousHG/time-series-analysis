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
uv run python scripts/migrate_parquet_to_db.py

# Sync AMFI master data (14K+ mutual fund schemes with ISIN codes)
# Also available via "Sync AMFI Master" button in Data Manager UI
uv run python -c "from data.repositories.amfi import sync_amfi_master; sync_amfi_master()"
```

## Architecture

**Trading agent platform** for Indian markets (NSE) — Streamlit UI with strategy backtesting, portfolio analytics, and a bot framework for automated trading.

### Pages

1. **Portfolio** — fund allocation, P&L, growth vs Nifty/FD, drawdown, risk metrics (quantstats), fund returns
2. **Mutual Fund Analysis** — overlap heatmap, sector exposure, return distributions, holdings treemaps, correlation
3. **Stock Analysis** — TradingView candlestick charts, 37 TA-Lib indicators (overlays + panels), strategy backtesting
4. **Data Manager** — AMFI sync, tradebook CSV upload, NAV/holdings data updates

### Layer Structure

```
main.py → ui/app.py (multi-page router, init_schema, setup_logging)
              ↓
         ui/views/
           portfolio.py                # Portfolio page entry point
           mutual_fund.py              # MF analysis page with tabs
           stock_analysis.py           # Stock analysis page (chart + backtest)
           data_manager.py             # Data management page
         ui/views/portfolio_tabs/      # Portfolio sub-tabs
           helpers.py                  # Re-exports from services/portfolio_service
           allocation.py, growth.py, drawdown.py, risk_metrics.py, fund_returns.py
         ui/views/mf_tabs/            # MF analysis sub-tabs
           portfolio.py, overlap.py, returns.py, holdings.py, correlation.py
         ui/views/stock_tabs/          # Stock analysis sub-tabs
           chart.py                    # TradingView candlestick + indicators
           strategy_backtest.py        # Strategy runner UI (delegates to services)
         ui/components/                # Reusable sidebar widgets
         ui/charts/                    # Plotly chart builders + dark theme
         ui/state/loaders.py           # @st.cache_data wrapped data loaders
         ui/persistence/selections.py  # File-based state (data/user/selections.json)
              ↓
         services/                     # Business logic layer (no Streamlit imports)
           backtest_service.py         # run_backtest(), compute_metrics()
           portfolio_service.py        # get_mapped_data(), build_portfolio_value_series()
              ↓
         core/
           database.py                 # SQLModel engine + Session factory
           models/                     # SQLModel ORM models (split by domain)
             mutual_fund.py            # MfNav, MfHolding, MfRegistry, etc.
             stock.py                  # StockOhlcv, StockRegistry
             trading.py                # Bot, Trade, Order (bot framework)
           config.py                   # Pydantic Settings (AppConfig)
           enums.py                    # BotState, RunMode, OrderSide, OrderStatus, etc.
           exceptions.py               # TradingError, DataFetchError, ExchangeError
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
           registry.py                 # MfRegistry + SchemeCodeMap
           fund_mapping.py             # FundMapping CRUD + auto-map
           amfi.py                     # AMFI master sync + ISIN lookup
           stock.py                    # Stock OHLCV CRUD + smart caching
           tradebook.py                # Tradebook import with dedup
              ↓
         exchange/                     # Exchange abstraction (for bot framework)
           base.py                     # ExchangeBase ABC
           paper.py                    # PaperExchange (simulated fills)
         bot/                          # Trading bot engine
           worker.py                   # Worker (state machine: STOPPED/RUNNING/PAUSED)
           bot.py                      # TradingBot (strategy + exchange + data)
           data_provider.py            # Unified data interface (backtest & live)
```

### Database (PostgreSQL)

All data is stored in PostgreSQL via SQLModel ORM. Models in `core/models/`:

| Table | Purpose | Key |
|-------|---------|-----|
| `mf_nav` | Daily NAV per scheme | (date, scheme_name) |
| `mf_holdings` | Fund portfolio holdings | scheme_slug |
| `mf_sector_allocation` | Sector weights | scheme_slug |
| `mf_asset_allocation` | Asset class weights | scheme_slug |
| `mf_registry` | Known scheme names + slugs | scheme_name |
| `amfi_schemes` | AMFI master (14K schemes with ISIN) | scheme_code |
| `scheme_code_map` | scheme_name → MFAPI code cache | scheme_name |
| `stock_ohlcv` | Daily OHLCV data | (date, symbol) |
| `stock_registry` | Stock metadata | symbol |
| `mf_tradebook` | Kite/Zerodha trades (deduped by trade_id) | trade_id |
| `fund_mapping` | trade_symbol → scheme_name | trade_symbol |
| `bots` | Bot instances (name, strategy, state) | id |
| `trades` | Bot trades (entry/exit, P&L) | id |
| `orders` | Bot orders (side, price, status) | id |

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
- **Bot framework** — Worker state machine, exchange abstraction, data provider (inspired by freqtrade)
- **Ruff** for both linting and formatting (config in `pyproject.toml`)
- **File-based selections** (`data/user/selections.json`) for UI state persistence
- **Rotating logs** — `logs/app.log`, `logs/data.log`, `logs/ui.log` (5MB, 3 backups)

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
| `pydantic-settings` | App configuration |

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
