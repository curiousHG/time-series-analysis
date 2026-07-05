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


def seed_nifty500(*, force_fundamentals: bool = False) -> int:
    """Populate the Nifty 500 constituents not yet in the universe. DB-first + resumable:
    only symbols without computed price metrics are synced. Fundamentals-off by default (just
    OHLCV + CAPM alpha/beta) so it's fast and polite for a background run. Returns #synced."""
    from data.fetchers.stock import fetch_nifty500_symbols  # noqa: PLC0415 — defer off boot
    from data.repositories.stock_fundamentals import load_stock_metrics  # noqa: PLC0415

    try:
        symbols = fetch_nifty500_symbols()
    except Exception:
        logger.exception("nifty500 constituent list fetch failed")
        return 0
    if not symbols:
        return 0

    have = load_stock_metrics(symbols)
    done = set(have["symbol"].to_list()) if not have.is_empty() and "symbol" in have.columns else set()
    todo = [s for s in symbols if s not in done]
    if not todo:
        logger.info("nifty500 seed: all %d constituents already have metrics", len(symbols))
        return 0

    logger.info("nifty500 seed: syncing %d of %d constituents", len(todo), len(symbols))
    return sync_stocks(todo, scrape_fundamentals=force_fundamentals)


def refresh_stocks_via_bhavcopy(*, max_days: int = 90) -> int:
    """Append every NSE bhavcopy day from the last stored date up to today for tracked stocks
    (weekends/holidays are simply empty). One bulk download per day beats a yfinance call per
    symbol. `max_days` caps a cold-start backfill. Returns rows upserted."""
    import datetime as _dt  # noqa: PLC0415

    from data.repositories.stock import last_stock_ohlcv_date, save_bhavcopy_day  # noqa: PLC0415 — defer off boot

    today = _dt.date.today()
    last = last_stock_ohlcv_date()
    start = max(last + _dt.timedelta(days=1), today - _dt.timedelta(days=max_days)) if last else today - _dt.timedelta(days=max_days)
    total = 0
    day = start
    while day <= today:
        total += save_bhavcopy_day(day, only_existing=True)
        day += _dt.timedelta(days=1)
    logger.info("bhavcopy refresh: %d rows from %s to %s", total, start, today)
    return total


def refresh_indices_via_bhavcopy(*, max_days: int = 120) -> int:
    """Backfill every NSE index bhavcopy day from the last stored index date up to today — 160+
    indices (incl. factor/strategy indices yfinance lacks) into index_ohlcv. Returns rows upserted."""
    import datetime as _dt  # noqa: PLC0415

    from data.repositories.stock import last_index_bhavcopy_date, save_index_bhavcopy_day  # noqa: PLC0415

    today = _dt.date.today()
    last = last_index_bhavcopy_date()
    start = max(last + _dt.timedelta(days=1), today - _dt.timedelta(days=max_days)) if last else today - _dt.timedelta(days=max_days)
    total = 0
    day = start
    while day <= today:
        total += save_index_bhavcopy_day(day)
        day += _dt.timedelta(days=1)
    logger.info("index bhavcopy refresh: %d rows from %s to %s", total, start, today)
    return total


def recompute_all_stock_metrics() -> int:
    """Recompute CAPM price metrics for the whole tracked stock universe (uses cached OHLCV)."""
    from data.repositories.stock_fundamentals import load_stock_metrics  # noqa: PLC0415
    from services.stock_metrics import recompute_price_metrics  # noqa: PLC0415

    m = load_stock_metrics()
    symbols = m["symbol"].to_list() if not m.is_empty() and "symbol" in m.columns else []
    return recompute_price_metrics(symbols) if symbols else 0


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
