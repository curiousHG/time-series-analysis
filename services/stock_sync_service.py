"""Populate stock fundamentals (screener.in) + price metrics (CAPM) for a set of symbols.

Off-boot orchestration for the Settings / screener "populate" action. Scraping is polite by
construction — DB-first `ensure_stock_fundamentals` skips already-fresh symbols.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from datetime import timedelta as _timedelta
from typing import TYPE_CHECKING

from data.repositories.stock_fundamentals import ensure_stock_fundamentals
from services.stock_metrics import recompute_price_metrics

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _days_between(start: _date, end: _date) -> list[_date]:
    return [start + _timedelta(days=i) for i in range((end - start).days + 1)]


def _report(progress_cb: Callable[..., None] | None, **fields: object) -> None:
    if progress_cb is not None:
        progress_cb(**fields)


def refresh_stocks_via_bhavcopy(*, max_days: int = 90, progress_cb: Callable[..., None] | None = None) -> int:
    """Append every NSE bhavcopy day from the last stored date up to today for tracked stocks
    (weekends/holidays are simply empty). One bulk download per day beats a yfinance call per
    symbol. `max_days` caps a cold-start backfill. Returns rows upserted."""
    from data.repositories.stock import last_stock_ohlcv_date, save_bhavcopy_day  # noqa: PLC0415 — defer off boot

    today = _date.today()
    last = last_stock_ohlcv_date()
    start = max(last + _timedelta(days=1), today - _timedelta(days=max_days)) if last else today - _timedelta(days=max_days)
    days = _days_between(start, today)
    total = 0
    for i, day in enumerate(days, 1):
        total += save_bhavcopy_day(day, only_existing=True)
        _report(progress_cb, phase="Stocks", done=i, total=len(days))
    logger.info("bhavcopy refresh: %d rows from %s to %s", total, start, today)
    return total


def refresh_indices_via_bhavcopy(*, max_days: int = 120, progress_cb: Callable[..., None] | None = None) -> int:
    """Backfill every NSE index bhavcopy day from the last stored index date up to today — 160+
    indices (incl. factor/strategy indices yfinance lacks) into index_ohlcv. Returns rows upserted."""
    from data.repositories.stock import last_index_bhavcopy_date, save_index_bhavcopy_day  # noqa: PLC0415

    today = _date.today()
    last = last_index_bhavcopy_date()
    start = max(last + _timedelta(days=1), today - _timedelta(days=max_days)) if last else today - _timedelta(days=max_days)
    days = _days_between(start, today)
    total = 0
    for i, day in enumerate(days, 1):
        total += save_index_bhavcopy_day(day)
        _report(progress_cb, phase="Indices", done=i, total=len(days))
    logger.info("index bhavcopy refresh: %d rows from %s to %s", total, start, today)
    return total


def backfill_indices_history(*, years: int = 12, progress_cb: Callable[..., None] | None = None) -> int:
    """Backfill index bhavcopy history as far back as ~`years` — fills the backward gap (below the
    earliest stored day) and the forward gap (last→today), skipping the already-stored range.
    Long-running; run it on a background task. Returns rows upserted."""
    from data.repositories.stock import (  # noqa: PLC0415
        first_index_bhavcopy_date,
        last_index_bhavcopy_date,
        save_index_bhavcopy_day,
    )

    today = _date.today()
    target = today - _timedelta(days=365 * years)
    first, last = first_index_bhavcopy_date(), last_index_bhavcopy_date()
    back = _days_between(target, first - _timedelta(days=1)) if first else []
    fwd = _days_between((last + _timedelta(days=1)) if last else target, today)
    days = back + fwd
    total = 0
    for i, day in enumerate(days, 1):
        total += save_index_bhavcopy_day(day)
        _report(progress_cb, phase="Index history", done=i, total=len(days))
    logger.info("index history backfill: %d rows (target %s)", total, target)
    return total


def refresh_all_stock_data(*, progress_cb: Callable[..., None] | None = None) -> dict:
    """Full stock + index data refresh for the background task: bhavcopy stocks + indices, then
    recompute stock metrics. Reports live phase/progress via `progress_cb`. Returns per-step counts."""
    from data.repositories.stock import sync_nse_etf_universe  # noqa: PLC0415 — defer off boot

    stock_rows = refresh_stocks_via_bhavcopy(progress_cb=progress_cb)
    index_rows = refresh_indices_via_bhavcopy(progress_cb=progress_cb)
    _report(progress_cb, phase="ETF list", done=0, total=1)
    etfs = sync_nse_etf_universe()  # keep the ETF classification current for the analysis picker
    _report(progress_cb, phase="ETF list", done=1, total=1)
    _report(progress_cb, phase="Metrics", done=0, total=1)
    metrics = recompute_all_stock_metrics()
    _report(progress_cb, phase="Metrics", done=1, total=1)
    return {"stock_rows": stock_rows, "index_rows": index_rows, "etfs": etfs, "metrics": metrics}


def stock_data_health() -> dict:
    """Counts + last-updated dates + staleness for stock/index data (Settings data-health view)."""
    from sqlmodel import text  # noqa: PLC0415

    from core.database import get_session  # noqa: PLC0415
    from services.data_freshness import business_days_between  # noqa: PLC0415

    def _q(session, sql: str):
        return session.exec(text(sql)).one()[0]

    with get_session() as session:
        stocks = _q(session, "SELECT count(distinct symbol) FROM stock_ohlcv") or 0
        stock_last = _q(session, "SELECT max(date) FROM stock_ohlcv")
        indices = _q(session, "SELECT count(distinct symbol) FROM index_ohlcv") or 0
        index_last = _q(session, "SELECT max(date) FROM index_ohlcv")
        index_first = _q(session, "SELECT min(date) FROM index_ohlcv WHERE symbol = 'Nifty 50'")
        with_metrics = _q(session, "SELECT count(*) FROM stock_metrics WHERE return_1y IS NOT NULL") or 0
        with_fundamentals = _q(session, "SELECT count(*) FROM stock_registry WHERE fundamentals_status = 'available'") or 0
        unavailable = _q(session, "SELECT count(*) FROM stock_registry WHERE ohlcv_status = 'unavailable'") or 0

    today = _date.today()

    def _bdays_old(d) -> int | None:
        return business_days_between(d, today) if d else None

    return {
        "stocks": stocks,
        "stock_last": stock_last,
        "stock_stale_days": _bdays_old(stock_last),
        "indices": indices,
        "index_last": index_last,
        "index_stale_days": _bdays_old(index_last),
        "index_first": index_first,
        "with_metrics": with_metrics,
        "with_fundamentals": with_fundamentals,
        "unavailable": unavailable,
    }


def find_corrupt_ohlcv_symbols(*, jump: float = 5.0) -> list[str]:
    """Symbols whose stored OHLCV has an implausible ADJACENT-DAY close jump (> `jump`x or < 1/jump)
    - a scale discontinuity from a bad historical fetch (a stale yfinance-adjusted series spliced
    onto raw bhavcopy rows). A ~600x splice corrupts return/CAPM metrics (PAGEIND showed return_1y
    ~ 26,775%). Adjacent-jump detection avoids flagging legit long-history growth (a real 20x over
    years has no single-day 5x gap)."""
    from sqlmodel import text  # noqa: PLC0415

    from core.database import get_session  # noqa: PLC0415

    with get_session() as session:
        rows = session.exec(
            text(
                "WITH r AS (SELECT symbol, close, "
                "LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev "
                "FROM stock_ohlcv WHERE close > 0) "
                "SELECT DISTINCT symbol FROM r "
                "WHERE prev > 0 AND (close / prev > :j OR close / prev < :inv) ORDER BY symbol"
            ),
            params={"j": jump, "inv": 1.0 / jump},
        ).all()
    return [r[0] for r in rows]


def repair_corrupt_ohlcv(*, jump: float = 5.0, progress_cb: Callable[..., None] | None = None) -> dict:
    """Re-fetch full history for stocks whose OHLCV has a corrupt scale discontinuity, overwriting the
    bad rows in place (UPSERT, no delete — safe to interrupt), then recompute their metrics. Returns
    {'repaired': [...], 'failed': [...]}."""
    from data.repositories.stock import clear_stock_ohlcv_status, refetch_stock_full  # noqa: PLC0415
    from services.stock_metrics import recompute_price_metrics  # noqa: PLC0415

    symbols = find_corrupt_ohlcv_symbols(jump=jump)
    repaired: list[str] = []
    failed: list[str] = []
    for i, sym in enumerate(symbols, 1):
        try:
            clear_stock_ohlcv_status(sym)  # reset the watermark (floor/streak) before the re-fetch
            refetch_stock_full(sym)  # fetch + upsert full history over the corrupt rows (no delete)
            repaired.append(sym)
        except Exception:
            failed.append(sym)
        _report(progress_cb, phase="Repair", done=i, total=len(symbols))
    if repaired:
        recompute_price_metrics(repaired)
    logger.info("repair_corrupt_ohlcv: repaired %d, failed %d", len(repaired), len(failed))
    return {"repaired": repaired, "failed": failed}


def refetch_all_stocks(*, progress_cb: Callable[..., None] | None = None) -> int:
    """Replace EVERY stock's OHLCV with a fresh yfinance pull (the single reliable source), then
    recompute metrics. Cleans source-mixing corruption (jugaad IST-offset dates / spikes) across the
    whole universe. Each stock is an atomic fetch-then-replace, so it's safe to interrupt. Returns
    the number of stocks re-fetched."""
    from data.repositories.stock import (  # noqa: PLC0415
        clear_stock_ohlcv_status,
        list_stock_symbols,
        refetch_stock_full,
    )
    from services.stock_metrics import recompute_price_metrics  # noqa: PLC0415

    symbols = list_stock_symbols()
    done: list[str] = []
    for i, sym in enumerate(symbols, 1):
        try:
            clear_stock_ohlcv_status(sym)
            refetch_stock_full(sym)
            done.append(sym)
        except Exception:
            logger.debug("re-fetch failed for %s", sym)
        _report(progress_cb, phase="Re-fetch", done=i, total=len(symbols))
    if done:
        recompute_price_metrics(done)
    logger.info("refetch_all_stocks: re-fetched %d of %d", len(done), len(symbols))
    return len(done)


def sync_missing_fundamentals(*, progress_cb: Callable[..., None] | None = None) -> int:
    """Scrape screener.in fundamentals for universe stocks that don't have them yet (the Nifty 500
    seed loads price metrics only). Polite per-symbol scrape, then one metrics recompute. Returns
    the number of symbols attempted."""
    from sqlmodel import text  # noqa: PLC0415

    from core.database import get_session  # noqa: PLC0415
    from data.repositories.stock_fundamentals import ensure_stock_fundamentals  # noqa: PLC0415
    from services.stock_metrics import recompute_price_metrics  # noqa: PLC0415

    with get_session() as session:
        symbols = [
            r[0]
            for r in session.exec(
                text(
                    "SELECT m.symbol FROM stock_metrics m LEFT JOIN stock_registry r ON r.symbol = m.symbol "
                    "WHERE coalesce(r.fundamentals_status, '') <> 'available' ORDER BY m.symbol"
                )
            ).all()
        ]
    for i, sym in enumerate(symbols, 1):
        try:
            ensure_stock_fundamentals([sym])
        except Exception:
            logger.debug("fundamentals scrape failed for %s", sym)
        _report(progress_cb, phase="Fundamentals", done=i, total=len(symbols))
    if symbols:
        recompute_price_metrics(symbols)
    logger.info("sync_missing_fundamentals: attempted %d symbols", len(symbols))
    return len(symbols)


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
