"""Stock/index OHLCV repository — facade re-exporting the focused modules this was split into:

- `_ohlcv_io`        — low-level table upsert / load / date-range (model-agnostic) + negative cache
- `ohlcv_watermark`  — availability state machine (status/floor/streak) + shared DB-first gap-fetch
- `index_ohlcv`      — the index_ohlcv table (bhavcopy / niftyindices / yfinance, incl. valuation)
- `stock_ohlcv`      — the stock_ohlcv table (NSE equities; delegates index symbols to index_ohlcv)

Kept as a facade so existing `from data.repositories.stock import …` call sites keep working. New
code may import from the focused modules directly.
"""

from data.repositories.index_ohlcv import (
    ensure_index_data,
    first_index_bhavcopy_date,
    last_index_bhavcopy_date,
    latest_index_valuation,
    list_bhavcopy_index_names,
    list_index_symbols,
    read_index_ohlcv,
    refresh_index_to_today,
    save_index_bhavcopy_day,
)
from data.repositories.ohlcv_watermark import (
    clear_stock_ohlcv_status,
    get_stock_ohlcv_statuses,
    list_unavailable_stock_symbols,
)
from data.repositories.stock_ohlcv import (
    ensure_stock_data,
    fill_stock_gaps,
    last_nse_stock_ohlcv_date,
    last_stock_ohlcv_date,
    list_registry_symbols,
    list_stock_symbols,
    load_registry_catalog,
    load_stock_registry,
    refetch_stock_full,
    refresh_stock_to_today,
    refresh_stocks_batch,
    register_stock,
    search_stock_symbols,
    stock_gap_ranges,
    sync_nse_etf_universe,
    sync_nse_universe,
)

__all__ = [
    "clear_stock_ohlcv_status",
    "ensure_index_data",
    "ensure_stock_data",
    "fill_stock_gaps",
    "first_index_bhavcopy_date",
    "get_stock_ohlcv_statuses",
    "last_index_bhavcopy_date",
    "last_nse_stock_ohlcv_date",
    "last_stock_ohlcv_date",
    "latest_index_valuation",
    "list_bhavcopy_index_names",
    "list_index_symbols",
    "list_registry_symbols",
    "list_stock_symbols",
    "list_unavailable_stock_symbols",
    "load_registry_catalog",
    "load_stock_registry",
    "read_index_ohlcv",
    "refetch_stock_full",
    "refresh_index_to_today",
    "refresh_stock_to_today",
    "refresh_stocks_batch",
    "register_stock",
    "save_index_bhavcopy_day",
    "search_stock_symbols",
    "stock_gap_ranges",
    "sync_nse_etf_universe",
    "sync_nse_universe",
]
