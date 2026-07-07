"""Low-level OHLCV table I/O shared by stock_ohlcv and index_ohlcv — upsert / load / date-range
keyed by a passed-in model (StockOhlcv or IndexOhlcv), plus the shared negative-fetch cache. No
domain logic, no fetching."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import polars as pl
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from data.constants import EMPTY_OHLCV

logger = logging.getLogger(__name__)

# Negative cache for symbols that returned zero rows. Without this, a symbol yfinance can
# never serve (e.g. a delisted ticker) keeps `_date_range` returning None, so ensure_* re-runs a
# slow doomed fetch on every call/rerun — which hangs pages like MF Analysis.
# In-process, short TTL so transient outages still retry.
_EMPTY_FETCH_TTL = timedelta(hours=1)
_empty_fetch_cache: dict[str, datetime] = {}


def _to_date(d: datetime | date) -> date:
    return d.date() if isinstance(d, datetime) else d


def _upsert_ohlcv(model: type, symbol: str, df: pl.DataFrame) -> None:
    """Upsert OHLCV rows into `model`'s table (StockOhlcv or IndexOhlcv). Rows without a positive
    close are dropped — yahoo's early-2000s .NS history contains zero/null-close days that would
    otherwise poison return calculations."""
    if df.height == 0:
        return
    df = df.filter(pl.col("Close").is_not_null() & (pl.col("Close") > 0))
    if df.height == 0:
        return
    with get_session() as session:
        for row in df.iter_rows(named=True):
            stmt = (
                pg_insert(model)
                .values(
                    date=row["Date"],
                    symbol=symbol,
                    open=row.get("Open"),
                    high=row.get("High"),
                    low=row.get("Low"),
                    close=row.get("Close"),
                    volume=row.get("Volume"),
                )
                .on_conflict_do_update(
                    index_elements=["date", "symbol"],
                    set_={
                        "open": row.get("Open"),
                        "high": row.get("High"),
                        "low": row.get("Low"),
                        "close": row.get("Close"),
                        "volume": row.get("Volume"),
                    },
                )
            )
            session.exec(stmt)
        session.commit()
    logger.info("Saved %d OHLCV rows for %s", df.height, symbol)


def _load_ohlcv(model: type, symbol: str, start_date: date, end_date: date) -> pl.DataFrame:
    """Load OHLCV rows for `symbol` from `model`'s table within a date range."""
    with get_session() as session:
        rows = session.exec(
            select(model)
            .where(
                col(model.symbol) == symbol,
                col(model.date) >= start_date,
                col(model.date) <= end_date,
            )
            .order_by(col(model.date))
        ).all()

    if not rows:
        return EMPTY_OHLCV.clone()

    return pl.DataFrame(
        {
            "Date": [r.date for r in rows],
            "Open": [r.open for r in rows],
            "High": [r.high for r in rows],
            "Low": [r.low for r in rows],
            "Close": [r.close for r in rows],
            "Volume": [r.volume for r in rows],
        }
    )


def _date_range(model: type, symbol: str) -> tuple[date, date] | None:
    """Min/max dates for `symbol` in `model`'s table, or None if no data."""
    with get_session() as session:
        row = session.exec(
            select(func.min(col(model.date)), func.max(col(model.date))).where(col(model.symbol) == symbol)
        ).one()
    if row[0] is None:
        return None
    return row[0], row[1]
