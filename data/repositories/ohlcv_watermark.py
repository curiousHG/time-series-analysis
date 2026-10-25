"""OHLCV availability watermark (status / earliest floor / empty-streak) on the stock and index registries,
plus the shared DB-first gap-fetch used by stock_ohlcv and index_ohlcv."""

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
_UNAVAILABLE_EMPTY_STREAK = 2
_COLD_BACKFILL_DAYS = 365 * 10


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
            row = StockRegistry(symbol=symbol)
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
    """DB-first gap-fetch for `symbol` over [start, end] using the given table, fetcher and watermark accessors."""
    wm = get_wm(symbol)
    if wm.status == "unavailable":
        return EMPTY_OHLCV.clone()

    existing = _date_range(model, symbol)
    if existing is None:
        last_empty = _empty_fetch_cache.get(symbol)
        if last_empty is not None and datetime.now() - last_empty < _EMPTY_FETCH_TTL:
            return EMPTY_OHLCV.clone()
        fetch_fn(symbol, start, end)
        rng = _date_range(model, symbol)
        if rng is None:
            _empty_fetch_cache[symbol] = datetime.now()
            streak = wm.streak + 1
            if flag_unavailable and streak >= _UNAVAILABLE_EMPTY_STREAK:
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
        if start < db_min and (db_min - start).days >= MIN_FETCH_DAYS and wm.earliest != db_min:
            fetch_fn(symbol, start, db_min)
            new_min = _date_range(model, symbol)[0]
            floor = db_min if new_min >= db_min else None
            set_wm(symbol, status="available", earliest=floor, empty_streak=0)
        elif wm.status != "available":
            set_wm(symbol, status="available", empty_streak=0)
        if end > db_max and (end - db_max).days >= MIN_FETCH_DAYS:
            fetch_fn(symbol, db_max, end)

    return _load_ohlcv(model, symbol, start, end)


def _refresh_to_today(symbol, *, model, fetch_fn) -> tuple[date, date | None]:
    today = date.today()
    existing = _date_range(model, symbol)
    if existing is None:
        start = today - timedelta(days=_COLD_BACKFILL_DAYS)
        fetch_fn(symbol, start, today + timedelta(days=1))
    else:
        _, db_max = existing
        if db_max >= today:
            return today, db_max
        fetch_fn(symbol, db_max, today + timedelta(days=1))
    new_range = _date_range(model, symbol)
    return today, (new_range[1] if new_range else None)


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
