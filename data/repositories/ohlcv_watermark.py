"""OHLCV availability watermark (status / earliest floor / empty-streak) + the shared DB-first
gap-fetch. State lives on stock_registry (equities, bare key) and index_registry (indices): the
`earliest` floor stops ensure_* from re-attempting impossible pre-inception history; the
empty_streak flips a stock to `unavailable` after 2 consecutive empty fetches so it's skipped.

The gap-fetch (`_ensure_ohlcv`) and forward-refresh (`_refresh_to_today`) are dependency-injected
with a model + fetcher + watermark accessors, so stock_ohlcv and index_ohlcv share one code path."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlmodel import col, select

from core.database import get_session
from core.models import IndexRegistry, StockRegistry
from core.notifications import push_notice
from data.constants import EMPTY_OHLCV, MIN_FETCH_DAYS
from data.repositories._ohlcv_io import _EMPTY_FETCH_TTL, _date_range, _empty_fetch_cache, _load_ohlcv
from stocks.constants import to_bare_symbol

if TYPE_CHECKING:
    import polars as pl

logger = logging.getLogger(__name__)

_UNSET = object()


@dataclass
class _Wm:
    status: str | None = None
    earliest: date | None = None
    streak: int = 0


def _get_stock_wm(symbol: str) -> _Wm:
    with get_session() as session:
        row = session.get(StockRegistry, symbol)
        if row is None:
            return _Wm()
        return _Wm(row.ohlcv_status, row.ohlcv_earliest, row.ohlcv_empty_streak or 0)


def _set_stock_wm(symbol: str, *, status: str | None = None, earliest=_UNSET, empty_streak: int | None = None) -> None:
    with get_session() as session:
        row = session.get(StockRegistry, symbol)
        if row is None:
            row = StockRegistry(symbol=symbol)  # on-demand (symbol not in the NSE master)
            session.add(row)
        if status is not None:
            row.ohlcv_status = status
            row.ohlcv_as_of = datetime.now()
        if earliest is not _UNSET:
            row.ohlcv_earliest = earliest
        if empty_streak is not None:
            row.ohlcv_empty_streak = empty_streak
        session.commit()


def _get_index_wm(symbol: str) -> _Wm:
    with get_session() as session:
        row = session.get(IndexRegistry, symbol)
        if row is None:
            return _Wm()
        return _Wm(row.status, row.earliest, 0)


def _set_index_wm(symbol: str, *, status: str | None = None, earliest=_UNSET, empty_streak: int | None = None) -> None:
    with get_session() as session:
        row = session.get(IndexRegistry, symbol)
        if row is None:
            row = IndexRegistry(symbol=symbol)
            session.add(row)
        if status is not None:
            row.status = status
            row.as_of = datetime.now()
        if earliest is not _UNSET:
            row.earliest = earliest
        session.commit()


def _ensure_ohlcv(symbol, start, end, *, model, fetch_fn, get_wm, set_wm, flag_unavailable) -> pl.DataFrame:
    """Shared DB-first gap-fetch with backward-gap watermark. Used by ensure_stock_data and
    ensure_index_data with their respective table/fetcher/watermark accessors."""
    wm = get_wm(symbol)
    if wm.status == "unavailable":
        return EMPTY_OHLCV.clone()  # short-circuit — cleared by a Settings retry

    existing = _date_range(model, symbol)
    if existing is None:
        last_empty = _empty_fetch_cache.get(symbol)
        if last_empty is not None and datetime.now() - last_empty < _EMPTY_FETCH_TTL:
            return EMPTY_OHLCV.clone()  # known-empty this hour — don't re-hammer
        fetch_fn(symbol, start, end)
        rng = _date_range(model, symbol)
        if rng is None:
            _empty_fetch_cache[symbol] = datetime.now()
            streak = wm.streak + 1
            if flag_unavailable and streak >= 2:
                set_wm(symbol, status="unavailable", empty_streak=streak)
                push_notice(f"No price data for {symbol} — marked unavailable.", level="warning", key=f"ohlcv:{symbol}")
            else:
                set_wm(symbol, status="pending", empty_streak=streak)
            logger.info("no OHLCV for %s — negative-caching for %s (streak=%d)", symbol, _EMPTY_FETCH_TTL, streak)
        else:
            _empty_fetch_cache.pop(symbol, None)
            set_wm(symbol, status="available", earliest=rng[0], empty_streak=0)
    else:
        db_min, db_max = existing
        # Backward gap — only if we haven't already confirmed db_min is the floor.
        if start < db_min and (db_min - start).days >= MIN_FETCH_DAYS and wm.earliest != db_min:
            fetch_fn(symbol, start, db_min)
            new_min = _date_range(model, symbol)[0]
            floor = db_min if new_min >= db_min else None  # lock floor only if nothing earlier arrived
            set_wm(symbol, status="available", earliest=floor, empty_streak=0)
        elif wm.status != "available":
            set_wm(symbol, status="available", empty_streak=0)
        # Forward gap.
        if end > db_max and (end - db_max).days >= MIN_FETCH_DAYS:
            fetch_fn(symbol, db_max, end)

    return _load_ohlcv(model, symbol, start, end)


def _refresh_to_today(symbol, *, model, fetch_fn) -> tuple[date, date | None]:
    today = date.today()
    existing = _date_range(model, symbol)
    if existing is None:
        # Cold cache: pull a 10Y backfill so all rolling-CAGR windows have data.
        start = today - timedelta(days=365 * 10)
        fetch_fn(symbol, start, today + timedelta(days=1))
    else:
        _, db_max = existing
        if db_max >= today:
            return today, db_max
        fetch_fn(symbol, db_max, today + timedelta(days=1))  # force-fetch regardless of MIN_FETCH_DAYS
    new_range = _date_range(model, symbol)
    return today, (new_range[1] if new_range else None)


# ---- Status queries for the UI (auto-remove dead symbols, Settings retry, index picker) ----


def get_stock_ohlcv_statuses(symbols: list[str]) -> dict[str, str]:
    """{bare_symbol: status} for the given symbols that carry an ohlcv_status."""
    bare = [to_bare_symbol(s) for s in symbols]
    if not bare:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(StockRegistry.symbol, StockRegistry.ohlcv_status).where(col(StockRegistry.symbol).in_(bare))
        ).all()
    return {sym: status for sym, status in rows if status}


def list_unavailable_stock_symbols() -> list[str]:
    """Bare symbols whose price history was confirmed unavailable."""
    with get_session() as session:
        rows = session.exec(select(StockRegistry.symbol).where(StockRegistry.ohlcv_status == "unavailable")).all()
    return list(rows)


def clear_stock_ohlcv_status(symbol: str) -> None:
    """Reset a symbol for a fresh retry: status→pending, clear floor + streak + negative cache."""
    sym = to_bare_symbol(symbol)
    _empty_fetch_cache.pop(sym, None)
    _empty_fetch_cache.pop(symbol, None)
    with get_session() as session:
        row = session.get(StockRegistry, sym)
        if row is not None:
            row.ohlcv_status = "pending"
            row.ohlcv_earliest = None
            row.ohlcv_empty_streak = 0
            row.ohlcv_as_of = datetime.now()
            session.commit()
