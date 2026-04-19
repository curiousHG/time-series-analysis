"""Tradebook DB operations — import CSVs with deduplication."""

import logging
import polars as pl
from sqlmodel import select, col
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.database import get_session
from core.models import MfTradebook

logger = logging.getLogger("data.store.tradebook")


def import_tradebook_csv(csv_path: str) -> tuple[int, int]:
    """
    Import a Kite/Zerodha tradebook CSV into the database.
    Deduplicates on trade_id.
    Returns (new_count, skipped_count).
    """
    df = pl.read_csv(csv_path, try_parse_dates=True)
    return _import_tradebook_df(df)


def import_tradebook_bytes(file_bytes: bytes) -> tuple[int, int]:
    """
    Import tradebook from uploaded file bytes (Streamlit file_uploader).
    Returns (new_count, skipped_count).
    """
    df = pl.read_csv(file_bytes, try_parse_dates=True)
    return _import_tradebook_df(df)


def _import_tradebook_df(df: pl.DataFrame) -> tuple[int, int]:
    """Upsert a tradebook DataFrame. Returns (new_count, skipped_count)."""
    if df.is_empty():
        return 0, 0

    total = df.height
    new_count = 0

    with get_session() as session:
        for row in df.iter_rows(named=True):
            trade_id = str(row["trade_id"])
            stmt = (
                pg_insert(MfTradebook)
                .values(
                    trade_id=trade_id,
                    symbol=row["symbol"],
                    isin=row["isin"],
                    trade_date=row["trade_date"],
                    exchange=row.get("exchange"),
                    segment=row.get("segment"),
                    series=row.get("series"),
                    trade_type=row["trade_type"],
                    auction=str(row.get("auction", "")),
                    quantity=float(row["quantity"]),
                    price=float(row["price"]),
                    order_id=str(row.get("order_id", "")),
                    order_execution_time=str(row.get("order_execution_time", "")),
                )
                .on_conflict_do_nothing(index_elements=["trade_id"])
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                new_count += 1
        session.commit()

    skipped = total - new_count
    logger.info("Imported tradebook: %d new, %d skipped (duplicates)", new_count, skipped)
    return new_count, skipped


def load_tradebook_from_db() -> pl.DataFrame:
    """Load all tradebook transactions from database as a Polars DataFrame."""
    with get_session() as session:
        rows = session.execute(
            select(MfTradebook).order_by(col(MfTradebook.trade_date))
        ).scalars().all()

    if not rows:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "isin": pl.Utf8,
                "trade_date": pl.Date,
                "trade_type": pl.Utf8,
                "quantity": pl.Float64,
                "price": pl.Float64,
            }
        )

    return pl.DataFrame(
        {
            "symbol": [r.symbol for r in rows],
            "isin": [r.isin for r in rows],
            "trade_date": [r.trade_date for r in rows],
            "trade_type": [r.trade_type for r in rows],
            "quantity": [r.quantity for r in rows],
            "price": [r.price for r in rows],
        }
    )


def get_tradebook_stats() -> dict:
    """Return summary stats for the tradebook."""
    with get_session() as session:
        rows = session.execute(select(MfTradebook)).scalars().all()

    if not rows:
        return {"total_trades": 0, "symbols": 0, "date_range": None}

    dates = [r.trade_date for r in rows]
    symbols = set(r.symbol for r in rows)
    buys = sum(1 for r in rows if r.trade_type.lower() == "buy")
    sells = len(rows) - buys

    return {
        "total_trades": len(rows),
        "buys": buys,
        "sells": sells,
        "symbols": len(symbols),
        "first_date": min(dates),
        "last_date": max(dates),
    }
