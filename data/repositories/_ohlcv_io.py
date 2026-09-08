"""Low-level OHLCV table I/O shared by stock_ohlcv and index_ohlcv — upsert / load / date-range
keyed by a passed-in model (StockOhlcv or IndexOhlcv), plus the shared negative-fetch cache. No
domain logic, no fetching."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import lru_cache

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


@lru_cache(maxsize=1)
def _special_session_dates() -> frozenset[date]:
    """Weekend dates on which NSE actually traded (Budget-day / Muhurat sessions), from the
    index bhavcopy — the only source here that reflects the real trading calendar."""
    from core.models import IndexOhlcv  # noqa: PLC0415 — model import kept off this module's top level

    with get_session() as session:
        rows = session.exec(select(col(IndexOhlcv.date)).where(col(IndexOhlcv.symbol) == "Nifty 50").distinct()).all()
    return frozenset(d for d in rows if d.weekday() >= 5)


def _drop_phantom_weekend_bars(df: pl.DataFrame, symbol: str) -> pl.DataFrame:
    """Drop Saturday/Sunday bars that are not a known NSE special session.

    yfinance answers a delisted/renamed ticker (TATAMOTORS after the demerger) with a history
    whose dates are shifted a day earlier, which shows up as a bar on every Sunday. Nothing
    trades on a weekend except NSE's occasional special sessions, so those are the only weekend
    dates allowed through.
    """
    weekend = df.filter(pl.col("Date").dt.weekday() >= 6)
    if weekend.height == 0:
        return df
    allowed = _special_session_dates()
    phantom = [d for d in weekend["Date"].to_list() if _as_date(d) not in allowed]
    if not phantom:
        return df
    logger.warning(
        "%s: dropping %d weekend bar(s) outside NSE special sessions (first %s)", symbol, len(phantom), phantom[0]
    )
    return df.filter(~(pl.col("Date").dt.weekday() >= 6) | pl.col("Date").is_in(sorted(allowed)))


def _as_date(d) -> date:
    return d.date() if isinstance(d, datetime) else d


def _upsert_ohlcv(model: type, symbol: str, df: pl.DataFrame) -> None:
    """Upsert OHLCV rows into `model`'s table (StockOhlcv or IndexOhlcv). Rows without a positive
    close are dropped — yahoo's early-2000s .NS history contains zero/null-close days that would
    otherwise poison return calculations."""
    if df.height == 0:
        return
    df = df.filter(pl.col("Close").is_not_null() & (pl.col("Close") > 0))
    df = _drop_phantom_weekend_bars(df, symbol)
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
