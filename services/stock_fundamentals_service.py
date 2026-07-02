"""Stock fundamentals service — universe percentiles, price momentum, quarterly trend.

Percentiles are ranked against the *screened universe* (all stocks with cached
screener.in metrics) — the data holds no sector/industry classification, so honest
labelling matters: "P75 vs screened universe", not "vs sector".
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from data.repositories.stock import ensure_stock_data
from data.repositories.stock_fundamentals import load_quarterly
from stocks.constants import to_yf_symbol

# Metric columns ranked by fundamentals_percentiles; True = higher is better.
PERCENTILE_COLS: dict[str, bool] = {
    "stock_pe": False,  # cheaper is "better" for a value lens
    "roe": True,
    "roce": True,
    "yoy_profit_growth": True,
    "yoy_sales_growth": True,
    "dividend_yield": True,
}


def fundamentals_percentiles(df: pl.DataFrame, cols: dict[str, bool] | None = None) -> pl.DataFrame:
    """Percentile rank (0-100) of each metric within the screened universe.

    :param df: stock screener frame (needs `symbol` + the metric columns).
    :param cols: metric → higher-is-better; defaults to PERCENTILE_COLS.
    :return: `symbol` + one `<col>_pctile` column per metric (null where the metric is null).
    """
    cols = cols if cols is not None else PERCENTILE_COLS
    if df.is_empty():
        return pl.DataFrame(schema={"symbol": pl.Utf8})
    exprs = []
    for col_name, higher_is_better in cols.items():
        if col_name not in df.columns:
            continue
        rank = pl.col(col_name).rank(method="average", descending=not higher_is_better)
        n = pl.col(col_name).is_not_null().sum()
        pctile = pl.when(pl.col(col_name).is_not_null() & (n > 1)).then((rank - 1) / (n - 1) * 100).otherwise(None)
        exprs.append(pctile.alias(f"{col_name}_pctile"))
    return df.select("symbol", *exprs)


def momentum_stats(symbol: str) -> dict | None:
    """Price momentum for one stock (yfinance or bare NSE form) from stock_ohlcv.

    Returns 52-week high/low/position, distance from high, trailing 1M/3M/6M/1Y returns,
    and price-vs-50/200-DMA positioning. None when no price history exists.
    """
    end = datetime.now()
    start = end - timedelta(days=500)
    try:
        df = ensure_stock_data(to_yf_symbol(symbol), start, end)
    except Exception:
        return None
    if df.is_empty():
        return None

    close = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()["Close"].astype(float)
    year = close.iloc[-252:]
    last = float(close.iloc[-1])
    hi52, lo52 = float(year.max()), float(year.min())

    def _trailing(rows: int) -> float | None:
        if len(close) < rows + 1:
            return None
        start_px = float(close.iloc[-(rows + 1)])
        return last / start_px - 1 if start_px > 0 else None

    dma_50 = float(close.iloc[-50:].mean()) if len(close) >= 50 else None
    dma_200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else None
    return {
        "last_close": last,
        "as_of": close.index[-1],
        "high_52w": hi52,
        "low_52w": lo52,
        "pos_52w": (last - lo52) / (hi52 - lo52) if hi52 > lo52 else None,
        "dist_from_high": last / hi52 - 1 if hi52 > 0 else None,
        "ret_1m": _trailing(21),
        "ret_3m": _trailing(63),
        "ret_6m": _trailing(126),
        "ret_1y": _trailing(252),
        "vs_dma_50": last / dma_50 - 1 if dma_50 else None,
        "vs_dma_200": last / dma_200 - 1 if dma_200 else None,
    }


def quarterly_trend(symbol: str) -> pl.DataFrame:
    """Quarterly sales / net profit / OPM% for one bare NSE symbol, oldest → newest."""
    q = load_quarterly(symbol)
    if q.is_empty():
        return q
    return q.select(["period_end", "period_label", "sales", "net_profit", "opm_pct", "eps"])
