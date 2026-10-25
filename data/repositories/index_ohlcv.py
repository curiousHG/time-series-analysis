"""Index OHLCV repository (index_ohlcv): NSE index bhavcopy for name-keyed indices, yfinance for literal symbols."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from core.models import IndexOhlcv
from data.fetchers.stock import fetch_symbol_data
from data.repositories._ohlcv_io import _load_ohlcv, _to_date, _upsert_ohlcv
from data.repositories.ohlcv_watermark import _ensure_ohlcv, _get_index_wm, _refresh_to_today, _set_index_wm

if TYPE_CHECKING:
    from datetime import date, datetime

logger = logging.getLogger(__name__)


def save_index_bhavcopy_day(day: date) -> int:
    """Fetch the NSE index bhavcopy for `day` and bulk-upsert every index, keyed by NSE index name. Returns rows
    upserted."""
    import pandas as pd  # noqa: PLC0415

    from data.fetchers.stock import fetch_nse_index_bhavcopy  # noqa: PLC0415

    df = fetch_nse_index_bhavcopy(day)
    if df is None or df.empty:
        return 0

    def _num(v: object) -> float | None:
        return float(v) if pd.notna(v) else None

    rows = [
        {
            "date": r.Date,
            "symbol": r.Name,
            "open": _num(r.Open),
            "high": _num(r.High),
            "low": _num(r.Low),
            "close": _num(r.Close),
            "volume": int(r.Volume) if pd.notna(r.Volume) and r.Volume else None,
            "turnover_cr": _num(r.TurnoverCr),
            "pe": _num(r.PE),
            "pb": _num(r.PB),
            "div_yield": _num(r.DivYield),
        }
        for r in df.itertuples(index=False)
    ]
    with get_session() as session:
        stmt = pg_insert(IndexOhlcv).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["date", "symbol"],
            set_={
                c: stmt.excluded[c]
                for c in ("open", "high", "low", "close", "volume", "turnover_cr", "pe", "pb", "div_yield")
            },
        )
        session.exec(stmt)
        session.commit()
    logger.info("index bhavcopy %s: upserted %d indices", day, len(rows))
    return len(rows)


def last_index_bhavcopy_date() -> date | None:
    """Latest bhavcopy date, tracked via the 'Nifty 50' row."""
    with get_session() as session:
        return session.exec(select(func.max(col(IndexOhlcv.date))).where(col(IndexOhlcv.symbol) == "Nifty 50")).one()


def first_index_bhavcopy_date() -> date | None:
    """Earliest bhavcopy date, tracked via the 'Nifty 50' row."""
    with get_session() as session:
        return session.exec(select(func.min(col(IndexOhlcv.date))).where(col(IndexOhlcv.symbol) == "Nifty 50")).one()


def _fetch_and_save_index(symbol: str, start: date, end: date) -> None:
    """Fetch an index by its literal yfinance symbol and upsert; name-keyed NSE indices (containing a space) are
    skipped."""
    if " " in symbol:
        logger.debug("index %r is bhavcopy-maintained (bulk); no on-demand fetch", symbol)
        return
    data = fetch_symbol_data(symbol, start=start, end=end)
    if data is None or data.empty:
        return
    frame = _reject_implausible_index_bars(symbol, pl.from_pandas(data.reset_index()))
    if frame.height:
        _upsert_ohlcv(IndexOhlcv, symbol, frame)


_MAX_INDEX_DAY_MOVE = 0.5
_LIVE_INDEX_WINDOW_DAYS = 45


def _reject_implausible_index_bars(symbol: str, frame: pl.DataFrame) -> pl.DataFrame:
    if frame.height == 0 or "Close" not in frame.columns:
        return frame
    first_date = _to_date(frame["Date"].min())
    with get_session() as session:
        prev = session.exec(
            select(col(IndexOhlcv.close))
            .where(col(IndexOhlcv.symbol) == symbol, col(IndexOhlcv.date) < first_date)
            .order_by(col(IndexOhlcv.date).desc())
            .limit(1)
        ).first()
    if not prev:
        return frame
    anchor = float(prev)
    keep: list[bool] = []
    for close in frame.sort("Date")["Close"].to_list():
        ok = close is not None and close > 0 and abs(close / anchor - 1) <= _MAX_INDEX_DAY_MOVE
        keep.append(ok)
        if ok:
            anchor = float(close)
    dropped = keep.count(False)
    if dropped:
        logger.warning(
            "%s: rejected %d implausible index bar(s) (> %.0f%% from prior close)",
            symbol,
            dropped,
            _MAX_INDEX_DAY_MOVE * 100,
        )
        frame = frame.sort("Date").with_columns(pl.Series("_keep", keep)).filter(pl.col("_keep")).drop("_keep")
    return frame


def ensure_index_data(symbol: str, start_date: datetime | date, end_date: datetime | date) -> pl.DataFrame:
    """DB-first OHLCV loader for market indices (index_ohlcv)."""
    start = _to_date(start_date)
    end = _to_date(end_date)
    return _ensure_ohlcv(
        symbol,
        start,
        end,
        model=IndexOhlcv,
        fetch_fn=_fetch_and_save_index,
        get_wm=_get_index_wm,
        set_wm=_set_index_wm,
        flag_unavailable=False,
    )


def refresh_index_to_today(symbol: str) -> tuple[date, date | None]:
    """refresh_stock_to_today for an index (index_ohlcv)."""
    return _refresh_to_today(symbol, model=IndexOhlcv, fetch_fn=_fetch_and_save_index)


def read_index_ohlcv(symbol: str, start_date: datetime | date, end_date: datetime | date) -> pl.DataFrame:
    """Read index OHLCV from index_ohlcv without fetching, with a Symbol column added."""
    df = _load_ohlcv(IndexOhlcv, symbol, _to_date(start_date), _to_date(end_date))
    return df if df.is_empty() else df.with_columns(pl.lit(symbol).alias("Symbol"))


def latest_index_valuation(symbol: str) -> dict | None:
    """Latest P/E, P/B, Div Yield and turnover (₹ cr) for an index, or None if it has no valuation rows."""
    with get_session() as session:
        row = session.exec(
            select(
                col(IndexOhlcv.date),
                col(IndexOhlcv.pe),
                col(IndexOhlcv.pb),
                col(IndexOhlcv.div_yield),
                col(IndexOhlcv.turnover_cr),
            )
            .where(col(IndexOhlcv.symbol) == symbol, col(IndexOhlcv.pe).is_not(None))
            .order_by(col(IndexOhlcv.date).desc())
            .limit(1)
        ).first()
    if row is None:
        return None
    return {"date": row[0], "pe": row[1], "pb": row[2], "div_yield": row[3], "turnover_cr": row[4]}


def list_index_symbols() -> list[str]:
    """Distinct index symbols with stored OHLCV."""
    with get_session() as session:
        rows = session.exec(select(col(IndexOhlcv.symbol)).distinct().order_by(col(IndexOhlcv.symbol))).all()
    return list(rows)


def list_bhavcopy_index_names() -> list[str]:
    """NSE-name indices published within the last 45 days of the newest bhavcopy day."""
    with get_session() as session:
        newest = session.exec(
            select(func.max(col(IndexOhlcv.date))).where(~col(IndexOhlcv.symbol).startswith("^"))
        ).one()
        if newest is None:
            return []
        rows = session.exec(
            select(col(IndexOhlcv.symbol))
            .where(~col(IndexOhlcv.symbol).startswith("^"), ~col(IndexOhlcv.symbol).contains("="))
            .group_by(col(IndexOhlcv.symbol))
            .having(func.max(col(IndexOhlcv.date)) >= newest - timedelta(days=_LIVE_INDEX_WINDOW_DAYS))
            .order_by(col(IndexOhlcv.symbol))
        ).all()
    return list(rows)
