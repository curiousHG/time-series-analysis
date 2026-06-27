"""Stock fundamentals store — scrape screener.in company pages, compute a screener-shaped
snapshot, persist DB-first (fetch only missing/stale symbols).

Layout mirrors the MF metrics pattern: raw quarterly rows in `stock_quarterly`, a derived
per-symbol snapshot in `stock_metrics`, and a `fundamentals_status`/`fundamentals_as_of`
marker on `stock_registry`.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
import time

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from core.models import StockMetrics, StockQuarterly, StockRegistry
from data.fetchers.screener_in import fetch_company

logger = logging.getLogger("data.store.stock_fundamentals")

_MONTHS = {
    m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)
}
# screener.in quarterly row label → StockQuarterly field.
_QMAP = {
    "Sales": "sales",
    "Expenses": "expenses",
    "Operating Profit": "operating_profit",
    "OPM %": "opm_pct",
    "Other Income": "other_income",
    "Interest": "interest",
    "Depreciation": "depreciation",
    "Profit before tax": "profit_before_tax",
    "Tax %": "tax_pct",
    "Net Profit": "net_profit",
    "EPS in Rs": "eps",
}
# screener.in top-ratio label → StockMetrics field.
_TMAP = {
    "Market Cap": "market_cap",
    "Current Price": "current_price",
    "Stock P/E": "stock_pe",
    "Book Value": "book_value",
    "Dividend Yield": "dividend_yield",
    "ROCE": "roce",
    "ROE": "roe",
    "Face Value": "face_value",
}


def _period_to_date(label: str) -> dt.date | None:
    """'Mar 2026' → 2026-03-31 (quarter-end)."""
    parts = label.split()
    if len(parts) != 2:
        return None
    month = _MONTHS.get(parts[0][:3].title())
    if not month or not parts[1].isdigit():
        return None
    year = int(parts[1])
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _growth(curr: float | None, prev: float | None) -> float | None:
    """Percentage change curr-vs-prev; None if prev is missing/zero."""
    if curr is None or prev in (None, 0):
        return None
    return (curr - prev) / abs(prev) * 100.0


def _ordered_periods(quarters: dict) -> list[str]:
    """Chronological period labels (screener renders oldest→newest left→right)."""
    return list(quarters.get("Sales", {}).keys())


def _quarterly_rows(symbol: str, quarters: dict) -> list[dict]:
    rows = []
    for label in _ordered_periods(quarters):
        period_end = _period_to_date(label)
        if period_end is None:
            continue
        row = {"symbol": symbol, "period_end": period_end, "period_label": label}
        for sc_label, field in _QMAP.items():
            row[field] = quarters.get(sc_label, {}).get(label)
        rows.append(row)
    return rows


def _metrics_row(symbol: str, data: dict) -> dict:
    quarters = data.get("quarters", {})
    periods = _ordered_periods(quarters)
    sales = quarters.get("Sales", {})
    net = quarters.get("Net Profit", {})

    def at(series: dict, idx: int) -> float | None:
        return series.get(periods[idx]) if periods and -len(periods) <= idx < len(periods) else None

    row: dict = {"symbol": symbol}
    for sc_label, field in _TMAP.items():
        row[field] = data.get("top_ratios", {}).get(sc_label)

    row["last_quarter_label"] = periods[-1] if periods else None
    row["sales_latest_q"] = at(sales, -1)
    row["net_profit_latest_q"] = at(net, -1)
    row["opm_latest_q"] = at(quarters.get("OPM %", {}), -1)
    row["eps_latest_q"] = at(quarters.get("EPS in Rs", {}), -1)
    row["yoy_sales_growth"] = _growth(at(sales, -1), at(sales, -5))
    row["yoy_profit_growth"] = _growth(at(net, -1), at(net, -5))
    row["qoq_sales_growth"] = _growth(at(sales, -1), at(sales, -2))
    row["qoq_profit_growth"] = _growth(at(net, -1), at(net, -2))

    promoters = data.get("shareholding", {}).get("Promoters", {})
    sh_periods = list(promoters.keys())
    if sh_periods:
        row["promoter_holding"] = promoters[sh_periods[-1]]
        if len(sh_periods) >= 5:
            row["promoter_holding_change_1y"] = _growth_diff(promoters[sh_periods[-1]], promoters[sh_periods[-5]])
    row["computed_at"] = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    return row


def _growth_diff(curr: float | None, prev: float | None) -> float | None:
    """Absolute (percentage-point) change — promoter holding is already a percentage."""
    if curr is None or prev is None:
        return None
    return curr - prev


def save_company_fundamentals(symbol: str, data: dict | None) -> bool:
    """Persist a scraped company dict (None → mark unavailable). Returns True if data stored."""
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with get_session() as session:
        if data is None:
            _mark_status(session, symbol, "unavailable", now)
            session.commit()
            return False

        q_rows = _quarterly_rows(symbol, data.get("quarters", {}))
        if q_rows:
            qstmt = pg_insert(StockQuarterly).values(q_rows)
            qstmt = qstmt.on_conflict_do_update(
                index_elements=["symbol", "period_end"],
                set_={c: qstmt.excluded[c] for c in q_rows[0] if c not in ("symbol", "period_end")},
            )
            session.exec(qstmt)

        m_row = _metrics_row(symbol, data)
        mstmt = pg_insert(StockMetrics).values(m_row)
        mstmt = mstmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={c: mstmt.excluded[c] for c in m_row if c != "symbol"},
        )
        session.exec(mstmt)
        _mark_status(session, symbol, "available", now, name=data.get("name"))
        session.commit()
    return True


def _mark_status(session, symbol: str, status: str, when: dt.datetime, *, name: str | None = None) -> None:
    row = session.get(StockRegistry, symbol)
    if row is None:
        row = StockRegistry(symbol=symbol)
        session.add(row)
    row.fundamentals_status = status
    row.fundamentals_as_of = when
    # Backfill the name from screener.in for symbols absent from the NSE master (e.g. LTIM).
    if name and not row.stock_name:
        row.stock_name = name


def ensure_stock_fundamentals(
    symbols: list[str],
    *,
    max_age_days: int = 7,
    request_delay: float = 0.7,
    retries: int = 1,
) -> pl.DataFrame:
    """DB-first: scrape only symbols missing or older than max_age_days; return their metrics.

    Scrapes are throttled (`request_delay` between requests, with `retries` on failure) so a
    bulk run stays polite to screener.in and avoids the rate-limiting that nulls out fields.
    """
    cutoff = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=max_age_days)
    with get_session() as session:
        fresh = set(
            session.exec(
                select(StockRegistry.symbol).where(
                    col(StockRegistry.symbol).in_(symbols),
                    StockRegistry.fundamentals_status == "available",
                    col(StockRegistry.fundamentals_as_of) >= cutoff,
                )
            ).all()
        )
    todo = [s for s in symbols if s not in fresh]
    for i, sym in enumerate(todo):
        data = fetch_company(sym)
        attempt = 0
        while data is None and attempt < retries:
            time.sleep(2.0)  # back off, then retry a throttled/empty response
            data = fetch_company(sym)
            attempt += 1
        save_company_fundamentals(sym, data)
        if request_delay and i < len(todo) - 1:
            time.sleep(request_delay)
    return load_stock_metrics(symbols)


def load_stock_metrics(symbols: list[str] | None = None) -> pl.DataFrame:
    """Load cached stock metrics (all, or a subset)."""
    with get_session() as session:
        stmt = select(StockMetrics)
        if symbols:
            stmt = stmt.where(col(StockMetrics.symbol).in_(symbols))
        rows = session.exec(stmt).all()
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame([r.model_dump() for r in rows])
