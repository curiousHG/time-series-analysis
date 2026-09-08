"""Stock (equity) OHLCV repository — the stock_ohlcv + stock_registry tables. Keyed by the bare NSE
symbol (RELIANCE); index symbols are delegated to index_ohlcv. Sources: yfinance (adjusted per-symbol
history; the single OHLCV source of truth) and the NSE stock bhavcopy (bulk one-day snapshots)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
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
from stocks.constants import NSE_EXCHANGES as _NSE_EXCHANGES
from stocks.constants import is_index_symbol, to_bare_symbol

if TYPE_CHECKING:
    from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


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


def sync_nse_etf_universe() -> int:
    """Tag the NSE-listed ETF universe in stock_registry (quote_type='ETF'), registering any not yet
    known. This is what lets the analysis picker classify a symbol as an ETF vs a plain equity — the
    live NAV/underlying/performance metadata is fetched on demand for the ETF view, not stored here.
    Returns the ETF count."""
    from data.fetchers.stock import fetch_nse_etf_list  # noqa: PLC0415 — defer heavy import off boot

    etfs = fetch_nse_etf_list()
    if not etfs:
        return 0
    payload = [
        {"symbol": e["symbol"], "stock_name": e["name"], "exchange": "NSE", "quote_type": "ETF"}
        for e in etfs
        if e["symbol"]
    ]
    with get_session() as session:
        stmt = pg_insert(StockRegistry).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={"quote_type": stmt.excluded.quote_type, "stock_name": stmt.excluded.stock_name},
        )
        session.exec(stmt)
        session.commit()
    logger.info("tagged %d NSE ETFs in stock_registry", len(payload))
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


def register_stock(
    symbol: str, *, name: str | None = None, exchange: str | None = None, quote_type: str | None = None
) -> None:
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


def refresh_stocks_batch(symbols: list[str], start: date, end_exclusive: date) -> int:
    """Upsert a recent OHLCV window for many equities from ONE batched yfinance download. `symbols`
    are bare keys (RELIANCE, AAPL); each is suffixed per its registry exchange like the per-symbol
    path, so NSE and international stocks refresh through the same call. Returns rows saved."""
    from data.fetchers.stock import fetch_symbols_batch  # noqa: PLC0415 — defer heavy import off boot

    if not symbols:
        return 0
    with get_session() as session:
        rows = session.exec(
            select(StockRegistry.symbol, StockRegistry.exchange).where(col(StockRegistry.symbol).in_(symbols))
        ).all()
    exchange_of = dict(rows)

    def _yf(bare: str) -> str:
        if "." in bare or bare.startswith("^"):
            return bare
        exch = (exchange_of.get(bare) or "NSE").upper()
        return bare if exch not in _NSE_EXCHANGES else f"{bare}.NS"

    # A registry row with NULL exchange means the symbol left the NSE master (delisted/renamed in a
    # demerger — e.g. TATAMOTORS → TMCV/TMPV). Its history is static; skip it instead of 404ing daily.
    live = [b for b in symbols if not (b in exchange_of and exchange_of[b] is None)]
    yf_to_bare = {_yf(b): b for b in live}
    total = 0
    for ticker, df in fetch_symbols_batch(list(yf_to_bare), start, end_exclusive).items():
        bare = yf_to_bare.get(ticker)
        if bare is None:
            continue
        frame = pl.from_pandas(df.reset_index())
        _upsert_ohlcv(StockOhlcv, bare, frame)
        total += frame.height
    return total


def ensure_stock_data(symbol: str, start_date: datetime | date, end_date: datetime | date) -> pl.DataFrame:
    """DB-first OHLCV loader. Equities are keyed by the bare NSE symbol (RELIANCE); index
    symbols are delegated to ensure_index_data (index_ohlcv). Fetches only missing gaps."""
    start = _to_date(start_date)
    end = _to_date(end_date)
    if is_index_symbol(symbol):
        return ensure_index_data(symbol, start, end)
    sym = to_bare_symbol(symbol)
    return _ensure_ohlcv(
        sym,
        start,
        end,
        model=StockOhlcv,
        fetch_fn=_fetch_and_save_stock,
        get_wm=_get_stock_wm,
        set_wm=_set_stock_wm,
        flag_unavailable=True,
    )


def refresh_stock_to_today(symbol: str) -> tuple[date, date | None]:
    """Force-fetch the forward gap db_max→today, bypassing MIN_FETCH_DAYS, for callers needing
    guaranteed-fresh data (e.g. the metrics-recompute benchmark). Index symbols are delegated.
    Returns (today, db_max_after); db_max_after is None if nothing fetched. No-op if current."""
    if is_index_symbol(symbol):
        return refresh_index_to_today(symbol)
    sym = to_bare_symbol(symbol)
    return _refresh_to_today(sym, model=StockOhlcv, fetch_fn=_fetch_and_save_stock)


def _despike(rows: list[dict]) -> list[dict]:
    """Drop one-day glitch rows: a close >5x away from BOTH neighbours' closes. Yahoo's early-2000s
    .NS history has days where values spike (or swap between tickers) and recover the next session —
    e.g. CIPLA 2003-04-14: 49.3 → 3.9 → 49.5. Real splits are level SHIFTS (the new level persists),
    so they never match the both-neighbours pattern. `rows` must be date-sorted with positive closes."""

    def _spiky(a: float, b: float) -> bool:
        return a / b > 5 or b / a > 5

    keep = rows[:1]
    for i in range(1, len(rows) - 1):
        c, p, n = rows[i]["close"], rows[i - 1]["close"], rows[i + 1]["close"]
        if _spiky(c, p) and _spiky(c, n):
            logger.info(
                "despike %s %s: close=%.2f vs neighbours %.2f / %.2f", rows[i]["symbol"], rows[i]["date"], c, p, n
            )
            continue
        keep.append(rows[i])
    if len(rows) > 1:
        keep.append(rows[-1])
    return keep


def refetch_stock_full(symbol: str, *, since=None) -> None:
    """Replace an equity's entire stored history with a fresh yfinance pull, ATOMICALLY. Fetch first;
    only if that succeeds, delete + re-insert in a single transaction. This fully cleans a corrupt or
    mixed-scale series (a plain upsert would leave stale rows on dates the new pull happens to skip),
    and it's interrupt-safe: a failed/empty fetch or a kill mid-transaction leaves the old rows intact.
    Zero-close rows are dropped and one-day glitches despiked before insert."""
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
    rows = _despike([r for r in rows if r["close"] and r["close"] > 0])
    if not rows:
        return
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


def load_registry_catalog() -> pl.DataFrame:
    """symbol · name · quote_type · exchange for the whole registry. The analysis picker reads
    quote_type to tell ETFs from plain equities, and exchange to badge/route international stocks
    (non-NSE exchange → yfinance fundamentals, S&P 500 CAPM benchmark)."""
    with get_session() as session:
        rows = session.exec(
            select(
                StockRegistry.symbol, StockRegistry.stock_name, StockRegistry.quote_type, StockRegistry.exchange
            ).order_by(col(StockRegistry.symbol))
        ).all()
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "name": [r[1] for r in rows],
            "quote_type": [r[2] for r in rows],
            "exchange": [r[3] for r in rows],
        },
        schema={"symbol": pl.Utf8, "name": pl.Utf8, "quote_type": pl.Utf8, "exchange": pl.Utf8},
    )


def nse_trading_days(start: date, end: date) -> list[date]:
    """NSE trading calendar between `start` and `end` inclusive — the dates the index bhavcopy
    published Nifty 50, which is the only source here that reflects holidays and special sessions."""
    from core.models import IndexOhlcv  # noqa: PLC0415 — model import kept local to this helper

    with get_session() as session:
        rows = session.exec(
            select(col(IndexOhlcv.date))
            .where(col(IndexOhlcv.symbol) == "Nifty 50", col(IndexOhlcv.date) >= start, col(IndexOhlcv.date) <= end)
            .order_by(col(IndexOhlcv.date))
        ).all()
    return list(rows)


def stock_gap_ranges(symbols: list[str] | None = None, *, lookback_days: int = 730) -> dict[str, tuple[date, date]]:
    """{symbol: (first_missing, last_missing)} for NSE equities missing trading days *inside*
    their stored history over the lookback — the internal holes a forward-only refresh never
    revisits. A symbol's own first stored date bounds the check, so thin new listings are not
    reported as gaps."""
    today = date.today()
    since = today - timedelta(days=lookback_days)
    # Weekend special sessions (Budget day) are real trading days the bhavcopy records, but
    # yfinance never publishes equity bars for them — a gap that can never be filled.
    calendar = [d for d in nse_trading_days(since, today) if d.weekday() < 5]
    if not calendar:
        return {}
    with get_session() as session:
        live = session.exec(
            select(StockRegistry.symbol).where(
                col(StockRegistry.exchange).is_not(None), col(StockRegistry.exchange).in_(_NSE_EXCHANGES)
            )
        ).all()
        wanted = set(symbols) & set(live) if symbols else set(live)
        rows = session.exec(
            select(col(StockOhlcv.symbol), col(StockOhlcv.date)).where(
                col(StockOhlcv.symbol).in_(wanted), col(StockOhlcv.date) >= since
            )
        ).all()
    have: dict[str, set[date]] = {}
    for sym, d in rows:
        have.setdefault(sym, set()).add(d)
    gaps: dict[str, tuple[date, date]] = {}
    for sym, dates in have.items():
        first, last = min(dates), max(dates)
        missing = [d for d in calendar if first <= d <= last and d not in dates]
        if missing:
            gaps[sym] = (missing[0], missing[-1])
    return gaps


def fill_stock_gaps(symbols: list[str] | None = None, *, chunk: int = 200) -> int:
    """Refetch every internal hole found by `stock_gap_ranges`, batching symbols that share the
    same missing window. Returns rows upserted."""
    gaps = stock_gap_ranges(symbols)
    if not gaps:
        return 0
    by_range: dict[tuple[date, date], list[str]] = {}
    for sym, rng in gaps.items():
        by_range.setdefault(rng, []).append(sym)
    total = 0
    for (first, last), syms in sorted(by_range.items()):
        for i in range(0, len(syms), chunk):
            total += refresh_stocks_batch(syms[i : i + chunk], first, last + timedelta(days=1))
    logger.info("fill_stock_gaps: %d symbol(s) across %d window(s), %d rows", len(gaps), len(by_range), total)
    return total


def last_nse_stock_ohlcv_date() -> date | None:
    """The date up to which the *typical* live NSE equity is stored: the median of per-symbol
    max dates. The plain max is dragged forward by the handful of international tickers that
    keep updating while every NSE symbol sits weeks behind, which is how a forward-only refresh
    window opened a 40-day hole across the whole universe."""
    with get_session() as session:
        rows = session.exec(
            select(func.max(col(StockOhlcv.date)))
            .join(StockRegistry, StockRegistry.symbol == StockOhlcv.symbol)
            .where(col(StockRegistry.exchange).in_(_NSE_EXCHANGES))
            .group_by(col(StockOhlcv.symbol))
        ).all()
    dates = sorted(d for d in rows if d is not None)
    return dates[len(dates) // 2] if dates else None


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
