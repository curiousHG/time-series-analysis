"""Stock (equity) OHLCV repository — the stock_ohlcv + stock_registry tables. Keyed by the bare NSE
symbol (RELIANCE); index symbols are delegated to index_ohlcv. Sources: the NSE stock bhavcopy (bulk),
jugaad-data (NSE), and yfinance (global tickers)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, select

from core.database import get_session
from core.models import StockOhlcv, StockRegistry
from data.fetchers.stock import fetch_nse_equity_list, fetch_symbol_data, query_stocks
from data.repositories._ohlcv_io import _to_date, _upsert_ohlcv
from data.repositories.index_ohlcv import ensure_index_data, refresh_index_to_today
from data.repositories.ohlcv_watermark import _ensure_ohlcv, _get_stock_wm, _refresh_to_today, _set_stock_wm
from stocks.constants import is_index_symbol, to_bare_symbol

if TYPE_CHECKING:
    from datetime import date, datetime

logger = logging.getLogger(__name__)

_NSE_EXCHANGES = {"NSE", "NSI", "BSE", "BO"}


def sync_nse_universe() -> int:
    """Upsert the NSE equity master into stock_registry. Returns the row count."""
    rows = fetch_nse_equity_list()
    if not rows:
        return 0
    payload = [
        {
            "symbol": r["symbol"],
            "stock_name": r["name"],
            "isin": r["isin"],
            "series": r["series"],
            "exchange": "NSE",
        }
        for r in rows
        if r["symbol"]
    ]
    with get_session() as session:
        stmt = pg_insert(StockRegistry).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={
                "stock_name": stmt.excluded.stock_name,
                "isin": stmt.excluded.isin,
                "series": stmt.excluded.series,
                "exchange": stmt.excluded.exchange,
            },
        )
        session.exec(stmt)
        session.commit()
    logger.info("synced %d NSE symbols into stock_registry", len(payload))
    return len(payload)


def _yf_fetch_symbol(symbol: str) -> str:
    """yfinance symbol to fetch for a stored stock symbol. NSE bare symbols get `.NS`; symbols
    that already carry an exchange qualifier (`.` / `^`) or are registered on a non-NSE exchange
    (global tickers like AAPL) are fetched as-is."""
    if "." in symbol or symbol.startswith("^"):
        return symbol
    with get_session() as session:
        exchange = session.exec(select(StockRegistry.exchange).where(StockRegistry.symbol == symbol)).first()
    if exchange and exchange.upper() not in _NSE_EXCHANGES:
        return symbol  # global (e.g. US) — no .NS suffix
    return f"{symbol}.NS"


def _fetch_and_save_stock(symbol: str, start: date, end: date) -> None:
    """Fetch an equity from yfinance — the single reliable source — and upsert into stock_ohlcv.

    yfinance is split/dividend-adjusted and correctly dated. jugaad-data was dropped: its rows are
    timestamped at IST midnight (18:30 UTC), so `.date()` shifted them a day forward — they landed on
    the wrong date and never overwrote corrupt rows, and it occasionally returned spiky values."""
    data = fetch_symbol_data(_yf_fetch_symbol(symbol), start=start, end=end)
    if data is not None and not data.empty:
        _upsert_ohlcv(StockOhlcv, symbol, pl.from_pandas(data.reset_index()))


def register_stock(symbol: str, *, name: str | None = None, exchange: str | None = None, quote_type: str | None = None) -> None:
    """Upsert a stock_registry row — for stocks added outside the NSE master (e.g. global tickers),
    so `_yf_fetch_symbol` knows to fetch them as-is."""
    with get_session() as session:
        row = session.get(StockRegistry, symbol)
        if row is None:
            row = StockRegistry(symbol=symbol)
            session.add(row)
        if name:
            row.stock_name = name
        if exchange:
            row.exchange = exchange
        if quote_type:
            row.quote_type = quote_type
        session.commit()


def save_bhavcopy_day(day: date, *, only_existing: bool = True) -> int:
    """Fetch the NSE bhavcopy for `day` and bulk-upsert equity OHLCV into stock_ohlcv — one
    download covers every cash-market stock. When only_existing, limits to symbols already in
    stock_ohlcv (extends their series); else stores the whole universe. Returns rows upserted."""
    import pandas as pd  # noqa: PLC0415 — pandas only here; the repo is polars-native

    from data.fetchers.stock import fetch_nse_bhavcopy  # noqa: PLC0415 — defer heavy import off boot

    df = fetch_nse_bhavcopy(day)
    if df is None or df.empty:
        return 0
    if only_existing:
        with get_session() as session:
            existing = set(session.exec(select(col(StockOhlcv.symbol)).distinct()).all())
        df = df[df["Symbol"].isin(existing)]
        if df.empty:
            return 0

    def _num(v: object) -> float | None:
        return float(v) if pd.notna(v) else None

    def _int(v: object) -> int | None:
        return int(v) if pd.notna(v) else None

    rows = [
        {
            "date": r.Date,
            "symbol": r.Symbol,
            "open": _num(r.Open),
            "high": _num(r.High),
            "low": _num(r.Low),
            "close": _num(r.Close),
            "volume": _int(r.Volume),
            "turnover": _num(r.Turnover),
            "num_trades": _int(r.NumTrades),
        }
        for r in df.itertuples(index=False)
    ]
    with get_session() as session:
        stmt = pg_insert(StockOhlcv).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["date", "symbol"],
            set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "turnover", "num_trades")},
        )
        session.exec(stmt)
        session.commit()
    logger.info("bhavcopy %s: upserted %d rows (only_existing=%s)", day, len(rows), only_existing)
    return len(rows)


def ensure_stock_data(symbol: str, start_date: datetime | date, end_date: datetime | date) -> pl.DataFrame:
    """DB-first OHLCV loader. Equities are keyed by the bare NSE symbol (RELIANCE); index
    symbols are delegated to ensure_index_data (index_ohlcv). Fetches only missing gaps."""
    start = _to_date(start_date)
    end = _to_date(end_date)
    if is_index_symbol(symbol):
        return ensure_index_data(symbol, start, end)
    sym = to_bare_symbol(symbol)
    return _ensure_ohlcv(
        sym, start, end, model=StockOhlcv, fetch_fn=_fetch_and_save_stock,
        get_wm=_get_stock_wm, set_wm=_set_stock_wm, flag_unavailable=True,
    )


def refresh_stock_to_today(symbol: str) -> tuple[date, date | None]:
    """Force-fetch the forward gap db_max→today, bypassing MIN_FETCH_DAYS, for callers needing
    guaranteed-fresh data (e.g. the metrics-recompute benchmark). Index symbols are delegated.
    Returns (today, db_max_after); db_max_after is None if nothing fetched. No-op if current."""
    if is_index_symbol(symbol):
        return refresh_index_to_today(symbol)
    sym = to_bare_symbol(symbol)
    return _refresh_to_today(sym, model=StockOhlcv, fetch_fn=_fetch_and_save_stock)


def refetch_stock_full(symbol: str, *, since=None) -> None:
    """Replace an equity's entire stored history with a fresh yfinance pull, ATOMICALLY. Fetch first;
    only if that succeeds, delete + re-insert in a single transaction. This fully cleans a corrupt or
    mixed-scale series (a plain upsert would leave stale rows on dates the new pull happens to skip),
    and it's interrupt-safe: a failed/empty fetch or a kill mid-transaction leaves the old rows intact."""
    import datetime as _dt  # noqa: PLC0415

    import pandas as pd  # noqa: PLC0415 — pandas only here; the repo is polars-native

    bare = to_bare_symbol(symbol)
    data = fetch_symbol_data(_yf_fetch_symbol(bare), start=(since or _dt.date(2000, 1, 1)), end=_dt.date.today())
    if data is None or data.empty:
        return  # never wipe on a failed/empty fetch

    def _num(v: object) -> float | None:
        return float(v) if pd.notna(v) else None

    rows = [
        {
            "date": r.Date.date() if hasattr(r.Date, "date") else r.Date,
            "symbol": bare,
            "open": _num(r.Open),
            "high": _num(r.High),
            "low": _num(r.Low),
            "close": _num(r.Close),
            "volume": int(r.Volume) if pd.notna(r.Volume) else None,
        }
        for r in data.reset_index().itertuples(index=False)
    ]
    with get_session() as session:
        session.exec(delete(StockOhlcv).where(col(StockOhlcv.symbol) == bare))
        session.exec(pg_insert(StockOhlcv).values(rows))
        session.commit()


def list_stock_symbols() -> list[str]:
    """Distinct equity symbols we hold OHLCV for (stock_ohlcv) — the fetched set."""
    with get_session() as session:
        rows = session.exec(select(col(StockOhlcv.symbol)).distinct().order_by(col(StockOhlcv.symbol))).all()
    return list(rows)


def list_registry_symbols() -> list[str]:
    """Every equity symbol in the NSE master (stock_registry) — the full pickable universe, including
    ones we haven't fetched OHLCV for yet. Those are fetched on demand the first time they're opened."""
    with get_session() as session:
        rows = session.exec(select(col(StockRegistry.symbol)).distinct().order_by(col(StockRegistry.symbol))).all()
    return list(rows)


def last_stock_ohlcv_date() -> date | None:
    """Most recent date present in stock_ohlcv (across all symbols), or None if empty."""
    with get_session() as session:
        return session.exec(select(func.max(col(StockOhlcv.date)))).one()


def search_stock_symbols(query: str):
    """Search stock symbols through the repository boundary."""
    return query_stocks(query)


def load_stock_registry() -> pl.DataFrame:
    with get_session() as session:
        rows = session.exec(select(StockRegistry)).all()
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
