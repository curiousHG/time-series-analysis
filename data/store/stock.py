import logging
import polars as pl
from datetime import datetime, date
from sqlmodel import select, col
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.database import get_session
from core.models import StockOhlcv, StockRegistry
from data.fetchers.stock import fetch_symbol_data, fetch_symbol_data_jugaad

logger = logging.getLogger("data.store.stock")


EMPTY_OHLCV = pl.DataFrame(
    schema={
        "Date": pl.Date,
        "Open": pl.Float64,
        "High": pl.Float64,
        "Low": pl.Float64,
        "Close": pl.Float64,
        "Volume": pl.Int64,
    }
)


def _to_date(d: datetime | date) -> date:
    return d.date() if isinstance(d, datetime) else d


def _fetch_and_save(symbol: str, start: date, end: date) -> None:
    """Fetch stock data from external sources and upsert into DB."""
    if symbol.endswith(".NS"):
        data = fetch_symbol_data_jugaad(symbol, start, end)
        if data is not None and not data.empty:
            _upsert_ohlcv(symbol, pl.from_pandas(data.reset_index()))
            return

    data = fetch_symbol_data(symbol, start=start, end=end)
    if data is not None and not data.empty:
        _upsert_ohlcv(symbol, pl.from_pandas(data.reset_index()))


def _upsert_ohlcv(symbol: str, df: pl.DataFrame) -> None:
    """Upsert OHLCV rows into the database."""
    if df.height == 0:
        return
    with get_session() as session:
        for row in df.iter_rows(named=True):
            stmt = (
                pg_insert(StockOhlcv)
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
            session.execute(stmt)
        session.commit()
    logger.info("Saved %d OHLCV rows for %s", df.height, symbol)


def _load_ohlcv(symbol: str, start_date: date, end_date: date) -> pl.DataFrame:
    """Load OHLCV data from database for a symbol within a date range."""
    with get_session() as session:
        rows = (
            session.execute(
                select(StockOhlcv)
                .where(
                    col(StockOhlcv.symbol) == symbol,
                    col(StockOhlcv.date) >= start_date,
                    col(StockOhlcv.date) <= end_date,
                )
                .order_by(col(StockOhlcv.date))
            )
            .scalars()
            .all()
        )

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


def _get_date_range(symbol: str) -> tuple[date, date] | None:
    """Get min/max dates for a symbol in the database, or None if no data."""
    with get_session() as session:
        row = session.execute(
            select(
                func.min(col(StockOhlcv.date)),
                func.max(col(StockOhlcv.date)),
            ).where(col(StockOhlcv.symbol) == symbol)
        ).one()

    if row[0] is None:
        return None
    return row[0], row[1]


def ensure_stock_data(
    symbol: str, start_date: datetime | date, end_date: datetime | date
) -> pl.DataFrame:
    """
    Smart-caching stock data loader.
    Checks DB for existing data, fetches only missing date ranges,
    tries jugaad-data (NSE) first, yfinance as fallback.
    """
    start = _to_date(start_date)
    end = _to_date(end_date)

    MIN_FETCH_DAYS = 5  # don't fetch ranges shorter than 5 days (avoids holiday gaps)

    existing = _get_date_range(symbol)

    if existing is None:
        _fetch_and_save(symbol, start, end)
    else:
        db_min, db_max = existing
        if start < db_min and (db_min - start).days >= MIN_FETCH_DAYS:
            _fetch_and_save(symbol, start, db_min)
        if end > db_max and (end - db_max).days >= MIN_FETCH_DAYS:
            _fetch_and_save(symbol, db_max, end)

    return _load_ohlcv(symbol, start, end)


def load_stock_registry() -> pl.DataFrame:
    with get_session() as session:
        rows = session.execute(select(StockRegistry)).scalars().all()
    if not rows:
        return pl.DataFrame(
            schema={
                "stockName": pl.Utf8,
                "symbol": pl.Utf8,
                "exchange": pl.Utf8,
                "quoteType": pl.Utf8,
            }
        )
    return pl.DataFrame(
        {
            "stockName": [r.stock_name for r in rows],
            "symbol": [r.symbol for r in rows],
            "exchange": [r.exchange for r in rows],
            "quoteType": [r.quote_type for r in rows],
        }
    )
