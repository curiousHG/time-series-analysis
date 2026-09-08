"""Price-derived stock metrics — 1Y return / volatility / CAPM alpha-beta.

Computed from our cached OHLCV (no extra scraping). Stored into stock_metrics alongside the
screener.in fundamentals so the screener can categorise stocks by alpha. The CAPM benchmark is
routed by listing market: NSE stocks vs Nifty 50, international stocks vs the S&P 500 (a US
stock's alpha vs an INR index would mix currencies and market regimes). Mirrors the MF
metrics-compute pattern; reuses services.mf_metrics.compute_alpha_beta.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, select

from core.database import get_session
from core.models import StockMetrics, StockRegistry
from data.repositories.stock import ensure_stock_data
from services.constants import TRADING_DAYS
from services.mf_metrics import compute_alpha_beta
from services.price_adjust import adjust_splits
from stocks.constants import is_nse_exchange

logger = logging.getLogger(__name__)

NIFTY_SYMBOL = "^NSEI"
SP500_SYMBOL = "^GSPC"
_PRICE_FIELDS = ("return_1y", "vol_1y", "beta_1y", "alpha_1y", "r2_1y")


def benchmark_symbol_for_exchange(exchange: str | None) -> str:
    """CAPM benchmark for a stock's listing exchange: Nifty 50 for Indian listings, S&P 500 else."""
    return NIFTY_SYMBOL if is_nse_exchange(exchange) else SP500_SYMBOL


def _daily_returns(symbol: str, *, lookback_days: int = 800) -> pd.Series:
    """Daily close-to-close returns for a symbol from cached/fetched OHLCV."""
    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    df = ensure_stock_data(symbol, start, end)
    if df.is_empty():
        return pd.Series(dtype="float64")
    pdf = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()
    close = adjust_splits(pdf["Close"])  # yfinance history still carries unrecorded splits/bonuses
    return close.pct_change().dropna()


def compute_price_metrics(symbol: str, benchmark_returns: pd.Series) -> dict | None:
    """Return {symbol, return_1y, vol_1y, beta_1y, alpha_1y, r2_1y} (percentages), or None.

    `symbol` is the bare registry key (RELIANCE, AAPL); ensure_stock_data suffixes it per its
    registry exchange. `benchmark_returns` must already be the exchange-appropriate index.
    """
    returns = _daily_returns(symbol)
    if returns.empty:
        return None
    out: dict = {"symbol": symbol}
    last_year = returns.iloc[-TRADING_DAYS:]
    if len(last_year) >= 60:
        out["vol_1y"] = float(last_year.std() * math.sqrt(TRADING_DAYS) * 100)
        cum = float((1.0 + last_year).prod())
        out["return_1y"] = (cum - 1.0) * 100
    ab = compute_alpha_beta(returns, benchmark_returns)
    if ab:
        out["alpha_1y"] = ab["alpha"] * 100  # annualised Jensen alpha → %
        out["beta_1y"] = ab["beta"]
        out["r2_1y"] = ab["r2"]
    return out


def recompute_price_metrics(symbols: list[str], *, max_workers: int = 8) -> int:
    """Compute price metrics for `symbols` in parallel and upsert into stock_metrics. Each symbol
    is benchmarked against its exchange's index (^NSEI or ^GSPC)."""
    with get_session() as session:
        rows_ = session.exec(
            select(StockRegistry.symbol, StockRegistry.exchange).where(col(StockRegistry.symbol).in_(symbols))
        ).all()
    exchange_of = dict(rows_)

    bench_of = {s: benchmark_symbol_for_exchange(exchange_of.get(s)) for s in symbols}
    bench_returns: dict[str, pd.Series] = {}
    for bench in sorted(set(bench_of.values())):
        bench_returns[bench] = _daily_returns(bench)
        if bench_returns[bench].empty:
            logger.warning("no %s returns — alpha/beta will be null for its stocks", bench)

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(compute_price_metrics, s, bench_returns[bench_of[s]]): s for s in symbols}
        for fut in as_completed(futures):
            try:
                row = fut.result()
            except Exception as e:
                logger.warning("price metrics failed for %s: %s", futures[fut], e)
                row = None
            if row and len(row) > 1:
                row["computed_at"] = dt.datetime.now(dt.UTC).replace(tzinfo=None)
                rows.append(row)

    if not rows:
        return 0
    with get_session() as session:
        for row in rows:  # rows have heterogeneous keys (some metrics may be missing) → upsert per row
            stmt = pg_insert(StockMetrics).values(row)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={k: stmt.excluded[k] for k in row if k != "symbol"},
            )
            session.exec(stmt)
        session.commit()
    logger.info("upserted price metrics for %d symbols", len(rows))
    return len(rows)
