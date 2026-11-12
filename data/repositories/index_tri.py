"""NSE total-return index repository (index_tri): DB-first gap fetch from niftyindices."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import polars as pl
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from core.models import IndexTri
from data.constants import MIN_FETCH_DAYS
from data.fetchers.niftyindices import TRI_SCHEMA, fetch_index_tri

logger = logging.getLogger(__name__)


def get_index_tri_range(symbol: str) -> tuple[date, date] | None:
    """(first, last) stored TRI date for `symbol`, or None when nothing is stored."""
    with get_session() as session:
        lo, hi = session.exec(
            select(func.min(col(IndexTri.date)), func.max(col(IndexTri.date))).where(col(IndexTri.symbol) == symbol)
        ).one()
    return (lo, hi) if lo is not None else None


def save_index_tri(symbol: str, frame: pl.DataFrame) -> int:
    """Upsert TRI_SCHEMA rows for `symbol`; returns rows written."""
    if frame.is_empty():
        return 0
    rows = [{"symbol": symbol, **r} for r in frame.select(list(TRI_SCHEMA)).to_dicts()]
    with get_session() as session:
        stmt = pg_insert(IndexTri).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["date", "symbol"], set_={"tri": stmt.excluded.tri, "ntr": stmt.excluded.ntr}
        )
        session.exec(stmt)
        session.commit()
    return len(rows)


def load_index_tri(symbol: str, start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """Stored TRI rows (TRI_SCHEMA) for `symbol` within [start, end], oldest first."""
    stmt = select(IndexTri).where(col(IndexTri.symbol) == symbol)
    if start is not None:
        stmt = stmt.where(col(IndexTri.date) >= start)
    if end is not None:
        stmt = stmt.where(col(IndexTri.date) <= end)
    with get_session() as session:
        rows = session.exec(stmt.order_by(col(IndexTri.date))).all()
    return pl.DataFrame([{"date": r.date, "tri": r.tri, "ntr": r.ntr} for r in rows], schema=TRI_SCHEMA)


def ensure_index_tri(symbol: str, start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """DB-first TRI for `symbol` (NSE name as in index_ohlcv): an empty table pulls full history once,
    afterwards only the forward gap past the last stored day is fetched."""
    end = end or date.today()
    stored = get_index_tri_range(symbol)
    if stored is None:
        save_index_tri(symbol, fetch_index_tri(symbol, None, end))
    elif (end - stored[1]).days >= MIN_FETCH_DAYS:
        save_index_tri(symbol, fetch_index_tri(symbol, stored[1] + timedelta(days=1), end))
    return load_index_tri(symbol, start, end)
