"""Populate stock fundamentals (screener.in) + price metrics (CAPM) for a set of symbols.

Off-boot orchestration for the Settings / screener "populate" action. Scraping is polite by
construction — DB-first `ensure_stock_fundamentals` skips already-fresh symbols.
"""

from __future__ import annotations

import logging

from data.repositories.stock_fundamentals import ensure_stock_fundamentals
from services.stock_metrics import recompute_price_metrics

logger = logging.getLogger("services.stock_sync")


def sync_stocks(symbols: list[str], *, scrape_fundamentals: bool = True) -> int:
    """Scrape fundamentals (optional) then compute price metrics. Returns #price-metric rows."""
    if scrape_fundamentals:
        ensure_stock_fundamentals(symbols)
    n = recompute_price_metrics(symbols)
    logger.info("synced %d stocks (fundamentals=%s)", len(symbols), scrape_fundamentals)
    return n
