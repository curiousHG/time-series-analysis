# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
streamlit run main.py

# Install dependencies (uses UV)
uv sync
uv pip install -e .

# Lint and format
uv run ruff check . --exclude notebooks/
uv run ruff check --fix . --exclude notebooks/
uv run black . --exclude notebooks/

# Tests
uv run pytest

# Database setup
createdb trading
uv run python scripts/migrate_parquet_to_db.py

# Sync AMFI master data (14K+ mutual fund schemes with ISIN codes)
# Also available via "Sync AMFI Master" button in Data Manager UI
uv run python -c "from data.store.amfi import sync_amfi_master; sync_amfi_master()"
```

## Architecture

**Streamlit-based financial analysis dashboard** for Indian markets (NSE) with four pages:

1. **Portfolio** — fund allocation, P&L, growth vs Nifty/FD, drawdown, risk metrics (quantstats), fund returns
2. **Mutual Fund Analysis** — overlap heatmap, sector exposure, return distributions, holdings treemaps, correlation
3. **Stock Analysis** — TradingView candlestick charts, 37 TA-Lib indicators (overlays + panels)
4. **Data Manager** — AMFI sync, tradebook CSV upload, NAV/holdings data updates

### Layer Structure

```
main.py → ui/app.py (multi-page router, init_schema, setup_logging)
              ↓
         ui/views/
           portfolio.py              # Portfolio page entry point
           mutual_fund.py            # MF analysis page with tabs
           backtest.py               # Stock analysis page
           data_manager.py           # Data management page
         ui/views/portfolio_tabs/    # Portfolio sub-tabs
           helpers.py                # Shared: get_mapped_data, build_portfolio_value_series
           allocation.py             # Holdings table, pie chart, P&L bar
           growth.py                 # Invested over time, portfolio vs Nifty vs FD
           drawdown.py               # Drawdown chart, underwater chart
           risk_metrics.py           # quantstats-powered metrics (TWR-adjusted)
           fund_returns.py           # Per-fund NAV growth, monthly heatmap
         ui/views/mf_tabs/          # MF analysis sub-tabs
           portfolio.py              # Orchestrator for portfolio_tabs
           overlap.py                # Overlap heatmap + sector stack
           returns.py                # KDE distributions + rolling returns
           holdings.py               # Treemap + donut charts per fund
           correlation.py            # Correlation heatmap
         ui/components/              # Reusable sidebar widgets
           fund_picker.py            # MF fund selector (decoupled state pattern)
           stock_picker.py           # Stock selector
           mutual_fund_holdings.py   # Holdings treemap + donut renderer
           mutual_funds_rolling_returns.py
         ui/charts/                  # Plotly chart builders
           theme.py                  # Dark mode Plotly template (auto-registered)
           plotters.py               # Sector stack, overlap heatmap, KDE
           correlation_heatmap.py
           fund_trade_comp.py
           indicator_chart.py
         ui/state/loaders.py         # @st.cache_data wrapped data loaders
         ui/persistence/selections.py # File-based state (data/user/selections.json)
              ↓
         core/
           database.py               # SQLModel engine + Session factory
           models.py                 # SQLModel ORM models (also Pydantic models)
           indicators.py             # 37 TA-Lib indicators with registry pattern
           logging_config.py         # Rotating file log setup (logs/)
         mutual_funds/
           analytics.py              # Overlap, sector exposure, rolling returns
           tradebook.py              # Transaction normalization, fund mapping
           holdings.py               # Holdings normalization from AdvisorKhoj
           table_schema.py           # Polars schemas
           constants.py              # Path constants
         strategies/
           base.py                   # Base strategy class
           rsi.py                    # RSI strategy (vectorbt)
              ↓
         data/fetchers/
           mutual_fund.py            # MFAPI (NAV), AdvisorKhoj (holdings), AMFI master
           stock.py                  # yfinance + jugaad-data (NSE fallback)
         data/store/
           mutual_fund.py            # MF NAV, holdings, registry, fund mapping (PostgreSQL)
           stock.py                  # Stock OHLCV (PostgreSQL)
           amfi.py                   # AMFI master data sync + ISIN lookup
           tradebook.py              # Tradebook import with dedup on trade_id
```

### Database (PostgreSQL)

All data is stored in PostgreSQL via SQLModel ORM. Models in `core/models.py`:

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
- **PostgreSQL** via **SQLModel** (Pydantic + SQLAlchemy) — replaced Parquet files
- **ISIN-based auto-mapping** — tradebook ISINs → AMFI scheme codes → correct NAV data
- **Time-weighted returns (TWR)** — risk metrics subtract cash flows to show pure market performance
- **File-based selections** (`data/user/selections.json`) — replaced flaky browser cookies
- **Decoupled widget state** — `_selected_schemes` (app state) + `_schemes_widget` (widget key) pattern avoids Streamlit session_state conflicts
- **Indicator registry** — `@register` decorator in `core/indicators.py`, UI reads from `INDICATOR_REGISTRY`
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
