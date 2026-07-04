import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import polars as pl
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from core.models import IndexOhlcv, IndexRegistry, StockOhlcv, StockRegistry
from core.notifications import push_notice
from data.constants import EMPTY_OHLCV, MIN_FETCH_DAYS
from data.fetchers.stock import (
    fetch_nse_equity_list,
    fetch_nse_index,
    fetch_symbol_data,
    fetch_symbol_data_jugaad,
    query_stocks,
)
from stocks.constants import is_index_symbol, to_bare_symbol

logger = logging.getLogger(__name__)

# Negative cache for symbols that returned zero rows. Without this, a symbol yfinance can
# never serve (e.g. the NIFTY_MIDCAP_150.NS index benchmark) keeps `_get_date_range`
# returning None, so ensure_stock_data re-runs a slow doomed fetch on every call/rerun —
# which hangs pages like MF Analysis. In-process, short TTL so transient outages still retry.
_EMPTY_FETCH_TTL = timedelta(hours=1)
_empty_fetch_cache: dict[str, datetime] = {}


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


def _to_date(d: datetime | date) -> date:
    return d.date() if isinstance(d, datetime) else d


_NSE_EXCHANGES = {"NSE", "NSI", "BSE", "BO"}


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
    """Fetch an equity and upsert into stock_ohlcv keyed by `symbol`. NSE symbols try jugaad-data
    first then yfinance; global symbols go straight to yfinance (fetched as-is)."""
    yf_sym = _yf_fetch_symbol(symbol)
    if yf_sym.endswith(".NS"):
        data = fetch_symbol_data_jugaad(symbol, start, end)  # NSE only; strips .NS internally
        if data is not None and not data.empty:
            _upsert_ohlcv(StockOhlcv, symbol, pl.from_pandas(data.reset_index()))
            return
    data = fetch_symbol_data(yf_sym, start=start, end=end)
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

    rows = [
        {
            "date": r.Date,
            "symbol": r.Symbol,
            "open": _num(r.Open),
            "high": _num(r.High),
            "low": _num(r.Low),
            "close": _num(r.Close),
            "volume": int(r.Volume) if pd.notna(r.Volume) else None,
        }
        for r in df.itertuples(index=False)
    ]
    with get_session() as session:
        stmt = pg_insert(StockOhlcv).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["date", "symbol"],
            set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume")},
        )
        session.exec(stmt)
        session.commit()
    logger.info("bhavcopy %s: upserted %d rows (only_existing=%s)", day, len(rows), only_existing)
    return len(rows)


def _fetch_and_save_index(symbol: str, start: date, end: date) -> None:
    """Fetch an index — niftyindices.com for "NIFTY …" space-form (yfinance lacks Smallcap 250 /
    Midcap 150), yfinance otherwise — and upsert into index_ohlcv."""
    if symbol.startswith("NIFTY "):
        data = fetch_nse_index(symbol, start, end)
    else:
        data = fetch_symbol_data(symbol, start=start, end=end)
    if data is not None and not data.empty:
        _upsert_ohlcv(IndexOhlcv, symbol, pl.from_pandas(data.reset_index()))


def _upsert_ohlcv(model: type, symbol: str, df: pl.DataFrame) -> None:
    """Upsert OHLCV rows into `model`'s table (StockOhlcv or IndexOhlcv)."""
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


# ---- Availability watermark (status / earliest floor / empty-streak) ----------------------
#
# Stored on stock_registry (equities, bare key) and index_registry (indices). The `earliest`
# floor stops ensure_* from re-attempting impossible pre-inception history; the empty_streak
# flips a stock to `unavailable` after 2 consecutive empty fetches so it's skipped + removed.

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


def ensure_index_data(symbol: str, start_date: datetime | date, end_date: datetime | date) -> pl.DataFrame:
    """DB-first OHLCV loader for market indices (index_ohlcv)."""
    start = _to_date(start_date)
    end = _to_date(end_date)
    return _ensure_ohlcv(
        symbol, start, end, model=IndexOhlcv, fetch_fn=_fetch_and_save_index,
        get_wm=_get_index_wm, set_wm=_set_index_wm, flag_unavailable=False,
    )


def refresh_stock_to_today(symbol: str) -> tuple[date, date | None]:
    """Force-fetch the forward gap db_max→today, bypassing MIN_FETCH_DAYS, for callers needing
    guaranteed-fresh data (e.g. the metrics-recompute benchmark). Index symbols are delegated.
    Returns (today, db_max_after); db_max_after is None if nothing fetched. No-op if current."""
    if is_index_symbol(symbol):
        return refresh_index_to_today(symbol)
    sym = to_bare_symbol(symbol)
    return _refresh_to_today(sym, model=StockOhlcv, fetch_fn=_fetch_and_save_stock)


def refresh_index_to_today(symbol: str) -> tuple[date, date | None]:
    """refresh_stock_to_today for an index (index_ohlcv)."""
    return _refresh_to_today(symbol, model=IndexOhlcv, fetch_fn=_fetch_and_save_index)


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


def list_index_symbols() -> list[str]:
    """Distinct index symbols we hold OHLC for (index_ohlcv)."""
    with get_session() as session:
        rows = session.exec(select(col(IndexOhlcv.symbol)).distinct().order_by(col(IndexOhlcv.symbol))).all()
    return list(rows)


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
