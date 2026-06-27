"""Price-derived stock metrics — 1Y return / volatility / CAPM alpha-beta vs Nifty 50.

Computed from our cached OHLCV (no extra scraping). Stored into stock_metrics alongside the
screener.in fundamentals so the screener can categorise stocks by alpha. Mirrors the MF
metrics-compute pattern; reuses services.mf_metrics.compute_alpha_beta.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.database import get_session
from core.models import StockMetrics
from data.repositories.stock import ensure_stock_data
from services.constants import TRADING_DAYS
from services.mf_metrics import compute_alpha_beta

logger = logging.getLogger("services.stock_metrics")

NIFTY_SYMBOL = "^NSEI"
_PRICE_FIELDS = ("return_1y", "vol_1y", "beta_1y", "alpha_1y", "r2_1y")


def _daily_returns(symbol: str, *, lookback_days: int = 800) -> pd.Series:
    """Daily close-to-close returns for a symbol from cached/fetched OHLCV."""
    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    df = ensure_stock_data(symbol, start, end)
    if df.is_empty():
        return pd.Series(dtype="float64")
    pdf = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()
    return pdf["Close"].pct_change().dropna()


def compute_price_metrics(symbol: str, nifty_returns: pd.Series) -> dict | None:
    """Return {symbol, return_1y, vol_1y, beta_1y, alpha_1y, r2_1y} (percentages), or None.

    `symbol` is the bare NSE symbol (e.g. RELIANCE); OHLCV is fetched as `<symbol>.NS`.
    """
    fetch_sym = symbol if symbol.startswith("^") else f"{symbol}.NS"
    returns = _daily_returns(fetch_sym)
    if returns.empty:
        return None
    out: dict = {"symbol": symbol}
    last_year = returns.iloc[-TRADING_DAYS:]
    if len(last_year) >= 60:
        out["vol_1y"] = float(last_year.std() * math.sqrt(TRADING_DAYS) * 100)
        cum = float((1.0 + last_year).prod())
        out["return_1y"] = (cum - 1.0) * 100
    ab = compute_alpha_beta(returns, nifty_returns)
    if ab:
        out["alpha_1y"] = ab["alpha"] * 100  # annualised Jensen alpha → %
        out["beta_1y"] = ab["beta"]
        out["r2_1y"] = ab["r2"]
    return out


def recompute_price_metrics(symbols: list[str], *, max_workers: int = 8) -> int:
    """Compute price metrics for `symbols` in parallel and upsert into stock_metrics."""
    nifty = _daily_returns(NIFTY_SYMBOL)
    if nifty.empty:
        logger.warning("no Nifty 50 returns — alpha/beta will be null")

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(compute_price_metrics, s, nifty): s for s in symbols}
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
