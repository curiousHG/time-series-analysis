"""Populate stock fundamentals (screener.in) + price metrics (CAPM) for a set of symbols.

Off-boot orchestration for the Settings / screener "populate" action. Scraping is polite by
construction — DB-first `ensure_stock_fundamentals` skips already-fresh symbols.
"""

from __future__ import annotations

import logging

from data.repositories.stock_fundamentals import ensure_stock_fundamentals
from services.stock_metrics import recompute_price_metrics

logger = logging.getLogger(__name__)


def sync_stocks(symbols: list[str], *, scrape_fundamentals: bool = True) -> int:
    """Scrape fundamentals (optional) then compute price metrics. Returns #price-metric rows."""
    if scrape_fundamentals:
        ensure_stock_fundamentals(symbols)
    n = recompute_price_metrics(symbols)
    logger.info("synced %d stocks (fundamentals=%s)", len(symbols), scrape_fundamentals)
    return n


def list_unavailable_stocks() -> list[str]:
    """Bare symbols whose price history was confirmed unavailable (for the Settings retry list)."""
    from data.repositories.stock import list_unavailable_stock_symbols  # noqa: PLC0415 — defer off boot

    return list_unavailable_stock_symbols()


def retry_stock_ohlcv(symbol: str) -> str:
    """Clear a symbol's unavailable marker and force a fresh full-history fetch. Returns the
    resulting ohlcv_status ('available' if data was recovered, else 'pending'/'unavailable')."""
    import datetime as _dt  # noqa: PLC0415

    from data.repositories.stock import (  # noqa: PLC0415 — defer heavy import off boot
        clear_stock_ohlcv_status,
        ensure_stock_data,
        get_stock_ohlcv_statuses,
    )

    clear_stock_ohlcv_status(symbol)
    ensure_stock_data(symbol, _dt.date(2000, 1, 1), _dt.date.today())
    return get_stock_ohlcv_statuses([symbol]).get(symbol.removesuffix(".NS"), "pending")
